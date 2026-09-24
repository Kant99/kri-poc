"""Controlled Repository for Querying Mock Financial Records."""

from datetime import date
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.models.financial import OrderIntake, PurchaseOrder


class FinancialRepository:
    """Provides controlled data access to financial entities."""

    def __init__(self, db: Session):
        self.db = db

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
