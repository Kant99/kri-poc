"""Controlled Repository for Querying Mock Financial Records."""

from datetime import date
from typing import List, Optional, Dict, Any, Sequence
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.models.financial import OrderIntake, OrderIntakeDebooking, PurchaseOrder


class FinancialRepository:
    """Provides controlled data access to financial entities.

    The descriptor-driven :meth:`query_entity` is the generic read path used by
    ``fetch_financial_data``; the two named methods remain as thin, typed conveniences.
    """

    def __init__(self, db: Session):
        self.db = db

    def query_entity(
        self,
        descriptor: Any,
        data_source_code: str,
        start_date: date,
        end_date: date,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 1000,
    ) -> List[Any]:
        """Query one entity through its descriptor, honouring the source discriminator."""
        model = descriptor.model
        available = set(descriptor.column_names)
        unknown = sorted(set(filters or {}) - available)
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

        for field_name, value in (filters or {}).items():
            column = getattr(model, field_name)
            conditions.append(column.ilike(f"%{value}%") if isinstance(value, str) else column == value)

        return (
            self.db.query(model)
            .filter(and_(*conditions))
            .order_by(getattr(model, descriptor.date_column).asc())
            .limit(limit)
            .all()
        )

    def query_by_source(
        self,
        descriptor: Any,
        data_source_code: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Any]:
        """Capability-manifest probe: does this source/entity combination actually hold data?"""
        model = descriptor.model
        query = self.db.query(model)
        if descriptor.discriminator_column:
            query = query.filter(getattr(model, descriptor.discriminator_column) == data_source_code)
        if start_date:
            query = query.filter(getattr(model, descriptor.date_column) >= start_date)
        if end_date:
            query = query.filter(getattr(model, descriptor.date_column) <= end_date)
        return query.all()

    def count_for_source(self, descriptor: Any, data_source_code: str) -> int:
        return len(self.query_by_source(descriptor, data_source_code))

    def query_order_intake(
        self,
        start_date: date,
        end_date: date,
        source_system: str = "SAP_ECC",
        customer_name: Optional[str] = None,
        limit: int = 1000,
    ) -> List[OrderIntake]:
        """Fetch order intake records within a date range with optional filters."""
        query = self.db.query(OrderIntake).filter(
            and_(
                OrderIntake.order_date >= start_date,
                OrderIntake.order_date <= end_date,
                OrderIntake.source_system == source_system,
            )
        )
        if customer_name:
            cleaned = str(customer_name).strip()
            if cleaned and cleaned.lower() not in ("string", "all", "none", "null", "undefined", ""):
                query = query.filter(OrderIntake.customer_name.ilike(f"%{cleaned}%"))

        return query.order_by(OrderIntake.order_date.asc()).limit(limit).all()

    def query_purchase_orders(
        self,
        start_date: date,
        end_date: date,
        source_system: str = "RED_BOX_PO",
        limit: int = 1000,
    ) -> List[PurchaseOrder]:
        """Fetch purchase orders within a date range."""
        query = self.db.query(PurchaseOrder).filter(
            and_(
                PurchaseOrder.po_date >= start_date,
                PurchaseOrder.po_date <= end_date,
                PurchaseOrder.source_system == source_system,
            )
        )
        return query.order_by(PurchaseOrder.po_date.asc()).limit(limit).all()

    def count_orders(self) -> int:
        return self.db.query(OrderIntake).count()

    def count_pos(self) -> int:
        return self.db.query(PurchaseOrder).count()

    def get_order_by_id(self, order_id: str) -> Optional[OrderIntake]:
        return self.db.query(OrderIntake).filter(OrderIntake.order_id == order_id).first()

    def get_po_by_id(self, po_id: str) -> Optional[PurchaseOrder]:
        return self.db.query(PurchaseOrder).filter(PurchaseOrder.po_id == po_id).first()

    def query_order_intake_debookings(
        self,
        start_date: date,
        end_date: date,
        source_system: str = "SAP_ECC",
        customer_name: Optional[str] = None,
        limit: int = 1000,
    ) -> List[OrderIntakeDebooking]:
        """Fetch order intake debookings within a date range with optional filters."""
        query = self.db.query(OrderIntakeDebooking).filter(
            and_(
                OrderIntakeDebooking.debooking_date >= start_date,
                OrderIntakeDebooking.debooking_date <= end_date,
                OrderIntakeDebooking.source_system == source_system,
            )
        )
        if customer_name:
            cleaned = str(customer_name).strip()
            if cleaned and cleaned.lower() not in ("string", "all", "none", "null", "undefined", ""):
                query = query.filter(OrderIntakeDebooking.customer_name.ilike(f"%{cleaned}%"))

        return query.order_by(OrderIntakeDebooking.debooking_date.asc()).limit(limit).all()

    def count_debookings(self) -> int:
        return self.db.query(OrderIntakeDebooking).count()

    def get_debooking_by_id(self, debooking_id: str) -> Optional[OrderIntakeDebooking]:
        return self.db.query(OrderIntakeDebooking).filter(OrderIntakeDebooking.debooking_id == debooking_id).first()

