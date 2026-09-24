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
    """Compute aggregate KRI metrics from candidate exceptions and comparison records."""
    # Retrieve comparisons from context
    all_orders = []
    for ds_key, records in context.datasets.items():
        if "order" in ds_key.lower():
            all_orders.extend(records)

    total_transactions = len(all_orders)
    total_order_value = sum(float(o.get("order_amount", 0.0)) for o in all_orders)

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

    total_exceptions = len(exceptions)
    matched_count = total_transactions - len(exception_order_ids)

    mismatch_val_pct = (
        round((exception_order_value / total_order_value) * 100.0, 2)
        if total_order_value > 0
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
        "total_order_value": round(total_order_value, 2),
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
    }

    context.calculated_metrics = metrics_result

    metric_defs = {
        "total_transactions": "Total population of order intake records evaluated within audit period.",
        "total_order_value": "Aggregate monetary sum of all evaluated order intake transactions.",
        "matched_count": "Orders with corresponding purchase orders within acceptable variance tolerance.",
        "missing_po_count": "Orders booked without any corresponding purchase order.",
        "amount_mismatch_count": "Orders whose matched PO variance exceeds the configured threshold.",
        "total_exceptions": "Count of all identified audit exceptions.",
        "exception_order_value": "Total monetary value of orders associated with flagged exceptions.",
        "mismatch_value_percentage": "Ratio of exception order value to total order population value.",
        "exception_rate_by_count": "Percentage of total transaction count flagged as exceptions.",
        "exception_rate_by_value": "Percentage of total monetary value flagged as exceptions.",
    }

    return CalculateKRIMetricsOutput(
        status="SUCCESS",
        total_transactions=total_transactions,
        total_order_value=round(total_order_value, 2),
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
