"""Tool 3: calculate_difference.

Calculates numerical amount differences and percentage variances deterministically.
Handles missing values and zero denominators with explicit, controlled policies.
"""

from typing import Optional
from app.schemas.tools import CalculateDifferenceInput, CalculateDifferenceOutput
from app.services.execution_context import AuditExecutionContext


def calculate_difference_handler(
    input_data: CalculateDifferenceInput,
    context: Optional[AuditExecutionContext] = None,
) -> CalculateDifferenceOutput:
    """Perform deterministic difference calculation between order amount and PO amount."""
    left_val = float(input_data.left_value)

    if input_data.right_value is None:
        # Case where counterpart (PO) is completely missing
        return CalculateDifferenceOutput(
            status="SUCCESS",
            left_value=left_val,
            right_value=None,
            difference=left_val,
            absolute_difference=abs(left_val),
            difference_percentage=100.0,
            note="Counterpart value is null; difference percentage treated as 100.0% variance.",
        )

    right_val = float(input_data.right_value)
    diff = left_val - right_val
    abs_diff = abs(diff)

    # Determine denominator based on configured policy
    policy = input_data.denominator_policy.upper()
    if policy == "LEFT_VALUE_ABSOLUTE":
        denominator = abs(left_val)
    elif policy == "AVERAGE_ABSOLUTE":
        denominator = (abs(left_val) + abs(right_val)) / 2.0
    else:  # Default: RIGHT_VALUE_ABSOLUTE
        denominator = abs(right_val)

    if denominator == 0.0:
        if abs_diff == 0.0:
            diff_pct = 0.0
            note = "Both values are zero; variance is 0.0%."
        else:
            diff_pct = 100.0
            note = "Zero denominator encountered with non-zero numerator; capped at 100.0% variance."
    else:
        diff_pct = round((abs_diff / denominator) * 100.0, 4)
        note = f"Calculated variance against denominator policy {policy}."

    return CalculateDifferenceOutput(
        status="SUCCESS",
        left_value=left_val,
        right_value=right_val,
        difference=diff,
        absolute_difference=abs_diff,
        difference_percentage=diff_pct,
        note=note,
    )
