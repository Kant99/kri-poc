"""Tool 7: generate_explanation.

Generates authoritative, plain-English explanations using deterministic templates.
Preserves exact numerical values and reason codes without hallucinations.
"""

from typing import Optional
from app.schemas.tools import GenerateExplanationInput, GenerateExplanationOutput
from app.services.execution_context import AuditExecutionContext


def generate_explanation_handler(
    input_data: GenerateExplanationInput,
    context: Optional[AuditExecutionContext] = None,
) -> GenerateExplanationOutput:
    """Generate human-readable audit finding explanation based on structured evidence."""
    exc_type = input_data.exception_type.upper()
    calc = input_data.calculation

    if exc_type == "MISSING_PO":
        order_amount = calc.get("order_amount", "N/A")
        currency = calc.get("currency", "USD")
        explanation = (
            f"Customer order booked for {order_amount} {currency} has no corresponding purchase order "
            f"found in the PO source system, violating the 1:1 PO alignment control."
        )
        reason_code = "MISSING_PO"

    elif exc_type == "AMOUNT_MISMATCH":
        order_amt = calc.get("order_amount", 0.0)
        po_amt = calc.get("po_amount", 0.0)
        diff_pct = calc.get("difference_percentage", 0.0)
        threshold = calc.get("threshold", 10.0)
        diff_amt = calc.get("difference_amount", abs(order_amt - po_amt))

        direction = "higher" if order_amt > po_amt else "lower"
        explanation = (
            f"The order amount (${order_amt:,.2f}) is {diff_pct:.2f}% {direction} than the PO amount "
            f"(${po_amt:,.2f}) by a difference of ${diff_amt:,.2f}, exceeding the configured {threshold:.1f}% threshold."
        )
        reason_code = "AMOUNT_MISMATCH"

    elif exc_type == "AMBIGUOUS_MATCH" or exc_type == "DUPLICATE_MATCH":
        match_count = calc.get("matching_pos_count", 2)
        explanation = (
            f"Customer order resolved to {match_count} conflicting purchase orders in the PO system. "
            f"Requires manual reviewer reconciliation to determine the authoritative match."
        )
        reason_code = "AMBIGUOUS_MATCH"

    else:
        explanation = f"Audit validation exception flagged for type {exc_type} based on deterministic evaluation."
        reason_code = exc_type

    return GenerateExplanationOutput(
        status="SUCCESS",
        explanation=explanation,
        reason_code=reason_code,
        is_deterministic=True,
    )
