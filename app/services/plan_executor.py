"""Deterministic Plan Executor.

Executes a stored ``StructuredPlan`` step by step. The plan is the execution contract (R1):
tools are dispatched from the plan, never chosen by an LLM. The LLM is consulted only for an
explicit ``PREPARE`` step.

Two kinds of planned step are handled:

* **Tool steps** - dispatched through the tool registry with run-time bindings resolved
  (dates, customer filter, dataset aliases).
* **Per-record operations** (``COMPARE_AMOUNTS``, ``APPLY_THRESHOLD``, ...) - these are
  aggregate descriptions in a plan but per-record computations at run time, so they are
  expanded over each record in the referenced comparison and the tools are called per record.
"""

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from app.tools.registry import registry
from app.schemas.tools import (
    ApplyThresholdInput,
    BuildEvidenceInput,
    CalculateDifferenceInput,
    CalculateKRIMetricsInput,
    GenerateExplanationInput,
)
from app.tools.calculate_difference import calculate_difference_handler
from app.tools.apply_threshold import apply_threshold_handler
from app.tools.generate_explanation import generate_explanation_handler
from app.tools.calculate_kri_metrics import calculate_kri_metrics_handler
from app.tools.build_evidence import build_evidence_handler
from app.services.execution_context import AuditExecutionContext
from app.services.plan_service import PlanService
from app.services.prompt_loader import load_agent_system_prompt

logger = logging.getLogger(__name__)

PER_RECORD_OPERATIONS = {
    "COMPARE_AMOUNTS",
    "CALCULATE_DIFFERENCE",
    "APPLY_THRESHOLD",
    "EVALUATE_THRESHOLD",
}


class PlanExecutionError(RuntimeError):
    """A planned step could not be executed."""


class PlanExecutor:
    """Executes an interpreted plan deterministically."""

    def __init__(self, llm_provider: Optional[Any] = None):
        self._llm_provider = llm_provider

    # -- entry point ----------------------------------------------------------

    def execute(self, plan: Dict[str, Any], context: AuditExecutionContext) -> Dict[str, Any]:
        context.plan = plan
        steps = sorted(plan.get("steps", []), key=lambda s: s.get("step_number", 0))
        if not steps:
            raise PlanExecutionError("Execution plan contains no steps to run.")

        self._log(context, "PLAN_EXECUTION_STARTED", f"Executing {len(steps)} planned step(s).", 0, {
            "plan_version": plan.get("version"),
            "data_sources_used": plan.get("data_sources_used", []),
        })

        for step in steps:
            self._run_step(step, plan, context)

        self._log(context, "PLAN_EXECUTION_COMPLETED", "All planned steps executed.", context.tool_invocations_count, {})

        self._persist_exceptions(context)

        return {
            "status": "COMPLETED",
            "audit_run_reference": context.run_reference,
            "metrics": context.calculated_metrics,
            "total_exceptions": len(context.candidate_exceptions),
            "tool_invocations_count": context.tool_invocations_count,
            "plan_version": plan.get("version"),
        }

    # -- step dispatch --------------------------------------------------------

    def _run_step(self, step: Dict[str, Any], plan: Dict[str, Any], context: AuditExecutionContext) -> None:
        step_number = step.get("step_number")
        operation = step.get("operation")
        tool_name = step.get("tool_name")
        self._log(
            context,
            "PLAN_STEP_STARTED",
            f"Step {step_number}: {operation} -> {tool_name}",
            step_number,
            {
                "operation": operation,
                "tool_name": tool_name,
                "description": step.get("description"),
                "data_source_code": step.get("data_source_code"),
                "entity_code": step.get("entity_code"),
                "selector_reason": step.get("selector_reason"),
            },
        )

        try:
            if operation in PER_RECORD_OPERATIONS:
                self._run_per_record_step(step, context)
            elif operation == "PREPARE":
                self._run_prepare_step(step, context)
            else:
                self._run_tool_step(step, context)
        except Exception as exc:
            self._log(
                context,
                "PLAN_STEP_FAILED",
                f"Step {step_number} ({operation}) failed: {exc}",
                step_number,
                {"error": str(exc)},
            )
            raise PlanExecutionError(f"Planned step {step_number} ({operation}) failed: {exc}") from exc

        self._log(
            context,
            "PLAN_STEP_COMPLETED",
            f"Step {step_number} ({operation}) completed.",
            step_number,
            {"tool_invocations": context.tool_invocations_count},
        )

    def _run_tool_step(self, step: Dict[str, Any], context: AuditExecutionContext) -> None:
        tool_name = step.get("tool_name")
        if not registry.is_registered(tool_name):
            raise PlanExecutionError(
                f"Step {step.get('step_number')} references tool '{tool_name}', which is not in "
                f"the registered allowlist. Refusing to guess a substitute."
            )

        args = self._resolve_arguments(step, context)
        tool = registry.get_tool(tool_name)
        output = self._invoke(tool_name, tool, args, context, step)

        # Post-processing: a comparison feeds the declarative exception rules.
        if tool_name == "compare_records":
            comparison_reference = output.get("comparison_reference")
            alias = (step.get("parameters") or {}).get("comparison_alias")
            if alias and comparison_reference:
                context.bind_comparison(alias, comparison_reference)
            comp_data = context.get_comparison(comparison_reference) if comparison_reference else None
            if comp_data:
                self._apply_exception_rules(comp_data, context, plan=context.plan)

        # The debooking analysis is computational; promoting its findings to exceptions is
        # this layer's job, so the rules that decide what is flagged live in the plan.
        if tool_name == "analyze_oi_debookings":
            self._promote_debooking_findings(output, context, step)

        # Metric step binds the metrics reference for later stages.
        if tool_name == "calculate_kri_metrics":
            context.metric_ref = (step.get("parameters") or {}).get("population_alias")

    def _run_per_record_step(self, step: Dict[str, Any], context: AuditExecutionContext) -> None:
        """Expand an aggregate per-record operation over every record in its comparison."""
        operation = step["operation"]
        params = step.get("parameters") or {}
        alias = params.get("comparison_alias") or "comparison"
        comparison_reference = context.resolve_comparison(alias)
        if comparison_reference is None:
            candidates = list(context.comparison_refs.values())
            if len(candidates) == 1:
                comparison_reference = candidates[0]
        comp_data = context.get_comparison(comparison_reference) if comparison_reference else None
        if not comp_data:
            # Last-resort safety net: if a self-comparing tool (e.g. analyze_oi_debookings)
            # already ran and promoted findings to candidate_exceptions, the threshold was
            # evaluated inside that tool.  A dangling APPLY_THRESHOLD step in the plan is
            # therefore a plan authoring error, but crashing the run here would discard all the
            # work already done.  Log a clear skip message and return so the run completes.
            # The interpreter and validator changes prevent well-formed plans from reaching here.
            self_comparing_tools = {"analyze_oi_debookings"}
            plan_tools = {
                s.get("tool_name") for s in (context.plan or {}).get("steps", [])
            }
            if plan_tools & self_comparing_tools and context.candidate_exceptions:
                self._log(
                    context,
                    "PLAN_STEP_SKIPPED",
                    (
                        f"Step {step.get('step_number')} ({operation}) skipped: comparison alias "
                        f"'{alias}' is not available because the plan uses a self-comparing "
                        f"analysis tool that applies the threshold internally. "
                        f"{len(context.candidate_exceptions)} exception(s) already promoted."
                    ),
                    step.get("step_number"),
                    {"alias": alias, "self_comparing_tools": sorted(plan_tools & self_comparing_tools)},
                )
                return
            raise PlanExecutionError(
                f"Step {step.get('step_number')} ({operation}) references comparison alias "
                f"'{alias}', but no comparison result is available in this run."
            )

        left_amount_field = comp_data.get("left_amount_field") or "order_amount"
        right_amount_field = comp_data.get("right_amount_field") or "po_amount"
        left_id_field = comp_data.get("left_id_field") or "order_id"
        threshold_value = params.get("threshold_value")
        threshold_type = params.get("threshold_type") or "PERCENTAGE_DIFFERENCE"
        operator = params.get("operator") or ">"

        differences: Dict[str, Dict[str, Any]] = {}

        for pair in comp_data.get("matched_pairs", []):
            left = pair.get("left") or {}
            right = pair.get("right") or {}
            left_id = left.get(left_id_field)
            left_value = float(left.get(left_amount_field) or 0.0)
            right_value = float(right.get(right_amount_field) or 0.0)

            if operation in ("COMPARE_AMOUNTS", "CALCULATE_DIFFERENCE"):
                diff_out = calculate_difference_handler(
                    CalculateDifferenceInput(
                        left_value=left_value,
                        right_value=right_value,
                        calculation_type=params.get("calculation_type", "ABSOLUTE_PERCENTAGE_DIFFERENCE"),
                        denominator_policy=params.get("denominator_policy", "RIGHT_VALUE_ABSOLUTE"),
                    ),
                    context,
                )
                self._record(context, "calculate_difference", diff_out.model_dump(mode="json"),
                             {"left_value": left_value, "right_value": right_value})
                differences[str(left_id)] = diff_out.model_dump(mode="json")
            else:
                # APPLY_THRESHOLD reuses the difference already computed for this pair.
                diff = differences.get(str(left_id))
                if diff is None:
                    diff = calculate_difference_handler(
                        CalculateDifferenceInput(
                            left_value=left_value,
                            right_value=right_value,
                            calculation_type="ABSOLUTE_PERCENTAGE_DIFFERENCE",
                            denominator_policy="RIGHT_VALUE_ABSOLUTE",
                        ),
                        context,
                    ).model_dump(mode="json")
                    differences[str(left_id)] = diff

                th_out = apply_threshold_handler(
                    ApplyThresholdInput(
                        value=diff.get("difference_percentage"),
                        threshold_type=threshold_type,
                        operator=operator,
                        threshold_value=float(threshold_value),
                        is_missing_counterpart=False,
                    ),
                    context,
                )
                self._record(context, "apply_threshold", th_out.model_dump(mode="json"),
                             {"left_id": left_id, "difference_percentage": diff.get("difference_percentage")})

        # A plan with a threshold operation but no difference operation computes differences here.
        if operation in ("APPLY_THRESHOLD", "EVALUATE_THRESHOLD") and not differences:
            for pair in comp_data.get("matched_pairs", []):
                left = pair.get("left") or {}
                right = pair.get("right") or {}
                diff = calculate_difference_handler(
                    CalculateDifferenceInput(
                        left_value=float(left.get(left_amount_field) or 0.0),
                        right_value=float(right.get(right_amount_field) or 0.0),
                    ),
                    context,
                ).model_dump(mode="json")
                differences[str(left.get(left_id_field))] = diff

    def _run_prepare_step(self, step: Dict[str, Any], context: AuditExecutionContext) -> None:
        """The only step type the LLM is allowed to influence, and only as text output."""
        instruction = (step.get("parameters") or {}).get("instruction") or step.get("description") or ""
        population = self._population_reference(step, context)
        sample = (context.get_dataset(population) or [])[:5] if population else []
        messages = [
            {"role": "system", "content": load_agent_system_prompt()},
            {
                "role": "user",
                "content": (
                    f"KRI: {context.kri.identifier} - {context.kri.name}\n"
                    f"Audit window: {context.start_date} to {context.end_date}\n"
                    f"Instruction: {instruction}\n"
                    f"Record sample: {json_dumps(sample)}\n\n"
                    f"Provide a short analysis. Do not request tool calls."
                ),
            },
        ]
        provider = self._llm_provider
        if provider is None:
            try:
                from app.services.llm_provider import get_llm_provider

                provider = get_llm_provider()
            except Exception as exc:
                logger.warning("No LLM provider available for PREPARE step: %s", exc)
                provider = None

        content = ""
        if provider is not None:
            try:
                response = provider.create_response(messages=messages, tools=None)
                content = response.get("content") or ""
            except Exception as exc:
                logger.warning("PREPARE step LLM call failed: %s", exc)
        if not content:
            content = f"Prepared analysis for instruction: {instruction}"

        context.prepared_notes.append({"step_number": step.get("step_number"), "note": content})
        self._record(context, "prepare", {"status": "SUCCESS", "content": content}, {"instruction": instruction})

    # -- argument binding -----------------------------------------------------

    def _resolve_arguments(self, step: Dict[str, Any], context: AuditExecutionContext) -> Dict[str, Any]:
        """Bind plan-time symbolic references to concrete run-time values."""
        operation = step["operation"]
        tool_name = step["tool_name"]
        params = dict(step.get("parameters") or {})

        if tool_name == "fetch_financial_data":
            args: Dict[str, Any] = {
                "data_source_code": params.get("data_source_code") or step.get("data_source_code"),
                "entity_code": params.get("entity_code") or step.get("entity_code"),
                "start_date": context.start_date,
                "end_date": context.end_date,
                "filters": dict(params.get("filters") or {}),
            }
            if context.customer_filter:
                args["filters"].setdefault("customer_name", context.customer_filter)
            return args

        if tool_name == "compare_records":
            left_alias = params.get("left_alias")
            right_alias = params.get("right_alias")
            left_ref = context.resolve_dataset(left_alias)
            right_ref = context.resolve_dataset(right_alias)
            if not left_ref or not right_ref:
                raise PlanExecutionError(
                    f"Step {step.get('step_number')} needs datasets for aliases "
                    f"'{left_alias}' and '{right_alias}', but only "
                    f"{sorted(context.dataset_refs)} are bound in this run."
                )
            return {
                "left_dataset_reference": left_ref,
                "right_dataset_reference": right_ref,
                "match_configuration": dict(params.get("match_configuration") or {}),
            }

        if tool_name == "calculate_kri_metrics":
            population_alias = params.get("population_alias")
            return {
                "audit_run_reference": context.run_reference,
                "calculation_policy": dict(
                    params.get("calculation_policy")
                    or {"mismatch_value_definition": "ORDER_VALUE_OF_QUALIFYING_EXCEPTIONS"}
                ),
                "population_dataset_reference": context.resolve_dataset(population_alias),
            }

        if tool_name == "analyze_oi_debookings":
            return {
                "audit_run_reference": context.run_reference,
                "bookings_dataset_reference": self._require_dataset(context, params, "bookings_alias", step),
                "debookings_dataset_reference": self._require_dataset(
                    context, params, "debookings_alias", step
                ),
                "customer_quarter_ratio_threshold": params.get("customer_quarter_ratio_threshold", 0.15),
                "rules": params.get("recognition_rules") or [],
            }

        if tool_name == "build_evidence":
            return {"audit_run_reference": context.run_reference}

        if tool_name == "generate_explanation":
            return {
                "exception_type": params.get("exception_type") or "AMOUNT_MISMATCH",
                "calculation": dict(params.get("calculation") or {}),
            }

        if tool_name in ("calculate_difference", "apply_threshold"):
            raise PlanExecutionError(
                f"Tool '{tool_name}' is a per-record operation and cannot be dispatched once "
                f"for the whole plan."
            )

        # Generic passthrough for any future registered tool.
        return params

    def _population_reference(self, step: Dict[str, Any], context: AuditExecutionContext) -> Optional[str]:
        alias = (step.get("parameters") or {}).get("population_alias")
        return context.resolve_dataset(alias) if alias else None

    def _require_dataset(
        self, context: AuditExecutionContext, params: Dict[str, Any], alias_key: str, step: Dict[str, Any]
    ) -> str:
        """Resolve a plan-declared dataset alias, failing loudly when it is unbound."""
        alias = params.get(alias_key)
        reference = context.resolve_dataset(alias) if alias else None
        if not reference:
            raise PlanExecutionError(
                f"Step {step.get('step_number')} needs the dataset bound to '{alias_key}' "
                f"({alias}), but only {sorted(context.dataset_refs)} are bound in this run."
            )
        return reference

    # -- debooking findings ---------------------------------------------------

    def _promote_debooking_findings(
        self, output: Dict[str, Any], context: AuditExecutionContext, step: Dict[str, Any]
    ) -> None:
        """Convert debooking analysis findings into candidate audit exceptions."""
        for finding in output.get("findings", []) or []:
            order_id = finding.get("order_id") or f"AGG:{finding.get('customer_name')}"
            amount = float(finding.get("debooking_amount") or 0.0)
            explanation = generate_explanation_handler(
                GenerateExplanationInput(
                    exception_type=finding.get("code", "DEBOOKING_FINDING"),
                    calculation={
                        "order_id": order_id,
                        "customer_name": finding.get("customer_name"),
                        "booking_quarter": finding.get("booking_quarter"),
                        "debooking_quarter": finding.get("debooking_quarter"),
                        "debooking_amount": amount,
                        "currency": finding.get("currency") or "USD",
                        "reason_code": finding.get("reason_code"),
                        "debooking_reference": finding.get("debooking_reference"),
                        "detail": finding.get("detail"),
                        "booked_value": finding.get("booked_value"),
                        "debooking_ratio": finding.get("debooking_ratio"),
                        "ratio_threshold": finding.get("ratio_threshold"),
                    },
                ),
                context,
            )
            candidate = {
                "exception_reference": f"exc_{uuid.uuid4().hex[:8]}",
                "order_id": order_id,
                "order_amount": amount,
                "po_amount": None,
                "exception_type": finding.get("code"),
                "severity": finding.get("severity", "MEDIUM"),
                "reason_code": finding.get("reason_code") or finding.get("code"),
                "difference_amount": -amount,
                "difference_percentage": None,
                "threshold_value": finding.get("ratio_threshold"),
                "explanation": explanation.explanation,
                "order_record": {
                    "order_id": order_id,
                    "customer_name": finding.get("customer_name"),
                    "debooking_id": finding.get("debooking_reference"),
                    "debooking_amount": amount,
                    "currency": finding.get("currency") or "USD",
                    "reason_code": finding.get("reason_code"),
                    "booking_quarter": finding.get("booking_quarter"),
                    "debooking_quarter": finding.get("debooking_quarter"),
                    "source_system": "SAP_ECC",
                },
                "po_record": None,
                "calculation_details": {
                    "order_id": order_id,
                    "customer_name": finding.get("customer_name"),
                    "booking_quarter": finding.get("booking_quarter"),
                    "debooking_quarter": finding.get("debooking_quarter"),
                    "debooking_amount": amount,
                    "reason_code": finding.get("reason_code"),
                    "debooking_reference": finding.get("debooking_reference"),
                    "matched_booking": finding.get("matched_booking"),
                    "aggregate": finding.get("aggregate", False),
                    "booked_value": finding.get("booked_value"),
                    "debooking_ratio": finding.get("debooking_ratio"),
                    "ratio_threshold": finding.get("ratio_threshold"),
                },
                "threshold_details": {
                    "debooking_ratio": finding.get("debooking_ratio"),
                    "ratio_threshold": finding.get("ratio_threshold"),
                    "reason_code": finding.get("reason_code"),
                },
            }
            context.add_candidate_exception(candidate)
            if context.run_logger:
                context.run_logger.log_exception_detected(candidate)

    # -- exception rules ------------------------------------------------------

    def _apply_exception_rules(
        self, comp_data: Dict[str, Any], context: AuditExecutionContext, plan: Dict[str, Any]
    ) -> None:
        """Turn comparison buckets into candidate exceptions using the plan's rules.

        The three default rules reproduce the previous hardcoded behaviour exactly; a plan
        carrying different rules genuinely produces a different exception set.
        """
        rules = plan.get("exception_rules") or []
        if not rules:
            rules = [rule.model_dump() for rule in PlanService.build_exception_rules()]

        threshold_value = self._threshold_value(context)
        left_id_field = comp_data.get("left_id_field") or "order_id"
        right_id_field = comp_data.get("right_id_field") or "po_id"
        left_amount_field = comp_data.get("left_amount_field") or "order_amount"
        right_amount_field = comp_data.get("right_amount_field") or "po_amount"

        for rule in rules:
            bucket_name = rule.get("source_bucket")
            records = comp_data.get(bucket_name) or []
            for record in records:
                candidate = self._build_candidate(
                    rule=rule,
                    record=record,
                    context=context,
                    threshold_value=threshold_value,
                    left_id_field=left_id_field,
                    right_id_field=right_id_field,
                    left_amount_field=left_amount_field,
                    right_amount_field=right_amount_field,
                )
                if candidate is not None:
                    context.add_candidate_exception(candidate)
                    if context.run_logger:
                        context.run_logger.log_exception_detected(candidate)

    def _build_candidate(
        self,
        rule: Dict[str, Any],
        record: Dict[str, Any],
        context: AuditExecutionContext,
        threshold_value: Optional[float],
        left_id_field: str,
        right_id_field: str,
        left_amount_field: str,
        right_amount_field: str,
    ) -> Optional[Dict[str, Any]]:
        if not rule.get("create_exception", True):
            return None

        rule_name = rule.get("rule")
        exception_type = rule.get("exception_type")
        left = record.get("left") or {}
        left_id = left.get(left_id_field)
        left_amount = float(left.get(left_amount_field) or 0.0)

        # A rule that evaluates a variance needs a limit. Without one the record is skipped
        # and the gap is reported, rather than compared against an invented threshold.
        if rule.get("only_if_threshold_breached") and threshold_value is None:
            return None

        base: Dict[str, Any] = {
            "exception_reference": f"exc_{uuid.uuid4().hex[:8]}",
            "order_id": left_id,
            "order_amount": left_amount,
            "threshold_value": threshold_value,
            "exception_type": exception_type,
        }

        if rule_name == "MISSING_COUNTERPART":
            diff_out = calculate_difference_handler(
                CalculateDifferenceInput(left_value=left_amount, right_value=None), context
            )
            th_out = apply_threshold_handler(
                ApplyThresholdInput(
                    value=None,
                    threshold_type="PERCENTAGE_DIFFERENCE",
                    operator=">",
                    threshold_value=threshold_value,
                    is_missing_counterpart=True,
                ),
                context,
            )
            exp_out = generate_explanation_handler(
                GenerateExplanationInput(
                    exception_type=exception_type,
                    calculation={"order_amount": left_amount, "currency": left.get("currency", "USD")},
                ),
                context,
            )
            base.update(
                {
                    "po_id": None,
                    "po_amount": None,
                    "severity": rule.get("severity_override") or th_out.severity,
                    "reason_code": rule.get("reason_code") or th_out.reason_code,
                    "difference_amount": diff_out.difference,
                    "difference_percentage": diff_out.difference_percentage,
                    "explanation": exp_out.explanation,
                    "order_record": left,
                    "po_record": None,
                    "calculation_details": diff_out.model_dump(mode="json"),
                    "threshold_details": th_out.model_dump(mode="json"),
                }
            )
            return base

        if rule_name == "VALUE_DIFFERENCE_EXCEEDS_THRESHOLD":
            right = record.get("right") or {}
            right_id = right.get(right_id_field)
            right_amount = float(right.get(right_amount_field) or 0.0)
            diff_out = calculate_difference_handler(
                CalculateDifferenceInput(left_value=left_amount, right_value=right_amount), context
            )
            th_out = apply_threshold_handler(
                ApplyThresholdInput(
                    value=diff_out.difference_percentage,
                    threshold_type="PERCENTAGE_DIFFERENCE",
                    operator=">",
                    threshold_value=threshold_value,
                    is_missing_counterpart=False,
                ),
                context,
            )
            if rule.get("only_if_threshold_breached") and not th_out.is_exception:
                return None
            exp_out = generate_explanation_handler(
                GenerateExplanationInput(
                    exception_type=exception_type,
                    calculation={
                        "order_amount": left_amount,
                        "po_amount": right_amount,
                        "difference_percentage": diff_out.difference_percentage,
                        "difference_amount": diff_out.absolute_difference,
                        "threshold": threshold_value,
                    },
                ),
                context,
            )
            base.update(
                {
                    "po_id": right_id,
                    "po_amount": right_amount,
                    "severity": rule.get("severity_override") or th_out.severity,
                    "reason_code": rule.get("reason_code") or th_out.reason_code,
                    "difference_amount": diff_out.difference,
                    "difference_percentage": diff_out.difference_percentage,
                    "explanation": exp_out.explanation,
                    "order_record": left,
                    "po_record": right,
                    "calculation_details": diff_out.model_dump(mode="json"),
                    "threshold_details": th_out.model_dump(mode="json"),
                }
            )
            return base

        if rule_name == "AMBIGUOUS_COUNTERPART":
            matches = record.get("matching") or []
            exp_out = generate_explanation_handler(
                GenerateExplanationInput(
                    exception_type=exception_type,
                    calculation={"matching_pos_count": len(matches)},
                ),
                context,
            )
            base.update(
                {
                    "po_id": None,
                    "po_amount": None,
                    "severity": rule.get("severity_override") or "HIGH",
                    "reason_code": rule.get("reason_code") or "AMBIGUOUS_MATCH",
                    "difference_amount": 0.0,
                    "difference_percentage": 0.0,
                    "explanation": exp_out.explanation,
                    "order_record": left,
                    "po_record": {"matching_pos": matches},
                    "calculation_details": {"ambiguous_pos_count": len(matches)},
                    "threshold_details": {"threshold_value": threshold_value},
                }
            )
            return base

        logger.warning("Unknown exception rule '%s'; skipped.", rule_name)
        return None

    def _threshold_value(self, context: AuditExecutionContext) -> Optional[float]:
        """Resolve the variance limit from the plan, never by re-deriving it.

        The plan carries the limit it was interpreted with, so execution cannot drift from
        the contract. A plan with no resolvable limit is reported rather than silently
        evaluated against a default.
        """
        plan = context.plan or {}
        resolved = plan.get("threshold")
        if resolved is None:
            for step in plan.get("steps", []):
                if step.get("operation") in ("APPLY_THRESHOLD", "EVALUATE_THRESHOLD"):
                    value = (step.get("parameters") or {}).get("threshold_value")
                    if value is not None:
                        resolved = value
                        break
        if resolved is None:
            resolved = context.get_active_threshold_value(default=None)
        if resolved is None:
            logger.warning(
                "Execution plan resolves no threshold; variance-based exception rules will be "
                "skipped rather than evaluated against a default."
            )
        return None if resolved is None else float(resolved)

    # -- bookkeeping ----------------------------------------------------------

    def _invoke(
        self, tool_name: str, tool: Any, args: Dict[str, Any], context: AuditExecutionContext, step: Dict[str, Any]
    ) -> Dict[str, Any]:
        start = time.time()
        context.tool_invocations_count += 1
        status, output, error = "SUCCESS", {}, None
        try:
            tool_output = tool.execute(args, context)
            output = tool_output.model_dump(mode="json")
            alias = (step.get("parameters") or {}).get("dataset_alias")
            if alias and tool_name == "fetch_financial_data" and output.get("dataset_reference"):
                context.bind_dataset(alias, output["dataset_reference"])
        except Exception as exc:
            status, error = "ERROR", str(exc)
            output = {"status": "ERROR", "error": error}
        elapsed = round((time.time() - start) * 1000.0, 2)

        context.audit_repo.record_tool_invocation(
            audit_run_id=context.audit_run_id,
            step_number=context.tool_invocations_count,
            tool_name=tool_name,
            input_payload=_jsonable(args),
            output_payload=_jsonable(output),
            status=status,
            error_message=error,
            execution_time_ms=elapsed,
        )
        if context.run_logger:
            context.run_logger.log_tool_call(
                step_number=context.tool_invocations_count,
                tool_name=tool_name,
                input_payload=_jsonable(args),
                output_payload=_jsonable(output),
                status=status,
                execution_time_ms=elapsed,
                error_message=error,
            )
        if status == "ERROR":
            raise PlanExecutionError(f"Tool '{tool_name}' failed: {error}")
        return output

    def _record(
        self,
        context: AuditExecutionContext,
        tool_name: str,
        output: Dict[str, Any],
        args: Dict[str, Any],
    ) -> None:
        """Record a per-record tool call in the trace without inflating plan step counts."""
        context.tool_invocations_count += 1
        context.audit_repo.record_tool_invocation(
            audit_run_id=context.audit_run_id,
            step_number=context.tool_invocations_count,
            tool_name=tool_name,
            input_payload=_jsonable(args),
            output_payload=_jsonable(output),
            status="SUCCESS",
            execution_time_ms=0.0,
        )

    def _persist_exceptions(self, context: AuditExecutionContext) -> None:
        for exc in context.candidate_exceptions:
            evidence = context.generated_evidence.get(exc.get("exception_reference"))
            context.audit_repo.create_exception(
                audit_run_id=context.audit_run_id,
                order_id=exc.get("order_id"),
                po_id=exc.get("po_id"),
                exception_type=exc.get("exception_type"),
                severity=exc.get("severity"),
                reason_code=exc.get("reason_code"),
                order_amount=exc.get("order_amount"),
                po_amount=exc.get("po_amount"),
                difference_amount=exc.get("difference_amount"),
                difference_percentage=exc.get("difference_percentage"),
                threshold_value=exc.get("threshold_value"),
                explanation=exc.get("explanation"),
                evidence_reference=evidence.get("evidence_reference") if evidence else None,
                exception_reference=exc.get("exception_reference"),
            )

    def _log(
        self,
        context: AuditExecutionContext,
        stage: str,
        message: str,
        step_number: int,
        payload: Dict[str, Any],
    ) -> None:
        if context.run_logger:
            context.run_logger.log_event(
                stage=stage, message=message, step_number=step_number, payload=_jsonable(payload)
            )


def _jsonable(value: Any) -> Any:
    import json
    from datetime import date, datetime

    def default(obj: Any) -> str:
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return str(obj)

    return json.loads(json.dumps(value, default=default))


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, default=str)
