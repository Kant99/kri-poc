"""Tool 2: compare_records.

Matches financial records deterministically between two scoped datasets (e.g. Order Intake vs Purchase Orders).
Identifies matched records, missing POs, amount mismatch candidates, and ambiguous or duplicate matches.
"""

import uuid
from typing import Dict, Any, List
from collections import defaultdict
from app.core.security import validate_matching_field
from app.schemas.tools import CompareRecordsInput, CompareRecordsOutput
from app.services.execution_context import AuditExecutionContext


def compare_records_handler(
    input_data: CompareRecordsInput,
    context: AuditExecutionContext,
) -> CompareRecordsOutput:
    """Execute deterministic matching between orders and purchase orders."""
    left_dataset = context.get_dataset(input_data.left_dataset_reference)
    if left_dataset is None:
        raise ValueError(f"Dataset reference '{input_data.left_dataset_reference}' not found in execution context.")

    right_dataset = context.get_dataset(input_data.right_dataset_reference)
    if right_dataset is None:
        raise ValueError(f"Dataset reference '{input_data.right_dataset_reference}' not found in execution context.")

    left_field = validate_matching_field(input_data.match_configuration.left_field)
    right_field = validate_matching_field(input_data.match_configuration.right_field)
    fallback_field = (
        validate_matching_field(input_data.match_configuration.fallback_field)
        if input_data.match_configuration.fallback_field
        else None
    )

    # Index right dataset (Purchase Orders) by right_field and po_id
    pos_by_order_id = defaultdict(list)
    pos_by_po_id = {}
    for po in right_dataset:
        if po.get(right_field):
            pos_by_order_id[str(po[right_field]).strip()].append(po)
        if po.get("po_id"):
            pos_by_po_id[str(po["po_id"]).strip()] = po

    matched_pairs: List[Dict[str, Any]] = []
    missing_pos: List[Dict[str, Any]] = []
    ambiguous_matches: List[Dict[str, Any]] = []
    matched_po_ids = set()

    for order in left_dataset:
        order_key = str(order.get(left_field, "")).strip()
        matched_pos_list = pos_by_order_id.get(order_key, [])

        # Check fallback if primary match didn't find anything
        if not matched_pos_list and fallback_field and order.get(fallback_field):
            fallback_key = str(order.get(fallback_field, "")).strip()
            if fallback_key in pos_by_po_id:
                matched_pos_list = [pos_by_po_id[fallback_key]]

        if len(matched_pos_list) == 1:
            po = matched_pos_list[0]
            matched_po_ids.add(po.get("po_id"))
            matched_pairs.append({
                "order": order,
                "po": po,
                "order_id": order.get("order_id"),
                "po_id": po.get("po_id"),
                "order_amount": order.get("order_amount"),
                "po_amount": po.get("po_amount"),
            })
        elif len(matched_pos_list) > 1:
            ambiguous_matches.append({
                "order": order,
                "matching_pos": matched_pos_list,
                "order_id": order.get("order_id"),
            })
            for po in matched_pos_list:
                matched_po_ids.add(po.get("po_id"))
        else:
            missing_pos.append({
                "order": order,
                "order_id": order.get("order_id"),
                "order_amount": order.get("order_amount"),
            })

    unmatched_pos = [po for po in right_dataset if po.get("po_id") not in matched_po_ids]

    comparison_reference = f"comp_{uuid.uuid4().hex[:6]}"
    comparison_data = {
        "comparison_reference": comparison_reference,
        "matched_pairs": matched_pairs,
        "missing_pos": missing_pos,
        "ambiguous_matches": ambiguous_matches,
        "unmatched_pos": unmatched_pos,
        "left_dataset_reference": input_data.left_dataset_reference,
        "right_dataset_reference": input_data.right_dataset_reference,
    }

    context.store_comparison(comparison_reference, comparison_data)

    return CompareRecordsOutput(
        status="SUCCESS",
        comparison_reference=comparison_reference,
        matched_count=len(matched_pairs),
        unmatched_orders_count=len(missing_pos),
        unmatched_pos_count=len(unmatched_pos),
        ambiguous_count=len(ambiguous_matches),
        exception_candidates_count=len(missing_pos) + len(ambiguous_matches),
        summary={
            "total_orders_evaluated": len(left_dataset),
            "total_pos_evaluated": len(right_dataset),
            "matched_orders": len(matched_pairs),
            "orders_without_po": len(missing_pos),
            "ambiguous_matched_orders": len(ambiguous_matches),
        },
    )
