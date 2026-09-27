"""Tool 1: fetch_financial_data.

Extracts records for a registered business entity from a catalog data source. The read target
is resolved through the capability registry, so the tool body is generic: adding a new source
table requires a model, an ``EntityDescriptor`` and a binding, with no change here.
"""

import uuid
from typing import Any, Dict, List, Optional

from app.core.security import derive_allowlists_from_registry
from app.schemas.tools import FetchFinancialDataInput, FetchFinancialDataOutput
from app.services.data_source_registry import EntityDescriptor, entity_registry
from app.services.execution_context import AuditExecutionContext

#: Filters that are never pushed down to the query, because the plan binds them at run time.
_RESERVED_FILTER_KEYS = {"start_date", "end_date", "source_system", "data_source_code"}


def _resolve_entity(input_data: FetchFinancialDataInput) -> EntityDescriptor:
    """Resolve the entity descriptor, refusing anything outside the registry (R7)."""
    try:
        return entity_registry.get_entity(input_data.entity_code)
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def _validate_binding(descriptor: EntityDescriptor, data_source_code: str) -> None:
    """Refuse to read an entity from a source it is not bound to (R8)."""
    bound = entity_registry.bound_entity_codes(data_source_code)
    if descriptor.entity_code not in bound:
        raise ValueError(
            f"Data source '{data_source_code}' does not expose entity "
            f"'{descriptor.entity_code}'. Exposed entities: {bound or 'none'}."
        )


def _build_filters(
    descriptor: EntityDescriptor, input_data: FetchFinancialDataInput, context: AuditExecutionContext
) -> Dict[str, Any]:
    """Assemble query filters from plan-bound filters plus the run's customer filter."""
    filters: Dict[str, Any] = {
        k: v for k, v in (input_data.filters or {}).items() if k not in _RESERVED_FILTER_KEYS
    }

    # The audit run's customer filter applies only when the entity actually has that column.
    if "customer_name" in descriptor.column_names:
        raw_customer = filters.get("customer_name") or context.customer_filter
        customer = _clean_customer(raw_customer)
        if customer:
            filters["customer_name"] = customer
        else:
            filters.pop("customer_name", None)
    return filters


def _clean_customer(raw: Any) -> Optional[str]:
    if not raw:
        return None
    cleaned = str(raw).strip()
    if not cleaned or cleaned.lower() in ("string", "all", "none", "null", "undefined", ""):
        return None
    return cleaned


def _query_entity(
    db: Any,
    descriptor: EntityDescriptor,
    data_source_code: str,
    start_date: Any,
    end_date: Any,
    filters: Dict[str, Any],
    limit: int = 1000,
) -> List[Any]:
    """Run the descriptor-driven, allowlisted query for one entity."""
    from sqlalchemy import and_

    model = descriptor.model
    available = set(descriptor.column_names)
    unknown = sorted(set(filters) - available)
    if unknown:
        raise ValueError(
            f"Unsupported filter field(s) {unknown} for entity "
            f"'{descriptor.entity_code}'. Available fields: {sorted(available)}."
        )

    conditions = [
        getattr(model, descriptor.date_column) >= start_date,
        getattr(model, descriptor.date_column) <= end_date,
    ]
    if descriptor.discriminator_column:
        conditions.append(getattr(model, descriptor.discriminator_column) == data_source_code)

    for field_name, value in filters.items():
        column = getattr(model, field_name)
        conditions.append(column.ilike(f"%{value}%") if isinstance(value, str) else column == value)

    return (
        db.query(model)
        .filter(and_(*conditions))
        .order_by(getattr(model, descriptor.date_column).asc())
        .limit(limit)
        .all()
    )


def fetch_financial_data_handler(
    input_data: FetchFinancialDataInput,
    context: AuditExecutionContext,
) -> FetchFinancialDataOutput:
    """Execute a controlled, registry-governed data extraction."""
    derive_allowlists_from_registry()

    data_source_code = (input_data.data_source_code or "").strip().upper()
    descriptor = _resolve_entity(input_data)
    _validate_binding(descriptor, data_source_code)

    filters = _build_filters(descriptor, input_data, context)
    rows = _query_entity(
        db=context.db,
        descriptor=descriptor,
        data_source_code=data_source_code,
        start_date=input_data.start_date,
        end_date=input_data.end_date,
        filters=filters,
    )

    records = [descriptor.project(row) for row in rows]

    # Source-scoped reference so two sources exposing the same entity never collide.
    dataset_reference = f"ds_{descriptor.entity_code.lower()}_{data_source_code.lower()}_{uuid.uuid4().hex[:6]}"
    context.store_dataset(
        dataset_reference=dataset_reference,
        entity_name=descriptor.entity_code,
        source_system=data_source_code,
        records=records,
        alias=context.alias_for(data_source_code, descriptor.entity_code),
    )

    return FetchFinancialDataOutput(
        status="SUCCESS",
        dataset_reference=dataset_reference,
        entity=descriptor.entity_code,
        data_source_code=data_source_code,
        record_count=len(records),
        records_sample=records[:3],
        message=(
            f"Extracted {len(records)} {descriptor.entity_code} record(s) from "
            f"{data_source_code} for the audit window."
        ),
    )
