"""Plan Interpreter.

Translates administrator-written natural language test steps into a structured, executable
plan. Division of responsibility:

* **The LLM classifies** each step into exactly one approved operation, and may propose a
  data source / entity / match field.
* **Deterministic resolvers own every parameter.** The data source resolver
  (``source_resolver``) and the threshold resolver decide the actual read targets and
  limits, validating everything against the capability manifest (R7).

Ambiguity is always a hard error (R4). There is no silent default operation and no silent
default data source.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import settings
from app.models.kri import KRI, KRITestStep
from app.schemas.kri import PlannedStepItem, StructuredPlan
from app.schemas.tools import UNSUPPORTED_RECOGNITION_REASONS
from app.services.data_source_bootstrap import DEBOOKING_ENTITY, ORDER_INTAKE
from app.services.data_source_registry import build_capability_manifest, entity_registry
from app.services.plan_service import LLM_OPERATIONS, OPERATION_TOOL_MAP, PlanService
from app.services.prompt_loader import load_planner_prompt
from app.services.source_resolver import DataSourceResolver, SourceResolutionError

logger = logging.getLogger(__name__)

# Operations whose *semantics* are per-record; the executor expands them over the
# comparison buckets rather than calling them once.
PER_RECORD_OPERATIONS = {"COMPARE_AMOUNTS", "CALCULATE_DIFFERENCE", "APPLY_THRESHOLD", "EVALUATE_THRESHOLD"}

#: Verbs that signal the step *analyses* data rather than merely reading it. A step that only
#: says "extract the debookings" is a read, not an analysis, however many debooking words it
#: contains - otherwise the composite analysis step would be emitted once per mention.
_ANALYSIS_VERBS = re.compile(
    r"\b(compare|assess|report|identif|flag|net|netting|analyse|analyze|evaluat|find|"
    r"investigate|highlight|quantif)\w*",
    re.IGNORECASE,
)

# Keyword rules used when the LLM is unavailable. Deliberately narrow: a step that matches
# nothing raises rather than defaulting to EXTRACT_POPULATION.
_RULES: List[Tuple[str, str]] = [
    (r"\b(debookings?|debit notes?|reversals?|credit notes?|cancellations?)\b.*\b(order intake|oi|quarter|net|order|orders|customer|customers|bookings?)\b"
     r"|\b(order intake|oi|order|orders|bookings?)\b.*\b(debookings?|debit notes?|reversals?|credit notes?|cancellations?)\b"
     r"|\bnet\b.*\b(order intake|oi)\b.*\b(quarter|value)\b", "ANALYZE_DEBOOKINGS"),
    (r"\b(metric|kpi|kri metric|aggregate|rate|exception rate|compile metrics)\b", "CALCULATE_METRICS"),
    (r"\b(evidence|evidence package|reproducib|document the finding|proof)\b", "BUILD_EVIDENCE"),
    (r"\b(explain|explanation|narrative|write.?up|rationale)\b", "GENERATE_EXPLANATION"),
    (r"\b(threshold|tolerance|exceed|breach|flag .{0,20}variance|material variance)\b", "APPLY_THRESHOLD"),
    (r"\b(match|reconcile|join|correspond|paired against|tie .{0,15}to)\b", "MATCH_RECORDS"),
    (r"\b(variance|difference|amount mismatch|price difference|compare amount)\b", "COMPARE_AMOUNTS"),
    (r"\b(extract|obtain|pull|retrieve|fetch|download|read|list all|get all|identify all)\b", "EXTRACT_POPULATION"),
]

#: Aliases that mark a step as being about order intake debookings. Used both for keyword
#: classification and to bias the LLM's proposed entity.
_DEBOOKING_HINTS = re.compile(
    r"debooking|debit note|reversal|reverses|reversed|credit note|oi net|net(?:ting)?\s+oi|cancellation|cancellations|cancelled",
    re.IGNORECASE,
)

#: Wording that signals a per-customer, quarter-on-quarter comparison.
_QUARTERLY_HINTS = re.compile(
    r"quarter|quarter[- ]on[- ]quarter|q[1-4]\b|period|same customer", re.IGNORECASE
)

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_PROPOSE_RE = re.compile(
    r"data_source_code\s*[=:]\s*[\"']?([A-Za-z0-9_]+)|entity_code\s*[=:]\s*[\"']?([A-Z_]+)",
)


class InterpretationError(ValueError):
    """One or more test steps could not be turned into a runnable plan (R4)."""

    def __init__(self, message: str, steps: Optional[Sequence[Dict[str, Any]]] = None):
        super().__init__(message)
        self.step_errors: List[Dict[str, Any]] = list(steps or [])

    def to_dict(self) -> Dict[str, Any]:
        return {"message": str(self), "steps": self.step_errors, "errors": [str(self)]}


class PlanInterpreter:
    """Interprets test steps into a ``StructuredPlan``."""

    def __init__(self, llm_provider: Optional[Any] = None, db: Optional[Any] = None):
        self._llm_provider = llm_provider
        self._db = db
        self._notes: List[str] = []
        self._resolver = DataSourceResolver(db_bindings=self._db_bindings())

    def _db_bindings(self) -> Optional[Dict[str, List[str]]]:
        """Authoritative source->entity bindings from the database, when available."""
        if self._db is None:
            return None
        try:
            from app.repositories.kri_repository import KRIRepository

            return KRIRepository(self._db).bindings_by_code()
        except Exception:  # pragma: no cover - fall back to registry defaults
            return None

    # -- public API -----------------------------------------------------------

    def interpret(self, kri: KRI, sample_rows: int = 0) -> StructuredPlan:
        """Interpret a KRI's active test steps into a validated-shaped plan.

        Raises ``InterpretationError`` when any step cannot be resolved unambiguously.
        """
        active_steps = sorted(
            [s for s in (kri.test_steps or []) if s.is_active], key=lambda s: s.step_number
        )
        if not active_steps:
            raise InterpretationError(
                f"KRI '{kri.identifier}' has no active test steps. Define at least one step "
                f"before generating an execution plan."
            )

        manifest = build_capability_manifest(kri, db=self._db, sample_rows=sample_rows)
        if not manifest["data_sources"]:
            assigned = [ds.code for ds in (kri.data_sources or [])]
            if assigned:
                raise InterpretationError(
                    f"None of the data sources assigned to KRI '{kri.identifier}' ({assigned}) "
                    f"expose a queryable entity, so no data can be read. Assign a data source "
                    f"with an entity binding, or add a binding for one of these sources.",
                    steps=[],
                )
            raise InterpretationError(
                f"KRI '{kri.identifier}' has no data sources assigned, so no data can be read. "
                f"Assign at least one queryable data source.",
                steps=[],
            )

        mode = settings.get_interpreter_mode()
        # An explicitly injected provider is always used, so a caller (or a test) can drive
        # classification deterministically regardless of the ambient Azure/mock configuration.
        if self._llm_provider is not None:
            mode = "llm"
        llm_classifications = self._classify_with_llm(kri, active_steps, manifest) if mode != "rules" else {}

        planned: List[PlannedStepItem] = []
        errors: List[Dict[str, Any]] = []
        used_aliases: set = set()
        # entity_code -> dataset alias already bound by a planned extraction, so a later
        # composite step reuses it instead of reading the same population twice.
        extracted: Dict[str, str] = {}

        for step in active_steps:
            text = self._step_text(step)
            try:
                if step.operation and step.parameters:
                    planned.append(self._from_pin(step))
                    continue

                classification = llm_classifications.get(step.id) or {}
                operation = (
                    classification.get("operation")
                    or (step.operation.value if hasattr(step.operation, "value") else step.operation)
                    or self._classify_with_rules(text)
                )
                if operation in LLM_OPERATIONS:
                    planned.append(
                        PlannedStepItem(
                            step_number=step.step_number,
                            operation=operation,
                            tool_name=PlanService.tool_for_operation(operation),
                            parameters={"instruction": step.instruction, **(step.parameters or {})},
                            description=step.title,
                            origin="PINNED" if step.operation else "RULES",
                            rationale=classification.get("rationale"),
                        )
                    )
                    continue

                built = self._build_steps_for_operation(
                    kri=kri,
                    step=step,
                    text=text,
                    operation=operation,
                    classification=classification,
                    used_aliases=used_aliases,
                    next_number=step.step_number,
                    extracted=extracted,
                    planned=planned,
                )

                planned.extend(built)
                for item in built:
                    if item.operation == "EXTRACT_POPULATION" and item.entity_code:
                        extracted[item.entity_code] = item.parameters["dataset_alias"]
            except SourceResolutionError as exc:
                errors.append(
                    {
                        "step_number": step.step_number,
                        "step_id": step.id,
                        "title": step.title,
                        "error": str(exc),
                        "candidates": exc.candidates,
                    }
                )
            except InterpretationError as exc:
                errors.append(
                    {
                        "step_number": step.step_number,
                        "step_id": step.id,
                        "title": step.title,
                        "error": str(exc),
                        "candidates": [],
                    }
                )
            except Exception as exc:  # unexpected: still surface it per-step, never swallow
                logger.exception("Unexpected error interpreting step %s", step.step_number)
                errors.append(
                    {
                        "step_number": step.step_number,
                        "step_id": step.id,
                        "title": step.title,
                        "error": f"Unexpected interpretation failure: {exc}",
                        "candidates": [],
                    }
                )

        if errors:
            raise InterpretationError(
                f"{len(errors)} of {len(active_steps)} test step(s) could not be interpreted into "
                f"a runnable plan. See 'steps' for what to clarify.",
                steps=errors,
            )

        # A composite builder may emit several steps for one user step, so renumber the whole
        # plan contiguously here instead of trusting each builder's arithmetic. Dependency order
        # is the emission order, which is already correct.
        for position, item in enumerate(planned, start=1):
            item.step_number = position

        plan = StructuredPlan(kri_id=kri.id, version=1, steps=planned)
        # Mandatory stages are injected at build time, bound to the resolved population.
        # Resolving the alias first surfaces a "no queryable entity" error here rather than
        # producing an unrunnable plan.
        plan = PlanService.inject_required_stages(plan, population_alias=self._population_alias(kri))

        # Resolve the variance limit once, so the exception rules and the threshold step agree
        # and execution never has to re-derive it. Only required when something evaluates one.
        if self._needs_threshold(planned):
            resolved = self._resolve_threshold(kri, kri.test_steps[0])
            threshold_type, threshold_value, operator, origin = resolved[:4]
            note = resolved[4] if len(resolved) > 4 else None
            plan.threshold = threshold_value
            plan.threshold_origin = origin
            for step in plan.steps:
                if step.operation in ("APPLY_THRESHOLD", "EVALUATE_THRESHOLD"):
                    step.parameters.setdefault("threshold_type", threshold_type)
                    step.parameters.setdefault("operator", operator)
                    step.parameters["threshold_value"] = threshold_value
                    step.parameters["threshold_origin"] = origin
            if note:
                self._notes.append(note)
        plan.exception_rules = PlanService.build_exception_rules()
        plan.data_sources_used = PlanService.summarize_data_sources(plan)
        plan.interpreter_meta = {
            "mode": mode,
            "prompt_version": settings.plan_prompt_version,
            "model": settings.azure_openai_deployment_name if mode != "rules" else None,
            "llm_classified_step_ids": sorted(llm_classifications),
            "notes": list(self._notes),
            "per_step": {
                item.step_number: {
                    "origin": item.origin,
                    "data_source_code": item.data_source_code,
                    "entity_code": item.entity_code,
                    "selector_reason": item.selector_reason,
                    "rationale": item.rationale,
                }
                for item in plan.steps
            },
        }
        return plan

    # -- per-operation builders ----------------------------------------------

    def _build_steps_for_operation(
        self,
        kri: KRI,
        step: KRITestStep,
        text: str,
        operation: str,
        classification: Dict[str, Any],
        used_aliases: set,
        next_number: int,
        extracted: Optional[Dict[str, str]] = None,
        planned: Optional[List[PlannedStepItem]] = None,
    ) -> List[PlannedStepItem]:
        extracted = extracted if extracted is not None else {}
        planned = planned if planned is not None else []
        if operation in ("EXTRACT_POPULATION", "FETCH_DATA"):
            return self._build_extract_steps(
                kri, step, text, classification, used_aliases, next_number
            )
        if operation == "ANALYZE_DEBOOKINGS":
            return self._build_debooking_analysis_steps(
                kri, step, text, classification, next_number, extracted
            )
        if operation in ("MATCH_RECORDS", "COMPARE_RECORDS"):
            return [self._build_match_step(kri, step, text, classification, next_number)]
        if operation in ("APPLY_THRESHOLD", "EVALUATE_THRESHOLD"):
            # If the plan already contains a self-comparing stage (e.g. ANALYZE_DEBOOKINGS),
            # no separate compare_records step exists and therefore no "comparison" alias is
            # ever populated at run time.  Emitting a per-record APPLY_THRESHOLD step in this
            # situation would always crash the executor.
            #
            # Instead, fold the resolved percentage limit back into the self-comparing step
            # (updating customer_quarter_ratio_threshold and the BOOKING_QUALITY rule) so the
            # analysis tool enforces exactly what the user wrote.
            #
            # For two-population plans (Order vs PO, three-way match, etc.) the regular
            # per-record path is still used because a comparison alias will be present.
            debooking_step = next(
                (s for s in planned if s.operation == "ANALYZE_DEBOOKINGS"), None
            )
            if debooking_step is not None or _DEBOOKING_HINTS.search(text):
                ratio_value = self._ratio_threshold_from_text(text)
                # Also scan all active steps when the individual step text is vague
                # (e.g. "apply threshold" with no explicit percentage).
                if ratio_value == 0.15:
                    for candidate_step in (kri.test_steps or []):
                        if candidate_step.is_active:
                            pct_match = _PERCENT_RE.search(self._step_text(candidate_step))
                            if pct_match:
                                ratio_value = round(float(pct_match.group(1)) / 100.0, 4)
                                break
                if debooking_step is not None:
                    debooking_step.parameters["customer_quarter_ratio_threshold"] = ratio_value
                    for rule in debooking_step.parameters.get("recognition_rules", []):
                        if rule.get("code") == "BOOKING_QUALITY":
                            rule["threshold"] = ratio_value
                    self._notes.append(
                        f"APPLY_THRESHOLD step (step {step.step_number}) folded into "
                        f"ANALYZE_DEBOOKINGS: customer_quarter_ratio_threshold set to "
                        f"{ratio_value:.2%}."
                    )
                # Return nothing — the threshold is already enforced by the analysis tool.
                return []
            # Two-population plan (Order vs PO, three-way match, etc.): use standard
            # per-record expansion — a comparison alias will be present at run time.
            return [
                PlannedStepItem(
                    step_number=next_number,
                    operation=operation,
                    tool_name=PlanService.tool_for_operation(operation),
                    parameters=self._per_record_parameters(
                        kri, step, text, operation, classification, next_number
                    ),
                    description=step.title,
                    origin="PINNED" if step.operation else classification.get("origin", "RULES"),
                    rationale=classification.get("rationale"),
                )
            ]
        if operation in PER_RECORD_OPERATIONS:
            return [
                PlannedStepItem(
                    step_number=next_number,
                    operation=operation,
                    tool_name=PlanService.tool_for_operation(operation),
                    parameters=self._per_record_parameters(
                        kri, step, text, operation, classification, next_number
                    ),
                    description=step.title,
                    origin="PINNED" if step.operation else classification.get("origin", "RULES"),
                    rationale=classification.get("rationale"),
                )
            ]
        if operation == "CALCULATE_METRICS":
            return [
                PlannedStepItem(
                    step_number=next_number,
                    operation=operation,
                    tool_name=PlanService.tool_for_operation(operation),
                    parameters={
                        "population_alias": self._population_alias(kri),
                        **PlanService.default_parameters_for(operation),
                    },
                    description=step.title,
                    origin="PINNED" if step.operation else classification.get("origin", "RULES"),
                    rationale=classification.get("rationale"),
                )
            ]
        if operation in ("BUILD_EVIDENCE", "GENERATE_EXPLANATION"):
            return [
                PlannedStepItem(
                    step_number=next_number,
                    operation=operation,
                    tool_name=PlanService.tool_for_operation(operation),
                    parameters=PlanService.default_parameters_for(operation)
                    or ({"exception_type": "AUTO"} if operation == "GENERATE_EXPLANATION" else {}),
                    description=step.title,
                    origin="PINNED" if step.operation else classification.get("origin", "RULES"),
                    rationale=classification.get("rationale"),
                )
            ]
        raise InterpretationError(
            f"Step {step.step_number} was classified as unsupported operation '{operation}'."
        )

    def _build_extract_steps(
        self,
        kri: KRI,
        step: KRITestStep,
        text: str,
        classification: Dict[str, Any],
        used_aliases: set,
        next_number: int,
    ) -> List[PlannedStepItem]:
        resolution = self._resolve_source(kri, step, text, classification)
        # Explicit "all populations" wording pulls one extract step per reachable entity.
        wants_all = bool(re.search(r"\b(all|every|each|both)\b.{0,30}\b(population|source|record)", text))
        if wants_all and not step.data_source_id and not step.entity_code:
            entities = self._reachable_entities(kri)
            if len(entities) > 1:
                resolutions = []
                for entity_code in entities:
                    resolutions.append(
                        self._resolver.resolve(
                            kri,
                            f"{text} {entity_code.replace('_', ' ')}",
                            pinned_entity_code=entity_code,
                        )
                    )
                return [
                    self._extract_item(step, res, index, len(resolutions))
                    for index, res in enumerate(resolutions)
                ]

        return [self._extract_item(step, resolution, 0, 1)]

    def _extract_item(
        self, step: KRITestStep, resolution: Any, index: int, total: int
    ) -> PlannedStepItem:
        alias = f"pop_{resolution.entity_code.lower()}"
        if total > 1:
            alias = f"{alias}_{index + 1}"
        return PlannedStepItem(
            step_number=step.step_number + index,
            operation="EXTRACT_POPULATION",
            tool_name="fetch_financial_data",
            parameters={
                "data_source_code": resolution.data_source_code,
                "entity_code": resolution.entity_code,
                "dataset_alias": alias,
            },
            description=step.title,
            data_source_id=resolution.data_source_id,
            data_source_code=resolution.data_source_code,
            entity_code=resolution.entity_code,
            selector_reason=resolution.selector_reason,
            origin=resolution.origin,
            rationale=f"Read {resolution.entity_code} from {resolution.data_source_code}.",
        )

    def _build_debooking_analysis_steps(
        self,
        kri: KRI,
        step: KRITestStep,
        text: str,
        classification: Dict[str, Any],
        next_number: int,
        extracted: Optional[Dict[str, str]] = None,
    ) -> List[PlannedStepItem]:
        """Build the debooking analysis step, plus any extraction it still needs.

        The analysis compares two populations, so it is the one operation that can own the
        extraction of its own inputs: order intake and order intake debookings. A population an
        earlier step already reads is reused, never fetched twice. Both reads are resolved
        through the same source resolver as any other, so the manifest still governs what can
        be read.
        """
        extracted = extracted if extracted is not None else {}
        if not entity_registry.is_registered(DEBOOKING_ENTITY):
            raise InterpretationError(
                f"Step {step.step_number} asks for an order intake debooking analysis, but "
                f"'{DEBOOKING_ENTITY}' is not a registered entity in this deployment."
            )

        # The bookings population is the order intake read; the debookings read is resolved
        # by mentioning the entity explicitly, because "SAP ECC" alone is ambiguous now that
        # the ERP exposes two entities.
        bookings = self._resolve_source(
            kri, step, f"Extract the population of Order Intake records from {self._source_of(kri, ORDER_INTAKE)}", {}
        )
        debookings = self._resolve_source(
            kri,
            step,
            f"Extract the population of Order Intake debookings from "
            f"{self._source_of(kri, DEBOOKING_ENTITY)}",
            {"entity_code": DEBOOKING_ENTITY},
        )

        bookings_alias = f"pop_{bookings.entity_code.lower()}"
        debookings_alias = f"pop_{debookings.entity_code.lower()}"

        quarterly = bool(_QUARTERLY_HINTS.search(text))
        ratio_threshold = self._ratio_threshold_from_text(text)
        if ratio_threshold == 0.15 and kri and kri.thresholds:
            for th in kri.thresholds:
                if th.threshold_value and 0 < th.threshold_value < 100 and (
                    th.unit == "%" or "PERCENT" in (th.threshold_type or "") or "ratio" in (th.name or "").lower()
                ):
                    ratio_threshold = round(float(th.threshold_value) / 100.0, 4)
                    break
        unsupported_reasons = self._unsupported_reasons_from_text(text)

        rules = [
            {
                "code": "PREMATURE_RECOGNITION",
                "severity": "HIGH",
                "condition": "CROSS_QUARTER",
                "create_exception": True,
            },
            {
                "code": "UNSUPPORTED_RECOGNITION",
                "severity": "CRITICAL",
                "condition": "REASON_CODE_IN",
                "reason_codes": unsupported_reasons,
                "create_exception": True,
            },
            {
                "code": "ORPHAN_DEBOOKING",
                "severity": "HIGH",
                "condition": "NO_MATCHING_BOOKING",
                "create_exception": True,
            },
            {
                "code": "BOOKING_QUALITY",
                "severity": "MEDIUM",
                "condition": "CUSTOMER_QUARTER_RATIO_ABOVE",
                "threshold": ratio_threshold,
                "create_exception": True,
            },
        ]
        if classification.get("recognition_rules"):
            rules = classification["recognition_rules"]

        items: List[PlannedStepItem] = []
        number = next_number
        for resolution, description, rationale in (
            (bookings, "Order intake population (netting base)",
             "Population that the debooking analysis nets against."),
            (debookings, "Order intake debookings (debit notes)",
             "Reversals raised against the order intake population."),
        ):
            if resolution.entity_code in extracted:
                continue
            alias = f"pop_{resolution.entity_code.lower()}"
            extracted[resolution.entity_code] = alias
            items.append(
                PlannedStepItem(
                    step_number=number,
                    operation="EXTRACT_POPULATION",
                    tool_name="fetch_financial_data",
                    parameters={
                        "data_source_code": resolution.data_source_code,
                        "entity_code": resolution.entity_code,
                        "dataset_alias": alias,
                    },
                    description=description,
                    data_source_id=resolution.data_source_id,
                    data_source_code=resolution.data_source_code,
                    entity_code=resolution.entity_code,
                    selector_reason=resolution.selector_reason,
                    origin=resolution.origin,
                    rationale=rationale,
                )
            )
            number += 1

        items.append(
            PlannedStepItem(
                step_number=number,
                operation="ANALYZE_DEBOOKINGS",
                tool_name="analyze_oi_debookings",
                parameters={
                    "bookings_alias": bookings_alias,
                    "debookings_alias": debookings_alias,
                    "customer_quarter_ratio_threshold": ratio_threshold,
                    "quarterly": quarterly,
                    "recognition_rules": rules,
                },
                description="Value of order intake, debookings for the same customer quarter on quarter",
                origin=classification.get("origin", "RULES") if not step.operation else "PINNED",
                rationale=(
                    f"Nets order intake against debookings per customer per quarter; flags "
                    f"cross-quarter reversals (premature recognition), unsupported-recognition "
                    f"reason codes, orphans, and same-quarter debooking concentration above "
                    f"{ratio_threshold:.1%}."
                ),
            )
        )
        return items

    def _source_of(self, kri: KRI, entity_code: str) -> str:
        """The assigned data source code that exposes an entity, for mention-scan wording."""
        for code, entities in (self._resolver.db_bindings or {}).items():
            if entity_code in entities and code in {ds.code.upper() for ds in kri.data_sources or []}:
                return code
        return entity_code

    @staticmethod
    def _ratio_threshold_from_text(text: str) -> float:
        """Read an explicit same-quarter debooking or cancellation ratio out of the step text, else default."""
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+[^.]*?)?(?:debooking|debit|reversal|cancellation|cancellations|cancelled|cancel)",
            text,
            re.IGNORECASE,
        )
        if match:
            value = float(match.group(1))
            if 0 < value < 100:
                return round(value / 100.0, 4)

        match2 = re.search(
            r"(?:over|exceeding|above|more\s+than|greater\s+than)?\s*(\d+(?:\.\d+)?)\s*%\s*(?:order\s+)?(?:cancellation|cancellations|cancelled|cancel)",
            text,
            re.IGNORECASE,
        )
        if match2:
            value = float(match2.group(1))
            if 0 < value < 100:
                return round(value / 100.0, 4)

        return 0.15

    @staticmethod
    def _unsupported_reasons_from_text(text: str) -> List[str]:
        """Keep only the unsupported-recognition reason codes the step actually names."""
        named = [
            code
            for code in UNSUPPORTED_RECOGNITION_REASONS
            if re.search(code.replace("_", r"[\s_-]*"), text, re.IGNORECASE)
        ]
        if re.search(r"\b(?:cancellation|cancellations|cancelled|cancel)\b", text, re.IGNORECASE):
            if "CUSTOMER_CANCELLED" not in named:
                named.append("CUSTOMER_CANCELLED")
        return named or list(UNSUPPORTED_RECOGNITION_REASONS)

    def _build_match_step(
        self,
        kri: KRI,
        step: KRITestStep,
        text: str,
        classification: Dict[str, Any],
        next_number: int,
    ) -> PlannedStepItem:
        population = self._population_alias(kri)
        counterpart = self._counterpart_alias(
            kri, exclude=population, text=text, step_label=f"Step {step.step_number}"
        )
        if counterpart is None:
            raise InterpretationError(
                f"Step {step.step_number} requires matching two populations, but the KRI only "
                f"has one queryable entity assigned: {population}. Assign a second data source "
                f"exposing a counterpart entity.",
            )

        proposed_field = (classification.get("match_field") or "").strip()
        shared = self._shared_match_fields(kri, population, counterpart)
        all_registered = entity_registry.allowed_matching_fields()

        # An explicitly named field wins - but only if both entities actually carry it.
        # Silently substituting a different column would be exactly the guessing R7 forbids.
        named = [f for f in [proposed_field, *_field_mentions(text)] if f and f in all_registered]
        usable = [f for f in named if f in shared]
        unusable = [f for f in named if f not in shared]

        if unusable and not usable:
            raise InterpretationError(
                f"Step {step.step_number} asks to match on '{unusable[0]}', but "
                f"{population} and {counterpart} do not both carry that column. Fields shared by "
                f"both: {shared or 'none'}. Name a shared field, or extract a counterpart entity "
                f"that has it.",
            )

        if usable:
            left_field = right_field = usable[0]
            reason = f"field '{usable[0]}' named or proposed in step text"
        elif len(shared) == 1:
            left_field = right_field = shared[0]
            reason = f"'{shared[0]}' is the only field shared by {population} and {counterpart}"
        elif shared:
            raise InterpretationError(
                f"Step {step.step_number} does not say which field to match on, and "
                f"{population}/{counterpart} share several: {shared}. Name the field in the "
                f"step text (e.g. 'using order_id') or pin it.",
            )
        else:
            raise InterpretationError(
                f"No field is shared by {population} and {counterpart}, so they cannot be "
                f"matched. Choose a different entity pair or register a shared match field.",
            )

        left_entity = self._entity_of(kri, population)
        right_entity = self._entity_of(kri, counterpart)
        match_configuration: Dict[str, Any] = {
            "left_field": left_field,
            "right_field": right_field,
            "left_id_field": entity_registry.get_entity(left_entity).id_column,
            "right_id_field": entity_registry.get_entity(right_entity).id_column,
        }
        fallback = entity_registry.fallback_match_for(left_entity, right_entity)
        if fallback and fallback.left_field != left_field:
            match_configuration["fallback_field"] = fallback.left_field
            match_configuration["fallback_right_field"] = fallback.right_field

        return PlannedStepItem(
            step_number=next_number,
            operation="MATCH_RECORDS",
            tool_name="compare_records",
            parameters={
                "left_alias": population,
                "right_alias": counterpart,
                "comparison_alias": "comparison",
                "match_configuration": match_configuration,
                "match_field_reason": reason,
            },
            description=step.title,
            origin="PINNED" if step.operation else classification.get("origin", "RULES"),
            rationale=reason,
        )

    def _per_record_parameters(
        self,
        kri: KRI,
        step: KRITestStep,
        text: str,
        operation: str,
        classification: Dict[str, Any],
        next_number: int,
    ) -> Dict[str, Any]:
        population = self._population_alias(kri)
        counterpart = self._counterpart_alias(
            kri, exclude=population, text=text, step_label=f"Step {step.step_number}"
        )
        comparison_alias = "comparison"

        if operation in ("COMPARE_AMOUNTS", "CALCULATE_DIFFERENCE"):
            if counterpart is None:
                raise InterpretationError(
                    f"Step {step.step_number} compares amounts, which requires two populations "
                    f"with an amount column. Only '{population}' is assigned."
                )
            return {
                "left_value_ref": {"dataset_alias": population, "field": self._amount_field(kri, population)},
                "right_value_ref": {"dataset_alias": counterpart, "field": self._amount_field(kri, counterpart)},
                "comparison_alias": comparison_alias,
                "calculation_type": classification.get("calculation_type")
                or "ABSOLUTE_PERCENTAGE_DIFFERENCE",
                "denominator_policy": classification.get("denominator_policy")
                or "RIGHT_VALUE_ABSOLUTE",
            }

        # APPLY_THRESHOLD / EVALUATE_THRESHOLD. The value is filled in plan-wide by
        # interpret(), so it only needs a provisional placeholder here.
        return {
            "value_ref": {"dataset_alias": population, "field": "difference_percentage"},
            "comparison_alias": comparison_alias,
            "threshold_type": "PERCENTAGE_DIFFERENCE",
            "threshold_value": 0.0,
            "operator": ">",
            "threshold_origin": "PENDING",
        }

    # -- resolvers ------------------------------------------------------------

    def _resolve_source(
        self, kri: KRI, step: KRITestStep, text: str, classification: Dict[str, Any]
    ) -> Any:
        proposed_source = classification.get("data_source_code")
        proposed_entity = classification.get("entity_code")
        return self._resolver.resolve(
            kri=kri,
            step_text=text,
            catalog_sources=self._catalog(),
            pinned_source_id=step.data_source_id,
            pinned_entity_code=step.entity_code,
            proposed_source_code=proposed_source,
            proposed_entity_code=proposed_entity,
        )

    def _catalog(self) -> Optional[List[Any]]:
        """The whole data source catalog, so an unassigned mention is detected (R8)."""
        if self._db is None:
            return None
        try:
            from app.models.kri import DataSource

            return self._db.query(DataSource).all()
        except Exception:  # pragma: no cover - catalog is advisory here
            return None

    def _needs_threshold(self, planned: List[PlannedStepItem]) -> bool:
        """A limit is required when a step tests one, or when a rule evaluates a variance."""
        if any(s.operation in ("APPLY_THRESHOLD", "EVALUATE_THRESHOLD") for s in planned):
            return True
        # VALUE_DIFFERENCE_EXCEEDS_THRESHOLD is the default variance rule, so any plan that
        # compares two populations needs a limit to decide what is an exception.
        return any(
            s.operation in ("MATCH_RECORDS", "COMPARE_RECORDS", "COMPARE_AMOUNTS", "CALCULATE_DIFFERENCE")
            for s in planned
        )

    def _resolve_threshold(
        self, kri: KRI, step: KRITestStep
    ) -> Tuple[str, float, str, str]:
        """Resolve threshold type/value/operator. Never falls back to a hardcoded default.

        A variance test compares a *percentage*, so resolution is ordered by how explicitly
        the limit is stated:

        1. An active ``PERCENTAGE_DIFFERENCE`` threshold on the KRI - configured intent wins.
        2. A percentage stated in a step's text. An ``ABSOLUTE_DIFFERENCE`` threshold
           (e.g. a "minimum amount" of 10,000) is not comparable to a percentage, so it is
           recorded as not applicable rather than silently applied or treated as fatal.
        3. Otherwise a hard error, because nothing states the limit.
        """
        required_type = "PERCENTAGE_DIFFERENCE"
        active = [t for t in (kri.thresholds or []) if t.is_active]
        matching = [t for t in active if (t.threshold_type or "").upper() == required_type]

        if matching:
            configured = matching[0]
            return (
                configured.threshold_type,
                float(configured.threshold_value),
                configured.operator,
                "KRI_THRESHOLD",
            )

        # No percentage threshold configured. Accept an explicit percentage stated in a step.
        for candidate in kri.test_steps or []:
            if not candidate.is_active:
                continue
            match = _PERCENT_RE.search(self._step_text(candidate))
            if match:
                note = None
                if active:
                    wrong = sorted({(t.threshold_type or "UNSET") for t in active})
                    note = (
                        f"Configured threshold type(s) {wrong} do not apply to a percentage "
                        f"variance; the limit was taken from the step text."
                    )
                return required_type, float(match.group(1)), ">", "STEP_TEXT", note

        if active:
            wrong_types = sorted({(t.threshold_type or "UNSET") for t in active})
            raise InterpretationError(
                f"KRI '{kri.identifier}' has active threshold(s) of type {wrong_types}, but a "
                f"variance test evaluates a percentage difference and needs a "
                f"{required_type} threshold. Add a percentage variance limit (for example a 10% "
                f"amount variance threshold) to the KRI configuration."
            )

        raise InterpretationError(
            f"KRI '{kri.identifier}' has no active percentage threshold configured and no step "
            f"states a percentage limit, so a variance test cannot be evaluated. Add a "
            f"{required_type} threshold to the KRI configuration, or state the limit in the step.",
        )

    # -- alias / entity helpers ----------------------------------------------

    def _reachable_entities(self, kri: KRI) -> List[str]:
        seen: List[str] = []
        for ds in kri.data_sources or []:
            for entity_code in entity_registry.bound_entity_codes(ds.code):
                if entity_code not in seen:
                    seen.append(entity_code)
        return seen

    def _population_alias(self, kri: KRI) -> str:
        for entity_code in self._reachable_entities(kri):
            descriptor = entity_registry.get_entity(entity_code)
            if descriptor.population_role == "population":
                return f"pop_{entity_code.lower()}"
        entities = self._reachable_entities(kri)
        if entities:
            return f"pop_{entities[0].lower()}"
        raise InterpretationError(
            f"KRI '{kri.identifier}' has no queryable entity to use as the audit population."
        )

    def _counterpart_alias(
        self,
        kri: KRI,
        exclude: Optional[str] = None,
        text: Optional[str] = None,
        step_label: str = "the match step",
    ) -> Optional[str]:
        """Pick the entity to match the population against.

        Order of assignment must not decide this: a data source that exposes several entities
        (an ERP with both order intake and its debookings) would otherwise make "match Order
        Intake with Purchase Orders" silently compare against the wrong table. So an entity the
        step actually names always wins, and iteration order is only the last resort.
        """
        candidates = [
            entity_code
            for entity_code in self._reachable_entities(kri)
            if f"pop_{entity_code.lower()}" != exclude
        ]
        if not candidates:
            return None
        if text:
            named = self._most_specific_named(candidates, text)
            if len(named) == 1:
                return f"pop_{named[0].lower()}"
            if len(named) > 1:
                raise InterpretationError(
                    f"Step {step_label} names several populations that could be matched against "
                    f"'{exclude}' ({', '.join(named)}). Name the single counterpart to use."
                )
        # Nothing in the text picks the counterpart, so fall back to the assigned data source
        # configuration: a KRI declares which entity is its counterparty. Choosing by
        # registration order instead would compare against whichever table came next - the
        # exact silent-wrong-answer failure this guards against.
        counterparty = [
            code
            for code in candidates
            if entity_registry.get_entity(code).population_role == "counterparty"
        ]
        if len(counterparty) == 1:
            return f"pop_{counterparty[0].lower()}"
        raise InterpretationError(
            f"Step {step_label} does not say which population to match against '{exclude}', and "
            f"{len(candidates)} other entities are assigned to this KRI ({', '.join(candidates)}) "
            f"with no single declared counterparty. Name the counterpart in the step text."
        )

    def _most_specific_named(self, candidates: List[str], text: str) -> List[str]:
        """The candidates whose longest name occurring in ``text`` is the longest overall.

        Entity names nest ("order intake" inside "order intake debookings"), so the most
        specific name present is the one the author wrote, and shorter nested hits are
        absorbed rather than treated as a second, conflicting entity.
        """
        scored: List[Tuple[int, str]] = []
        for code in candidates:
            descriptor = entity_registry.get_entity(code)
            haystack = text.lower()
            lengths = [len(code)] if code.lower() in haystack else []
            lengths += [
                len(alias)
                for alias in descriptor.aliases
                if len(alias) > 3 and alias.lower() in haystack
            ]
            if lengths:
                scored.append((max(lengths), code))
        if not scored:
            return []
        best = max(length for length, _ in scored)
        return [code for length, code in scored if length == best]

    def _entity_of(self, kri: KRI, alias: str) -> str:
        entity_code = alias.replace("pop_", "").upper()
        if entity_code.endswith("_1") or entity_code.endswith("_2"):
            entity_code = entity_code[:-2]
        if not entity_registry.is_registered(entity_code):
            raise InterpretationError(f"Internal error: alias '{alias}' has no registered entity.")
        return entity_code

    def _shared_match_fields(self, kri: KRI, left_alias: str, right_alias: str) -> List[str]:
        return entity_registry.match_fields_between(
            self._entity_of(kri, left_alias), self._entity_of(kri, right_alias)
        )

    def _amount_field(self, kri: KRI, alias: str) -> str:
        return entity_registry.get_entity(self._entity_of(kri, alias)).amount_column

    # -- classification -------------------------------------------------------

    @staticmethod
    def _step_text(step: KRITestStep) -> str:
        return f"{step.title or ''} {step.instruction or ''} {step.expected_output or ''}".strip()

    def _classify_with_rules(self, text: str) -> str:
        lowered = text.lower()
        analyses = bool(_ANALYSIS_VERBS.search(lowered))
        for pattern, operation in _RULES:
            if operation == "ANALYZE_DEBOOKINGS" and not analyses:
                continue
            if re.search(pattern, lowered):
                return operation
        raise InterpretationError(
            f"Step text '{text[:160]}' could not be mapped to an approved operation. Rephrase "
            f"it using an explicit audit verb (extract / match / compare / threshold / metrics / "
            f"evidence), or pin an operation on the step."
        )

    def _classify_with_llm(
        self, kri: KRI, steps: Sequence[KRITestStep], manifest: Dict[str, Any]
    ) -> Dict[int, Dict[str, Any]]:
        """Classify steps via the LLM, constrained to the manifest. Returns step_id -> result."""
        from app.services.llm_provider import get_llm_provider

        try:
            provider = self._llm_provider or get_llm_provider()
        except Exception as exc:
            logger.warning("LLM provider unavailable, falling back to rules: %s", exc)
            return {}

        operations = sorted(OPERATION_TOOL_MAP) + sorted(LLM_OPERATIONS)
        source_codes = [s["code"] for s in manifest["data_sources"]]
        entity_codes = manifest["reachable_entities"]

        payload = {
            "kri": {
                "identifier": kri.identifier,
                "name": kri.name,
                "risk_description": kri.risk_description,
                "end_goal": kri.end_goal,
            },
            "approved_operations": operations,
            "available_data_source_codes": source_codes,
            "available_entity_codes": entity_codes,
            "available_match_fields": sorted(entity_registry.allowed_matching_fields()),
            "capability_manifest": manifest,
            "steps": [
                {
                    "id": step.id,
                    "step_number": step.step_number,
                    "title": step.title,
                    "instruction": step.instruction,
                    "expected_output": step.expected_output,
                }
                for step in steps
            ],
        }

        try:
            if hasattr(provider, "classify_steps"):
                data = provider.classify_steps(payload)
            else:  # pragma: no cover - providers predating the classify_steps contract
                data = {}
        except Exception as exc:
            logger.warning("LLM step classification failed, falling back to rules: %s", exc)
            return {}

        return self._parse_classifications(data, steps, source_codes, entity_codes)

    @staticmethod
    def _parse_classifications(
        data: Dict[str, Any],
        steps: Sequence[KRITestStep],
        source_codes: Sequence[str],
        entity_codes: Sequence[str],
    ) -> Dict[int, Dict[str, Any]]:
        if not isinstance(data, dict):
            return {}

        valid_operations = set(OPERATION_TOOL_MAP) | set(LLM_OPERATIONS)
        source_set = {c.upper() for c in source_codes}
        entity_set = {e.upper() for e in entity_codes}
        step_ids = {step.id for step in steps}

        results: Dict[int, Dict[str, Any]] = {}
        for entry in data.get("classifications", []) or []:
            try:
                step_id = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            if step_id not in step_ids:
                continue

            operation = str(entry.get("operation") or "").strip().upper()
            if operation not in valid_operations:
                continue

            source = entry.get("data_source_code")
            entity = entry.get("entity_code")
            # Manifest fence (R7): discard anything outside the enumeration.
            source = str(source).upper() if source and str(source).upper() in source_set else None
            entity = str(entity).upper() if entity and str(entity).upper() in entity_set else None

            results[step_id] = {
                "operation": operation,
                "data_source_code": source,
                "entity_code": entity,
                "match_field": entry.get("match_field"),
                "calculation_type": entry.get("calculation_type"),
                "denominator_policy": entry.get("denominator_policy"),
                "rationale": entry.get("rationale"),
                "origin": "LLM",
            }
        return results

    # -- pins -----------------------------------------------------------------

    def _from_pin(self, step: KRITestStep) -> PlannedStepItem:
        """A step with both operation and parameters is fully deterministic."""
        operation = step.operation.value if hasattr(step.operation, "value") else step.operation
        return PlannedStepItem(
            step_number=step.step_number,
            operation=operation,
            tool_name=PlanService.tool_for_operation(operation),
            parameters=dict(step.parameters or {}),
            description=step.title,
            data_source_id=step.data_source_id,
            data_source_code=(step.parameters or {}).get("data_source_code"),
            entity_code=(step.parameters or {}).get("entity_code")
            or (step.parameters or {}).get("entity"),
            selector_reason="pinned on step (operation and parameters supplied explicitly)",
            origin="PINNED",
            rationale="Step supplies an explicit operation and parameters.",
        )


def _field_mentions(text: str) -> List[str]:
    """Return registered match fields explicitly named in the step text."""
    lowered = text.lower()
    found = [field for field in entity_registry.allowed_matching_fields() if field.lower() in lowered]
    # Longest first so 'po_reference' is not shadowed by 'order_id' inside a longer name.
    return sorted(found, key=len, reverse=True)
