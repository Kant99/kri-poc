"""Tool 2: compare_records.

Matches records deterministically between two scoped datasets. All field access is driven by
the ``MatchConfiguration`` resolved from the plan, so the same tool supports matching on any
shared column pair (e.g. ``order_id``, ``customer_name``) without code changes.
"""

import uuid
from typing import Any, Dict, List, Optional
from collections import defaultdict

from app.core.security import derive_allowlists_from_registry
from app.schemas.tools import CompareRecordsInput, CompareRecordsOutput
from app.services.execution_context import AuditExecutionContext


def _present(records: List[Dict[str, Any]], field: str) -> bool:
    return any(field in record for record in records)


def _require_field(records: List[Dict[str, Any]], field: str, side: str) -> None:
    if not _present(records, field):
        available = sorted({key for record in records for key in record})
        raise ValueError(
            f"Field '{field}' does not exist on the {side} dataset. Available fields: {available}."
        )


def compare_records_handler(
    input_data: CompareRecordsInput,
    context: AuditExecutionContext,
) -> CompareRecordsOutput:
    """Execute deterministic record matching between two datasets."""
    left_dataset = context.get_dataset(input_data.left_dataset_reference)
    if left_dataset is None:
        raise ValueError(f"Dataset reference '{input_data.left_dataset_reference}' not found in execution context.")

    right_dataset = context.get_dataset(input_data.right_dataset_reference)
    if right_dataset is None:
        raise ValueError(f"Dataset reference '{input_data.right_dataset_reference}' not found in execution context.")

    derive_allowlists_from_registry()
    match = input_data.match_configuration

    # Both sides must actually carry the requested fields - fail loudly rather than
    # silently producing an all-missing result (R7).
    _require_field(left_dataset, match.left_field, "left")
    _require_field(right_dataset, match.right_field, "right")
    _require_field(left_dataset, match.left_id_field, "left")
    _require_field(right_dataset, match.right_id_field, "right")
    if match.fallback_field:
        _require_field(left_dataset, match.fallback_field, "left")
        if not match.fallback_right_field:
            raise ValueError("fallback_field requires fallback_right_field.")
        _require_field(right_dataset, match.fallback_right_field, "right")

    key_of = _key

    # Index the right dataset by the primary match field and by its identifier.
    right_by_match_field: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    right_by_id: Dict[str, Dict[str, Any]] = {}
    for row in right_dataset:
        value = key_of(row.get(match.right_field))
        if value is not None:
            right_by_match_field[value].append(row)
        id_value = key_of(row.get(match.right_id_field))
        if id_value is not None:
            right_by_id[id_value] = row

    matched_pairs: List[Dict[str, Any]] = []
    missing_pos: List[Dict[str, Any]] = []
    ambiguous_matches: List[Dict[str, Any]] = []
    matched_right_ids = set()

    for left_row in left_dataset:
        matches = list(right_by_match_field.get(key_of(left_row.get(match.left_field)) or "", []))

        # Secondary match when the primary field found nothing.
        if not matches and match.fallback_field and match.fallback_right_field:
            fallback_value = key_of(left_row.get(match.fallback_field))
            if fallback_value is not None:
                candidate = right_by_id.get(fallback_value)
                if candidate is not None:
                    matches = [candidate]

        left_id = key_of(left_row.get(match.left_id_field))
        if len(matches) == 1:
            right_row = matches[0]
            right_id = key_of(right_row.get(match.right_id_field))
            matched_right_ids.add(right_id)
            matched_pairs.append(
                {
                    "left": left_row,
                    "right": right_row,
                    "left_id": left_row.get(match.left_id_field),
                    "right_id": right_row.get(match.right_id_field),
                    "left_value": left_row.get(match.left_field),
                    "right_value": right_row.get(match.right_field),
                }
            )
        elif len(matches) > 1:
            ambiguous_matches.append(
                {
                    "left": left_row,
                    "matching": matches,
                    "left_id": left_row.get(match.left_id_field),
                    "left_value": left_row.get(match.left_field),
                }
            )
            for right_row in matches:
                matched_right_ids.add(key_of(right_row.get(match.right_id_field)))
        else:
            missing_pos.append(
                {
                    "left": left_row,
                    "left_id": left_row.get(match.left_id_field),
                    "left_value": left_row.get(match.left_field),
                }
            )

    unmatched_pos = [
        row
        for row in right_dataset
        if key_of(row.get(match.right_id_field)) not in matched_right_ids
    ]

    comparison_reference = f"comp_{uuid.uuid4().hex[:6]}"
    comparison_data: Dict[str, Any] = {
        "comparison_reference": comparison_reference,
        "matched_pairs": matched_pairs,
        "missing_pos": missing_pos,
        "ambiguous_matches": ambiguous_matches,
        "unmatched_pos": unmatched_pos,
        "left_dataset_reference": input_data.left_dataset_reference,
        "right_dataset_reference": input_data.right_dataset_reference,
        "match_configuration": match.model_dump(),
        "left_id_field": match.left_id_field,
        "right_id_field": match.right_id_field,
        "left_amount_field": _detect_amount_field(left_dataset),
        "right_amount_field": _detect_amount_field(right_dataset),
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
            "left_records_evaluated": len(left_dataset),
            "right_records_evaluated": len(right_dataset),
            "matched_records": len(matched_pairs),
            "left_records_without_counterpart": len(missing_pos),
            "ambiguous_records": len(ambiguous_matches),
            "left_id_field": match.left_id_field,
            "right_id_field": match.right_id_field,
        },
        match_configuration=match.model_dump(),
    )


def _key(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _detect_amount_field(records: List[Dict[str, Any]]) -> Optional[str]:
    """Infer the monetary column on a dataset by well-known naming, for reporting only."""
    for candidate in ("order_amount", "po_amount", "amount", "value", "total_amount"):
        if _present(records, candidate):
            return candidate
    return None
