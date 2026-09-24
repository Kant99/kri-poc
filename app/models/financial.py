"""SQLAlchemy Models for Mock Financial Source Records (Order Intake and Purchase Orders)."""

import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Date,
    Index,
)
from app.core.database import Base


class OrderIntake(Base):
    """Customer Order Intake records (e.g. from SAP ECC)."""
    __tablename__ = "order_intake"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String(100), unique=True, nullable=False, index=True)
    customer_name = Column(String(255), nullable=False, index=True)
    order_date = Column(Date, nullable=False, index=True)
    order_amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    po_reference = Column(String(100), nullable=True, index=True)
    source_system = Column(String(50), default="SAP_ECC", nullable=False, index=True)
    status = Column(String(50), default="BOOKED", nullable=False)  # BOOKED, CANCELLED, PENDING
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_order_intake_date_system", "order_date", "source_system"),
    )


class PurchaseOrder(Base):
    """Purchase Order records (e.g. from Red Box PO)."""
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    po_id = Column(String(100), unique=True, nullable=False, index=True)
    order_id = Column(String(100), nullable=True, index=True)  # Matched or referenced order_id
    vendor_id = Column(String(100), nullable=False, index=True)
    po_date = Column(Date, nullable=False, index=True)
    po_amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    source_system = Column(String(50), default="RED_BOX_PO", nullable=False, index=True)
    status = Column(String(50), default="APPROVED", nullable=False)  # APPROVED, REJECTED, PENDING
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_po_date_system", "po_date", "source_system"),
    )
