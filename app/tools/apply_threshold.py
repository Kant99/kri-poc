"""Tool 4: apply_threshold.

Applies configured KRI threshold logic deterministically to variance calculations.
Evaluates severity and reason codes without allowing arbitrary LLM overrides.
"""

from typing import Optional
from app.core.security import validate_operator
from app.schemas.tools import ApplyThresholdInput, ApplyThresholdOutput
from app.services.execution_context import AuditExecutionContext


def apply_threshold_handler(
    input_data: ApplyThresholdInput,
    context: Optional[AuditExecutionContext] = None,
) -> ApplyThresholdOutput:
    """Evaluate whether variance exceeds threshold and determine severity classification."""
    if input_data.is_missing_counterpart or input_data.value is None:
        return ApplyThresholdOutput(
            is_exception=True,
            reason_code="MISSING_PO",
            actual_value=None,
            threshold_value=input_data.threshold_value,
            operator=input_data.operator,
            severity="HIGH",
        )

    op = validate_operator(input_data.operator)
    val = float(input_data.value)
    th_val = float(input_data.threshold_value)

    # Evaluate comparison operator
    if op == ">":
        is_exc = val > th_val
    elif op == ">=":
        is_exc = val >= th_val
    elif op == "<":
        is_exc = val < th_val
    elif op == "<=":
        is_exc = val <= th_val
    elif op == "==":
        is_exc = val == th_val
    elif op == "!=":
        is_exc = val != th_val
    else:
        is_exc = val > th_val

    if not is_exc:
        return ApplyThresholdOutput(
            is_exception=False,
            reason_code="WITHIN_TOLERANCE",
            actual_value=val,
            threshold_value=th_val,
            operator=op,
            severity="LOW",
        )

    # Determine severity for amount mismatches
    if val > 50.0:
        severity = "CRITICAL"
    elif val > 25.0:
        severity = "HIGH"
    else:
        severity = "MEDIUM"

    return ApplyThresholdOutput(
        is_exception=True,
        reason_code="AMOUNT_MISMATCH",
        actual_value=val,
        threshold_value=th_val,
        operator=op,
        severity=severity,
    )
