"""Tool 8: analyze_oi_debookings.

Computes the value of Order Intake quarter-on-quarter, per customer, by netting debits
raised against the order intake population. Three independent signals drive the findings:

1. **Timing** - was the reversal posted in the same quarter as the booking, or a later one?
   A later reversal means the revenue was recognised in a period before the event that
   invalidated it, which is the premature-recognition population.
2. **Support** - *why* was it reversed. A price correction is a commercial reclassification
   of a valid order; a missing contract, cancellation, duplicate booking or post-recognition
   credit means the original recognition was never supported.
3. **Concentration** - what proportion of a customer's quarter-on-quarter order intake value
   was reversed within that same quarter. A high ratio is a booking-quality signal.

The tool is purely computational: it returns ``findings`` and the per-customer-quarter
summary. Turning findings into audit exceptions is the executor's job, so the rules that
decide what becomes an exception live in the execution plan, not in this handler.
"""

import datetime
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.tools import (
    AnalyzeOIDebookingsInput,
    AnalyzeOIDebookingsOutput,
    CustomerQuarterSummary,
    DebookingRule,
    UNSUPPORTED_RECOGNITION_REASONS,
)
from app.services.execution_context import AuditExecutionContext

#: How long after a quarter closes the books may still be open for postings, in days.
BOOKING_WINDOW_DAYS = 90


def quarter_of(value: Any) -> str:
    """Return the fiscal quarter label (``YYYY-Qn``) for a date-like value or ISO string."""
    if value is None:
        return "UNKNOWN"
    if isinstance(value, str):
        try:
            value = datetime.date.fromisoformat(value[:10])
        except Exception:
            return "UNKNOWN"
    try:
        year = value.year
        quarter = (value.month - 1) // 3 + 1
        return f"{year}-Q{quarter}"
    except (AttributeError, TypeError):
        return "UNKNOWN"


def _quarter_ordinal(label: str) -> Tuple[int, int]:
    """Convert ``YYYY-Qn`` into a sortable (year, quarter) tuple."""
    try:
        year, quarter = label.split("-Q")
        return int(year), int(quarter)
    except Exception:
        return 0, 0


def default_rules(ratio_threshold: float) -> List[DebookingRule]:
    """The default recognition-quality rule set, used when a plan supplies none."""
    return [
        DebookingRule(
            code="PREMATURE_RECOGNITION",
            severity="HIGH",
            condition="CROSS_QUARTER",
        ),
        DebookingRule(
            code="UNSUPPORTED_RECOGNITION",
            severity="CRITICAL",
            condition="REASON_CODE_IN",
            reason_codes=list(UNSUPPORTED_RECOGNITION_REASONS),
        ),
        DebookingRule(
            code="ORPHAN_DEBOOKING",
            severity="HIGH",
            condition="NO_MATCHING_BOOKING",
        ),
        DebookingRule(
            code="BOOKING_QUALITY",
            severity="MEDIUM",
            condition="CUSTOMER_QUARTER_RATIO_ABOVE",
            threshold=ratio_threshold,
        ),
    ]


def analyze_oi_debookings_handler(
    input_data: AnalyzeOIDebookingsInput,
    context: Optional[AuditExecutionContext] = None,
) -> AnalyzeOIDebookingsOutput:
    """Net Order Intake against debookings per customer per quarter."""
    bookings = _load(context, input_data.bookings_dataset_reference)
    debookings = _load(context, input_data.debookings_dataset_reference)

    rules = input_data.rules or default_rules(input_data.customer_quarter_ratio_threshold)
    ratio_threshold = input_data.customer_quarter_ratio_threshold
    for rule in rules:
        if rule.condition == "CUSTOMER_QUARTER_RATIO_ABOVE" and rule.threshold is not None:
            ratio_threshold = rule.threshold

    bookings_by_id = {b.get("order_id"): b for b in bookings if b.get("order_id")}

    # --- per customer + booking quarter: booked value -------------------------
    booked: Dict[Tuple[str, str], float] = defaultdict(float)
    booked_count: Dict[Tuple[str, str], int] = defaultdict(int)
    booking_quarters: Dict[str, List[str]] = defaultdict(list)
    for booking in bookings:
        key = (booking.get("customer_name") or "UNKNOWN", quarter_of(booking.get("order_date")))
        booked[key] += float(booking.get("order_amount") or 0.0)
        booked_count[key] += 1
        if booking.get("order_id"):
            booking_quarters[str(booking["order_id"])].append(key[1])

    # --- per debooking: which quarter did the reversal land in? ---------------
    same_q: Dict[Tuple[str, str], float] = defaultdict(float)
    cross_q: Dict[Tuple[str, str], float] = defaultdict(float)
    debooked_count: Dict[Tuple[str, str], int] = defaultdict(int)
    findings: List[Dict[str, Any]] = []

    for debit in debookings:
        amount = float(debit.get("debooking_amount") or debit.get("amount") or 0.0)
        customer = debit.get("customer_name") or (bookings_by_id.get(debit.get("order_id"), {}).get("customer_name")) or "UNKNOWN"
        booking_date = debit.get("booking_date") or (bookings_by_id.get(debit.get("order_id"), {}).get("order_date"))
        booking_q = quarter_of(booking_date)
        debit_q = quarter_of(debit.get("debooking_date"))
        reason = (debit.get("reason_code") or "UNKNOWN").upper()
        order_id = debit.get("order_id")

        # The customer-quarter the reversal is measured against is the quarter the
        # reversal landed in; the booking quarter is compared against it for timing.
        key = (customer, debit_q)
        debooked_count[key] += 1
        if _quarter_ordinal(debit_q) > _quarter_ordinal(booking_q):
            cross_q[key] += amount
        else:
            same_q[key] += amount

        matched = order_id in bookings_by_id
        for rule in rules:
            if not rule.create_exception:
                continue
            hit = _rule_matches(
                rule,
                debit=debit,
                matched=matched,
                booking_quarter=booking_q,
                debit_quarter=debit_q,
                reason=reason,
            )
            if hit:
                findings.append(
                    {
                        "code": rule.code,
                        "severity": rule.severity,
                        "condition": rule.condition,
                        "reason_code": reason,
                        "order_id": order_id,
                        "customer_name": customer,
                        "booking_quarter": booking_q,
                        "debooking_quarter": debit_q,
                        "debooking_amount": amount,
                        "currency": debit.get("currency") or "USD",
                        "debooking_reference": debit.get("debooking_id"),
                        "matched_booking": matched,
                        "detail": hit,
                    }
                )

    # --- per customer-quarter summary + concentration rule --------------------
    keys = set(booked) | set(same_q) | set(cross_q)
    summaries: List[CustomerQuarterSummary] = []
    for customer, quarter in sorted(keys):
        booked_value = round(booked.get((customer, quarter), 0.0), 2)
        debooked_value = round(same_q.get((customer, quarter), 0.0) + cross_q.get((customer, quarter), 0.0), 2)
        same_value = round(same_q.get((customer, quarter), 0.0), 2)
        cross_value = round(cross_q.get((customer, quarter), 0.0), 2)
        # The concentration signal is same-quarter reversal against same-quarter booking:
        # that is the population that gets booked and un-booked within one period.
        ratio = round(same_value / booked_value, 4) if booked_value > 0 else 0.0
        summary = CustomerQuarterSummary(
            customer_name=customer,
            quarter=quarter,
            booked_value=booked_value,
            debooked_value=debooked_value,
            same_quarter_debooked_value=same_value,
            cross_quarter_debooked_value=cross_value,
            net_order_intake_value=round(booked_value - debooked_value, 2),
            debooking_ratio=ratio,
            booking_count=booked_count.get((customer, quarter), 0),
            debooking_count=debooked_count.get((customer, quarter), 0),
        )
        summaries.append(summary)

        for rule in rules:
            if not rule.create_exception or rule.condition != "CUSTOMER_QUARTER_RATIO_ABOVE":
                continue
            limit = rule.threshold if rule.threshold is not None else ratio_threshold
            if booked_value > 0 and ratio > limit:
                findings.append(
                    {
                        "code": rule.code,
                        "severity": rule.severity,
                        "condition": rule.condition,
                        "reason_code": "DEBOOKING_CONCENTRATION",
                        # Aggregated finding: no single order, so the reference is synthetic.
                        "order_id": f"AGG:{customer}:{quarter}",
                        "customer_name": customer,
                        "booking_quarter": quarter,
                        "debooking_quarter": quarter,
                        "debooking_amount": same_value,
                        "currency": "USD",
                        "debooking_reference": None,
                        "matched_booking": True,
                        "detail": (
                            f"Same-quarter debookings of {same_value:,.2f} are {ratio:.1%} of "
                            f"{customer}'s {quarter} order intake value of {booked_value:,.2f}, "
                            f"above the {limit:.1%} booking-quality limit."
                        ),
                        "aggregate": True,
                        "booked_value": booked_value,
                        "debooking_ratio": ratio,
                        "ratio_threshold": limit,
                    }
                )

    counts: Dict[str, int] = defaultdict(int)
    for finding in findings:
        counts[finding["code"]] += 1

    return AnalyzeOIDebookingsOutput(
        status="SUCCESS",
        audit_run_reference=input_data.audit_run_reference,
        quarters_analyzed=sorted({s.quarter for s in summaries}),
        customer_quarters=summaries,
        findings=findings,
        total_booked_value=round(sum(s.booked_value for s in summaries), 2),
        total_debooked_value=round(sum(s.debooked_value for s in summaries), 2),
        net_order_intake_value=round(sum(s.net_order_intake_value for s in summaries), 2),
        counts_by_code=dict(counts),
        message=(
            f"Netted {len(bookings)} order intake records against {len(debookings)} debookings "
            f"across {len(summaries)} customer-quarters; {len(findings)} finding(s)."
        ),
    )


def _rule_matches(
    rule: DebookingRule,
    debit: Dict[str, Any],
    matched: bool,
    booking_quarter: str,
    debit_quarter: str,
    reason: str,
) -> Optional[str]:
    """Return a human-readable reason when a rule fires on this debooking, else ``None``."""
    condition = rule.condition
    amount = float(debit.get("debooking_amount") or 0.0)

    if condition == "CROSS_QUARTER":
        if _quarter_ordinal(debit_quarter) > _quarter_ordinal(booking_quarter):
            return (
                f"Order intake booked in {booking_quarter} was reversed in {debit_quarter}, so the "
                f"revenue was recognised in an earlier period than the event that invalidated it."
            )
        return None

    if condition == "SAME_QUARTER":
        if _quarter_ordinal(debit_quarter) == _quarter_ordinal(booking_quarter):
            return (
                f"Order intake booked in {booking_quarter} was reversed in the same quarter."
            )
        return None

    if condition == "REASON_CODE_IN":
        wanted = {r.upper() for r in (rule.reason_codes or [])}
        if reason in wanted:
            return (
                f"Reversal reason '{reason}' indicates the original recognition was never "
                f"supported, not merely reclassified."
            )
        return None

    if condition == "NO_MATCHING_BOOKING":
        if not matched:
            return (
                f"Debooking {debit.get('debooking_id')} references order "
                f"{debit.get('order_id')}, which has no order intake record in the population."
            )
        return None

    return None


def _load(context: Optional[AuditExecutionContext], reference: str) -> List[Dict[str, Any]]:
    if context is None:
        raise ValueError("An execution context is required to resolve dataset references.")
    records = context.get_dataset(reference)
    if records is None:
        raise ValueError(f"Dataset reference '{reference}' not found in execution context.")
    return records
