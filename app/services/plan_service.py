"""Execution Plan Service.

Owns the operation/tool contract, the default declarative exception rules, and the technical
validation applied to an interpreted plan. Plan *interpretation* itself lives in
``app.services.plan_interpreter``; plan *execution* lives in
``app.services.plan_executor``. This module is the shared vocabulary between them.
"""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from app.models.kri import KRI
from app.schemas.kri import (
    CapabilityManifest,
    DataSourceUse,
    ExceptionRule,
    PlanValidationResult,
    PlannedStepItem,
    StructuredPlan,
)
from app.services.data_source_registry import build_capability_manifest, entity_registry

#: The only legal operation -> tool mapping. Mirrored by ``OperationTypeEnum``.
OPERATION_TOOL_MAP: Dict[str, str] = {
    "EXTRACT_POPULATION": "fetch_financial_data",
    "FETCH_DATA": "fetch_financial_data",
    "MATCH_RECORDS": "compare_records",
    "COMPARE_RECORDS": "compare_records",
    "COMPARE_AMOUNTS": "calculate_difference",
    "CALCULATE_DIFFERENCE": "calculate_difference",
    "APPLY_THRESHOLD": "apply_threshold",
    "EVALUATE_THRESHOLD": "apply_threshold",
    "CALCULATE_METRICS": "calculate_kri_metrics",
    "BUILD_EVIDENCE": "build_evidence",
    "GENERATE_EXPLANATION": "generate_explanation",
    "ANALYZE_DEBOOKINGS": "analyze_oi_debookings",
    "ANALYZE_WBS_INTEGRITY": "analyze_wbs_integrity",
}

#: ``PREPARE`` is the single operation the LLM is allowed to reason over at run time.
LLM_OPERATIONS = {"PREPARE"}

#: Stages guaranteed to be present in every plan, injected at build time.
DEFAULT_REQUIRED_STAGES = ("CALCULATE_METRICS", "BUILD_EVIDENCE")

#: Stages a valid plan must contain for the resulting run to be meaningful. The absence of any
#: extraction stage is a hard error; a missing comparison stage is reported the same way.
MANDATORY_OPERATIONS = ("EXTRACT_POPULATION", "MATCH_RECORDS")

#: A plan whose comparison is performed inside a single tool does not need the record-level
#: matching and threshold stages: ``analyze_oi_debookings`` reconciles the debookings to their
#: bookings and applies the recognition rules itself. Demanding MATCH_RECORDS there would force
#: the plan to invent a match the KRI never asked for.
SELF_COMPARING_OPERATIONS = ("ANALYZE_DEBOOKINGS", "ANALYZE_WBS_INTEGRITY")


def mandatory_operations_for(plan: "StructuredPlan") -> Tuple[str, ...]:
    """The operations this particular plan must contain."""
    present = {step.operation for step in plan.steps}
    if present & set(SELF_COMPARING_OPERATIONS):
        return tuple(op for op in MANDATORY_OPERATIONS if op == "EXTRACT_POPULATION")
    return MANDATORY_OPERATIONS

OPERATION_DESCRIPTIONS: Dict[str, str] = {
    "EXTRACT_POPULATION": "Extract a record population from an assigned data source.",
    "FETCH_DATA": "Fetch records for a registered entity from an assigned data source.",
    "MATCH_RECORDS": "Match two extracted populations on a shared field.",
    "COMPARE_RECORDS": "Compare two extracted populations record by record.",
    "COMPARE_AMOUNTS": "Compute the amount difference and percentage variance between two values.",
    "CALCULATE_DIFFERENCE": "Compute the amount difference and percentage variance between two values.",
    "APPLY_THRESHOLD": "Evaluate a computed variance against the KRI configured threshold.",
    "EVALUATE_THRESHOLD": "Evaluate a computed variance against the KRI configured threshold.",
    "CALCULATE_METRICS": "Aggregate population and exception metrics for the KRI.",
    "BUILD_EVIDENCE": "Build reproducible evidence packages for every flagged exception.",
    "GENERATE_EXPLANATION": "Produce the plain-English finding explanation for an exception.",
    "ANALYZE_DEBOOKINGS": "Net order intake against its debookings per customer per quarter and test revenue-recognition quality.",
    "PREPARE": "Free-text reasoning or classification over already-extracted data.",
}

#: Parameters each operation requires at execution time (dates/filters excluded: bound at run time).
OPERATION_REQUIRED_PARAMETERS: Dict[str, List[str]] = {
    "EXTRACT_POPULATION": ["data_source_code", "entity_code", "dataset_alias"],
    "FETCH_DATA": ["data_source_code", "entity_code", "dataset_alias"],
    "MATCH_RECORDS": ["left_alias", "right_alias", "comparison_alias", "match_configuration"],
    "COMPARE_RECORDS": ["left_alias", "right_alias", "comparison_alias", "match_configuration"],
    "COMPARE_AMOUNTS": ["left_value_ref", "right_value_ref"],
    "CALCULATE_DIFFERENCE": ["left_value_ref", "right_value_ref"],
    "APPLY_THRESHOLD": ["value_ref", "threshold_value"],
    "EVALUATE_THRESHOLD": ["value_ref", "threshold_value"],
    "CALCULATE_METRICS": ["population_alias"],
    "BUILD_EVIDENCE": [],
    "GENERATE_EXPLANATION": ["exception_type"],
    "ANALYZE_DEBOOKINGS": ["bookings_alias", "debookings_alias"],
    "PREPARE": ["instruction"],
}

#: Declarative rules turning comparison buckets into candidate exceptions. The executor
#: interprets these instead of hardcoding three branches, so a different match key or
#: threshold genuinely produces a different exception set.
DEFAULT_EXCEPTION_RULES = [
    ExceptionRule(
        rule="MISSING_COUNTERPART",
        source_bucket="missing_pos",
        exception_type="MISSING_PO",
        create_exception=True,
        severity_override="HIGH",
        reason_code="MISSING_PO",
    ),
    ExceptionRule(
        rule="VALUE_DIFFERENCE_EXCEEDS_THRESHOLD",
        source_bucket="matched_pairs",
        exception_type="AMOUNT_MISMATCH",
        create_exception=True,
        only_if_threshold_breached=True,
    ),
    ExceptionRule(
        rule="AMBIGUOUS_COUNTERPART",
        source_bucket="ambiguous_matches",
        exception_type="AMBIGUOUS_MATCH",
        create_exception=True,
        severity_override="HIGH",
        reason_code="AMBIGUOUS_MATCH",
    ),
]


class PlanService:
    """Shared plan vocabulary: operation map, default rules, hashing and validation."""

    # -- hashing / provenance -------------------------------------------------

    @staticmethod
    def hash_payload(payload: Dict[str, Any]) -> str:
        """Stable sha256 over a plan payload, ignoring volatile bookkeeping fields."""
        normalized = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_steps_hash(kri: KRI) -> str:
        """Hash every input that can change the resulting plan (R3).

        Covers the active step content *and* the KRI's assigned data sources, thresholds and
        end goal. Changing a threshold or unassigning a data source invalidates the plan
        exactly as editing step text does.
        """
        parts: List[str] = []
        for step in sorted(
            [s for s in (kri.test_steps or []) if s.is_active],
            key=lambda s: s.step_number,
        ):
            parts.append(step.content_hash or step.compute_content_hash())

        parts.append(
            "ds:" + ",".join(sorted(ds.code.upper() for ds in (kri.data_sources or [])))
        )
        parts.append(
            "th:"
            + ",".join(
                sorted(
                    f"{t.threshold_type}{t.operator}{t.threshold_value}"
                    for t in (kri.thresholds or [])
                    if t.is_active
                )
            )
        )
        parts.append(f"goal:{kri.end_goal or ''}")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    # -- plan assembly helpers ------------------------------------------------

    @staticmethod
    def tool_for_operation(operation: str) -> str:
        if operation in LLM_OPERATIONS:
            return "prepare"
        tool = OPERATION_TOOL_MAP.get(operation)
        if not tool:
            raise ValueError(
                f"Unsupported operation '{operation}'. Approved operations: "
                f"{sorted(OPERATION_TOOL_MAP) + sorted(LLM_OPERATIONS)}"
            )
        return tool

    @staticmethod
    def describe_operations() -> List[Dict[str, Any]]:
        """Machine-readable operation catalog used by the UI dropdowns."""
        descriptors: List[Dict[str, Any]] = []
        for operation in sorted(OPERATION_TOOL_MAP):
            descriptors.append(
                {
                    "operation": operation,
                    "tool_name": OPERATION_TOOL_MAP[operation],
                    "description": OPERATION_DESCRIPTIONS.get(operation, ""),
                    "required_parameters": OPERATION_REQUIRED_PARAMETERS.get(operation, []),
                }
            )
        for operation in sorted(LLM_OPERATIONS):
            descriptors.append(
                {
                    "operation": operation,
                    "tool_name": "prepare",
                    "description": OPERATION_DESCRIPTIONS.get(operation, ""),
                    "required_parameters": OPERATION_REQUIRED_PARAMETERS.get(operation, []),
                }
            )
        return descriptors

    @staticmethod
    def inject_required_stages(plan: StructuredPlan, population_alias: Optional[str] = None) -> StructuredPlan:
        """Ensure mandatory stages exist, appending them in dependency order.

        ``population_alias`` is required for the injected ``CALCULATE_METRICS`` step, because
        metrics must be computed against an explicitly bound population rather than inferred.
        """
        present = {s.operation for s in plan.steps}
        next_number = max([s.step_number for s in plan.steps], default=0)

        for operation in DEFAULT_REQUIRED_STAGES:
            if operation in present:
                continue
            parameters = PlanService.default_parameters_for(operation)
            if operation == "CALCULATE_METRICS" and population_alias:
                parameters["population_alias"] = population_alias
            next_number += 1
            plan.steps.append(
                PlannedStepItem(
                    step_number=next_number,
                    operation=operation,
                    tool_name=PlanService.tool_for_operation(operation),
                    parameters=parameters,
                    description=OPERATION_DESCRIPTIONS[operation],
                    origin="INJECTED",
                    rationale="mandatory stage: every audit run must report metrics and evidence",
                )
            )
            present.add(operation)
        return plan

    @staticmethod
    def default_parameters_for(operation: str) -> Dict[str, Any]:
        if operation == "CALCULATE_METRICS":
            return {
                "calculation_policy": {
                    "mismatch_value_definition": "ORDER_VALUE_OF_QUALIFYING_EXCEPTIONS",
                    "include_missing_po_in_mismatch_value": True,
                }
            }
        return {}

    @staticmethod
    def build_exception_rules() -> List[ExceptionRule]:
        return [ExceptionRule(**rule.model_dump()) for rule in DEFAULT_EXCEPTION_RULES]

    @staticmethod
    def summarize_data_sources(plan: StructuredPlan) -> List[DataSourceUse]:
        """Attribute every extract step to the catalog source it reads from (R5)."""
        uses: List[DataSourceUse] = []
        for step in plan.steps:
            if not step.data_source_code or not step.entity_code:
                continue
            uses.append(
                DataSourceUse(
                    data_source_id=step.data_source_id or 0,
                    data_source_code=step.data_source_code,
                    entity_code=step.entity_code,
                    extract_step_number=step.step_number,
                    selector_reason=step.selector_reason,
                )
            )
        return uses

    # -- validation -----------------------------------------------------------

    @staticmethod
    def validate_for_activation(kri: KRI, plan: StructuredPlan, **kwargs: Any) -> PlanValidationResult:
        """Validation used before activation and before a run: mandatory stages are errors."""
        return PlanService.validate_plan(kri, plan, strict_stages=True, **kwargs)

    @staticmethod
    def validate_plan(
        kri: KRI,
        plan: StructuredPlan,
        manifest: Optional[Dict[str, Any]] = None,
        steps_hash: Optional[str] = None,
        strict_stages: bool = True,
    ) -> PlanValidationResult:
        """Validate a plan technically. No human approval step is involved (R2, R6).

        ``strict_stages`` controls only the mandatory-stage check. It is ``True`` for
        activation and for run-time resolution, where a plan without an extraction or
        comparison stage genuinely cannot execute. It is ``False`` for the intermediate
        saves while a user is still typing steps one at a time, so an incomplete-but-valid
        step set is not rejected mid-edit; the gap is reported as a warning instead.
        """
        errors: List[str] = []
        warnings: List[str] = []

        if manifest is None:
            manifest = build_capability_manifest(kri)

        if not plan.steps:
            return PlanValidationResult(
                is_valid=False,
                kri_id=kri.id,
                errors=["Execution plan contains no steps. Define at least one active test step."],
                steps_hash=steps_hash,
                manifest=CapabilityManifest(**manifest),
            )

        operations = [s.operation for s in plan.steps]
        tool_names = [s.tool_name for s in plan.steps]

        # Mandatory stages
        required = mandatory_operations_for(plan)
        if "EXTRACT_POPULATION" in required and (
            "EXTRACT_POPULATION" not in operations and "fetch_financial_data" not in tool_names
        ):
            message = "Plan is missing a mandatory 'EXTRACT_POPULATION' stage."
            (errors if strict_stages else warnings).append(message)
        if "MATCH_RECORDS" in required and (
            "MATCH_RECORDS" not in operations and "compare_records" not in tool_names
        ):
            message = "Plan is missing a mandatory 'MATCH_RECORDS' stage."
            (errors if strict_stages else warnings).append(message)

        # Step numbering must be unique and contiguous from 1.
        numbers = [s.step_number for s in plan.steps]
        if len(numbers) != len(set(numbers)):
            errors.append("Plan contains duplicate step numbers.")
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            errors.append("Plan step numbers must be contiguous starting at 1.")

        # Every step must reference a registered tool (R1: no LLM dispatch, no fallbacks).
        allowed_tools = set(OPERATION_TOOL_MAP.values()) | {"prepare"}
        for step in plan.steps:
            if step.tool_name not in allowed_tools:
                errors.append(
                    f"Step {step.step_number} specifies unregistered tool '{step.tool_name}'."
                )
            expected_tool = PlanService.tool_for_operation(step.operation)
            if step.tool_name != expected_tool:
                errors.append(
                    f"Step {step.step_number} operation '{step.operation}' must map to tool "
                    f"'{expected_tool}' but declares '{step.tool_name}'."
                )
            missing = [
                key
                for key in OPERATION_REQUIRED_PARAMETERS.get(step.operation, [])
                if step.parameters.get(key) in (None, "")
            ]
            if missing:
                errors.append(
                    f"Step {step.step_number} operation '{step.operation}' is missing required "
                    f"parameter(s): {missing}."
                )

        # Data source fence (R7) - every read target must be in the manifest.
        manifest_sources = {s["code"].upper() for s in manifest.get("data_sources", [])}
        manifest_bindings = {
            (s["code"].upper(), e["entity_code"])
            for s in manifest.get("data_sources", [])
            for e in s.get("entities", [])
        }
        for step in plan.steps:
            if not step.data_source_code:
                continue
            code = step.data_source_code.upper()
            if code not in manifest_sources:
                errors.append(
                    f"Step {step.step_number} reads data source '{code}', which is not an "
                    f"assigned, queryable data source for this KRI. Available: "
                    f"{sorted(manifest_sources) or 'none'}."
                )
            elif (code, step.entity_code) not in manifest_bindings:
                errors.append(
                    f"Step {step.step_number} reads entity '{step.entity_code}' from "
                    f"'{code}', which does not expose it."
                )
            if not step.entity_code:
                errors.append(f"Step {step.step_number} declares a data source but no entity_code.")

        # Match fields must exist on both sides of the comparison.
        for step in plan.steps:
            if step.operation not in ("MATCH_RECORDS", "COMPARE_RECORDS"):
                continue
            match = step.parameters.get("match_configuration") or {}
            left_field = match.get("left_field")
            right_field = match.get("right_field")
            if not left_field or not right_field:
                errors.append(
                    f"Step {step.step_number} match configuration must declare both "
                    f"left_field and right_field."
                )
                continue
            if left_field != right_field:
                errors.append(
                    f"Step {step.step_number} matches '{left_field}' against '{right_field}'; "
                    f"both sides must use the same field name to be a shared column."
                )
            if left_field not in entity_registry.allowed_matching_fields():
                errors.append(
                    f"Step {step.step_number} matches on unregistered field '{left_field}'. "
                    f"Registered match fields: {sorted(entity_registry.allowed_matching_fields())}."
                )

        # Threshold tests require a resolved limit on the step. The interpreter owns that
        # resolution (from the KRI configuration or the step text); a plan reaching here
        # without one is malformed. Checking the parameter rather than the KRI keeps
        # intermediate saves - where no threshold row exists yet - working.
        threshold_steps = [
            s for s in plan.steps if s.operation in ("APPLY_THRESHOLD", "EVALUATE_THRESHOLD")
        ]
        for step in threshold_steps:
            if step.parameters.get("threshold_value") is None:
                errors.append(
                    f"Step {step.step_number} evaluates a threshold but carries no resolved "
                    f"threshold_value. Configure a threshold on the KRI or state the limit in "
                    f"the step."
                )
        if threshold_steps and not (kri.thresholds or []):
            warnings.append(
                "Plan evaluates a threshold but the KRI has no configured threshold; the limit "
                "was taken from the step text."
            )
        # Structural check: a per-record APPLY_THRESHOLD step requires a comparison alias
        # populated by a MATCH_RECORDS / COMPARE_RECORDS stage.  When the plan only contains
        # a self-comparing stage (ANALYZE_DEBOOKINGS), no such alias exists at run time and
        # execution would always crash.  The interpreter should have folded the threshold into
        # the analysis step; reaching validation with both is a plan error.
        if threshold_steps:
            has_comparison_stage = any(
                s.operation in ("MATCH_RECORDS", "COMPARE_RECORDS") for s in plan.steps
            )
            has_self_comparing = any(
                s.operation in SELF_COMPARING_OPERATIONS for s in plan.steps
            )
            if has_self_comparing and not has_comparison_stage:
                errors.append(
                    "Plan contains APPLY_THRESHOLD / EVALUATE_THRESHOLD step(s) but no "
                    "MATCH_RECORDS / COMPARE_RECORDS stage to populate the comparison alias. "
                    "For debooking or other self-comparing analyses the threshold is applied "
                    "inside the analysis tool — remove the standalone threshold step, or add a "
                    "MATCH_RECORDS stage for a two-population comparison."
                )
        # Any note the interpreter recorded (e.g. a configured threshold that did not apply to
        # a percentage comparison) is surfaced rather than buried in interpreter_meta.
        for note in (getattr(plan, "interpreter_meta", None) or {}).get("notes", []) or []:
            warnings.append(note)

        # Advisory checks.
        if not (kri.data_sources or []):
            errors.append("KRI has no data sources assigned, so no data can be read.")
        elif manifest.get("unbound_assigned_sources"):
            warnings.append(
                "KRI has assigned data source(s) with no queryable entity: "
                f"{[u['code'] for u in manifest['unbound_assigned_sources']]}."
            )
        if not (kri.thresholds or []):
            warnings.append("KRI has no configured thresholds.")
        if not any(s.origin == "LLM" for s in plan.steps):
            warnings.append("Plan contains no LLM-derived steps; interpretation ran in rules mode.")

        return PlanValidationResult(
            is_valid=not errors,
            kri_id=kri.id,
            errors=errors,
            warnings=warnings,
            plan=plan,
            steps_hash=steps_hash,
            manifest=CapabilityManifest(**manifest),
        )
