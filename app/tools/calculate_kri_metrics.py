"""Tool 5: calculate_kri_metrics.

Calculates aggregate KRI audit metrics deterministically from validated transaction-level results.
Computes count rates, financial value exposure, mismatch percentages, and severity breakdowns.
"""

from typing import Dict, Any
from collections import defaultdict
from app.schemas.tools import CalculateKRIMetricsInput, CalculateKRIMetricsOutput
from app.services.execution_context import AuditExecutionContext


def calculate_kri_metrics_handler(
    input_data: CalculateKRIMetricsInput,
    context: AuditExecutionContext,
) -> CalculateKRIMetricsOutput:
    """Compute aggregate KRI metrics from candidate exceptions and comparison records.

    The population is resolved from the plan-supplied dataset reference. The previous
    ``"order" in dataset_key`` substring sniff is retained only as a fallback for direct
    tool calls that carry no plan binding.
    """
    population_reference = input_data.population_dataset_reference
    if population_reference and context.get_dataset(population_reference) is None:
        raise ValueError(
            f"Population dataset '{population_reference}' was not produced by any extraction step in this run."
        )
    all_population = context.get_population_records(population_reference)

    total_transactions = len(all_population)
    total_population_value = sum(
        float(record.get("order_amount") or record.get("po_amount") or record.get("amount") or 0.0)
        for record in all_population
    )

    exceptions = context.candidate_exceptions
    counts_by_type = defaultdict(int)
    counts_by_severity = defaultdict(int)
    exception_order_ids = set()
    exception_order_value = 0.0

    missing_po_count = 0
    amount_mismatch_count = 0

    for exc in exceptions:
        order_id = exc.get("order_id")
        exc_type = exc.get("exception_type", "UNKNOWN")
        severity = exc.get("severity", "MEDIUM")
        order_amt = float(exc.get("order_amount", 0.0))

        counts_by_type[exc_type] += 1
        counts_by_severity[severity] += 1

        if exc_type == "MISSING_PO":
            missing_po_count += 1
        elif exc_type == "AMOUNT_MISMATCH":
            amount_mismatch_count += 1

        if order_id not in exception_order_ids:
            exception_order_ids.add(order_id)
            exception_order_value += order_amt

    # Aggregate value reporting differs by test family: a debooking analysis reports the
    # exception value as the debited amount, because that is the amount at risk.
    debooking_codes = {"PREMATURE_RECOGNITION", "UNSUPPORTED_RECOGNITION", "BOOKING_QUALITY", "ORPHAN_DEBOOKING"}
    debooked_value = 0.0
    for exc in exceptions:
        if exc.get("exception_type") in debooking_codes:
            debooked_value += abs(float(exc.get("order_amount") or 0.0))

    total_exceptions = len(exceptions)
    matched_count = total_transactions - len(exception_order_ids)

    mismatch_val_pct = (
        round((exception_order_value / total_population_value) * 100.0, 2)
        if total_population_value > 0
        else 0.0
    )
    exc_rate_count = (
        round((len(exception_order_ids) / total_transactions) * 100.0, 2)
        if total_transactions > 0
        else 0.0
    )
    exc_rate_value = mismatch_val_pct

    metrics_result = {
        "total_transactions": total_transactions,
        "total_order_value": round(total_population_value, 2),
        "matched_count": matched_count,
        "missing_po_count": missing_po_count,
        "amount_mismatch_count": amount_mismatch_count,
        "total_exceptions": total_exceptions,
        "exception_order_value": round(exception_order_value, 2),
        "mismatch_value_percentage": mismatch_val_pct,
        "exception_rate_by_count": exc_rate_count,
        "exception_rate_by_value": exc_rate_value,
        "counts_by_exception_type": dict(counts_by_type),
        "counts_by_severity": dict(counts_by_severity),
        "total_debooked_value": round(debooked_value, 2),
    }

    context.calculated_metrics = metrics_result

    metric_defs = {
        "total_transactions": "Total population of records evaluated within the audit period.",
        "total_order_value": "Aggregate monetary sum of the evaluated population.",
        "matched_count": "Population records with a counterpart satisfying the configured tolerance.",
        "missing_po_count": "Population records booked without any counterpart record.",
        "amount_mismatch_count": "Matched records whose amount variance exceeds the configured threshold.",
        "total_exceptions": "Count of all identified audit exceptions.",
        "exception_order_value": "Total monetary value of records associated with flagged exceptions.",
        "mismatch_value_percentage": "Ratio of exception value to total population value.",
        "exception_rate_by_count": "Percentage of total record count flagged as exceptions.",
        "exception_rate_by_value": "Percentage of total monetary value flagged as exceptions.",
    }

    return CalculateKRIMetricsOutput(
        status="SUCCESS",
        total_transactions=total_transactions,
        total_order_value=round(total_population_value, 2),
        matched_count=matched_count,
        missing_po_count=missing_po_count,
        amount_mismatch_count=amount_mismatch_count,
        total_exceptions=total_exceptions,
        exception_order_value=round(exception_order_value, 2),
        mismatch_value_percentage=mismatch_val_pct,
        exception_rate_by_count=exc_rate_count,
        exception_rate_by_value=exc_rate_value,
        counts_by_exception_type=dict(counts_by_type),
        counts_by_severity=dict(counts_by_severity),
        metric_definitions=metric_defs,
    )

