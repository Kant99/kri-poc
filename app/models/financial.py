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


#: Why an Order Intake booking was reversed. These drive the recognition-quality tests.
DEBOOKING_REASON_CODES = (
    "PRICE_CORRECTION",       # value corrected, original booking was arithmetically wrong
    "TIMING_SHIFT",            # moved to a different period
    "CUSTOMER_CANCELLED",      # order never stood up
    "DUPLICATE_BOOKING",       # the same demand was booked twice
    "NO_CONTRACT",             # recognised without an enforceable contract
    "CREDIT_NOTE",             # commercial credit given after recognition
    "WRONG_CUSTOMER",          # booked to the wrong customer
)


class OrderIntakeDebooking(Base):
    """Order Intake debookings (debit notes) reversing a previously booked order.

    A debooking is recognised by two independent dimensions:
      * ``booking_date`` / ``debooking_date`` - which quarter the demand was booked in and
        which quarter the reversal landed in. A reversal in a *later* quarter than the
        booking indicates the original recognition was pulled forward.
      * ``reason_code`` - why it was reversed, which distinguishes a commercial
        reclassification (price correction) from a recognition that was never supported.
    """
    __tablename__ = "order_intake_debookings"

    id = Column(Integer, primary_key=True, index=True)
    debooking_id = Column(String(100), unique=True, nullable=False, index=True)
    order_id = Column(String(100), nullable=False, index=True)  # the OI that was reversed
    customer_name = Column(String(255), nullable=False, index=True)
    booking_date = Column(Date, nullable=False, index=True)
    debooking_date = Column(Date, nullable=False, index=True)
    debooking_amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    reason_code = Column(String(50), default="PRICE_CORRECTION", nullable=False, index=True)
    source_system = Column(String(50), default="SAP_ECC", nullable=False, index=True)
    status = Column(String(50), default="POSTED", nullable=False)  # POSTED, DRAFT
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_debooking_customer_dates", "customer_name", "debooking_date"),
    )
