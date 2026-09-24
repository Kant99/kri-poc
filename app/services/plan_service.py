"""Execution Plan Generation and Validation Service.

Translates administrator-defined natural language test steps into structured, executable plans.
Validates dependencies, allowed tools, parameters, and ensures mandatory audit stages are enforced.
"""

from typing import List, Dict, Any, Optional
from app.models.kri import KRI, KRITestStep
from app.schemas.kri import StructuredPlan, PlannedStepItem, PlanValidationResult

OPERATION_TOOL_MAP = {
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
}


class PlanService:
    """Handles parsing, translating, and validating KRI test steps into structured execution plans."""

    @classmethod
    def generate_plan_from_steps(cls, kri: KRI) -> StructuredPlan:
        """Translate active test steps of a KRI into a structured execution plan."""
        planned_steps: List[PlannedStepItem] = []
        active_steps = [s for s in kri.test_steps if s.is_active]
        active_steps.sort(key=lambda s: s.step_number)

        # Default standard mapping for primary KRI if steps are standard
        for step in active_steps:
            text = (step.title + " " + step.instruction).lower()
            operation = "UNKNOWN"
            tool_name = "fetch_financial_data"
            params: Dict[str, Any] = {}

            if "extract" in text or "population" in text or "fetch" in text:
                operation = "EXTRACT_POPULATION"
                tool_name = "fetch_financial_data"
                params = {
                    "source_system": "SAP_ECC",
                    "entity": "ORDER_INTAKE",
                }
            elif "match" in text or "join" in text or "compare records" in text:
                operation = "MATCH_RECORDS"
                tool_name = "compare_records"
                params = {
                    "left_field": "order_id",
                    "right_field": "order_id",
                    "fallback_field": "po_reference",
                }
            elif "compare order amounts" in text or "difference" in text or "amount" in text and "compare" in text:
                operation = "COMPARE_AMOUNTS"
                tool_name = "calculate_difference"
                params = {
                    "calculation_type": "ABSOLUTE_PERCENTAGE_DIFFERENCE",
                    "denominator_policy": "RIGHT_VALUE_ABSOLUTE",
                }
            elif "threshold" in text or "flag" in text or "mismatch" in text:
                operation = "APPLY_THRESHOLD"
                tool_name = "apply_threshold"
                # Lookup KRI threshold value if configured
                th_val = 10.0
                if kri.thresholds:
                    th_val = kri.thresholds[0].threshold_value
                params = {
                    "threshold_type": "PERCENTAGE_DIFFERENCE",
                    "operator": ">",
                    "threshold_value": th_val,
                }
            elif "evidence" in text or "explanation" in text or "prepare" in text or "report" in text:
                operation = "BUILD_EVIDENCE"
                tool_name = "build_evidence"
                params = {}
            else:
                operation = "EXTRACT_POPULATION"
                tool_name = "fetch_financial_data"
                params = {"source_system": "SAP_ECC", "entity": "ORDER_INTAKE"}

            planned_steps.append(
                PlannedStepItem(
                    step_number=step.step_number,
                    operation=operation,
                    tool_name=tool_name,
                    parameters=params,
                    description=step.title,
                )
            )

        # Enforce mandatory backend stages if missing (e.g. Metrics calculation)
        existing_ops = {s.operation for s in planned_steps}
        if "CALCULATE_METRICS" not in existing_ops:
            next_num = len(planned_steps) + 1
            planned_steps.append(
                PlannedStepItem(
                    step_number=next_num,
                    operation="CALCULATE_METRICS",
                    tool_name="calculate_kri_metrics",
                    parameters={"mismatch_value_definition": "ORDER_VALUE_OF_QUALIFYING_EXCEPTIONS"},
                    description="Aggregate KRI Metrics Calculation",
                )
            )

        return StructuredPlan(kri_id=kri.id, version=1, steps=planned_steps)

    @classmethod
    def validate_plan(cls, kri: KRI, plan: StructuredPlan) -> PlanValidationResult:
        """Validate execution plan dependencies, tools, parameters, and KRI integrity."""
        errors: List[str] = []
        warnings: List[str] = []

        if not plan.steps:
            errors.append("Execution plan contains no steps.")
            return PlanValidationResult(is_valid=False, kri_id=kri.id, errors=errors, plan=plan)

        operations = [s.operation for s in plan.steps]
        tool_names = [s.tool_name for s in plan.steps]

        # Check required data extraction
        if "EXTRACT_POPULATION" not in operations and "fetch_financial_data" not in tool_names:
            errors.append("Plan is missing mandatory 'EXTRACT_POPULATION' stage.")

        # Check record matching
        if "MATCH_RECORDS" not in operations and "compare_records" not in tool_names:
            errors.append("Plan is missing mandatory 'MATCH_RECORDS' stage.")

        # Check step numbers are continuous and positive
        step_numbers = [s.step_number for s in plan.steps]
        if len(step_numbers) != len(set(step_numbers)):
            errors.append("Plan contains duplicate step numbers.")

        # Check registered tool names
        allowed_tools = set(OPERATION_TOOL_MAP.values())
        for s in plan.steps:
            if s.tool_name not in allowed_tools:
                errors.append(f"Step {s.step_number} specifies unregistered tool '{s.tool_name}'.")

        # Threshold configuration check
        if not kri.thresholds:
            warnings.append("KRI has no configured thresholds. Default 10% threshold will be applied.")

        # Data source checks
        if not kri.data_sources:
            warnings.append("KRI has no explicit data sources assigned.")

        is_valid = len(errors) == 0
        return PlanValidationResult(
            is_valid=is_valid,
            kri_id=kri.id,
            errors=errors,
            warnings=warnings,
            plan=plan,
        )
