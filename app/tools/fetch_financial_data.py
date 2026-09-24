"""Tool 1: fetch_financial_data.

Fetches financial records (Order Intake or Purchase Orders) from approved source systems.
Stores full records in the scoped execution context and returns a compact dataset reference.
"""

import uuid
from typing import Any
from app.core.security import validate_source_system, validate_entity
from app.schemas.tools import FetchFinancialDataInput, FetchFinancialDataOutput
from app.services.execution_context import AuditExecutionContext


def fetch_financial_data_handler(
    input_data: FetchFinancialDataInput,
    context: AuditExecutionContext,
) -> FetchFinancialDataOutput:
    """Execute controlled data extraction against the financial repository."""
    source_sys = validate_source_system(input_data.source_system)
    entity = validate_entity(input_data.entity)

    records = []
    if entity == "ORDER_INTAKE":
        raw_cust = input_data.filters.get("customer_name") or context.customer_filter
        customer_filter = None
        if raw_cust:
            cleaned_cust = str(raw_cust).strip()
            if cleaned_cust and cleaned_cust.lower() not in ("string", "all", "none", "null", "undefined", ""):
                customer_filter = cleaned_cust

        orders = context.financial_repo.query_order_intake(
            start_date=input_data.start_date,
            end_date=input_data.end_date,
            source_system=source_sys,
            customer_name=customer_filter,
        )
        records = [
            {
                "order_id": o.order_id,
                "customer_name": o.customer_name,
                "order_date": o.order_date.isoformat(),
                "order_amount": float(o.order_amount),
                "currency": o.currency,
                "po_reference": o.po_reference,
                "source_system": o.source_system,
                "status": o.status,
            }
            for o in orders
        ]
        ref_prefix = "dataset_orders"
    elif entity == "PURCHASE_ORDER":
        pos = context.financial_repo.query_purchase_orders(
            start_date=input_data.start_date,
            end_date=input_data.end_date,
            source_system=source_sys,
        )
        records = [
            {
                "po_id": p.po_id,
                "order_id": p.order_id,
                "vendor_id": p.vendor_id,
                "po_date": p.po_date.isoformat(),
                "po_amount": float(p.po_amount),
                "currency": p.currency,
                "source_system": p.source_system,
                "status": p.status,
            }
            for p in pos
        ]
        ref_prefix = "dataset_pos"
    else:
        raise ValueError(f"Unsupported entity type: {entity}")

    dataset_reference = f"{ref_prefix}_{uuid.uuid4().hex[:6]}"
    context.store_dataset(
        dataset_reference=dataset_reference,
        entity_name=entity,
        source_system=source_sys,
        records=records,
    )

    sample = records[:3] if records else []
    return FetchFinancialDataOutput(
        status="SUCCESS",
        dataset_reference=dataset_reference,
        entity=entity,
        record_count=len(records),
        records_sample=sample,
        message=f"Successfully extracted {len(records)} records for entity {entity} from {source_sys}",
    )
