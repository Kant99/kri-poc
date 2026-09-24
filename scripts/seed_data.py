"""Deterministic Mock Financial Data Generator.

Generates 80 deterministic Order Intake records (SAP ECC) and matching Purchase Orders (Red Box PO)
using a fixed random seed to ensure complete test reproducibility across runs.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import random
from datetime import date, timedelta
from sqlalchemy.orm import Session
from app.core.database import SessionLocal, init_db
from app.models.financial import OrderIntake, PurchaseOrder

CUSTOMERS = [
    "Acme Global Corp",
    "Apex Industries",
    "Blue Horizon Ltd",
    "Cyberdyne Systems",
    "Echo Logistics",
    "Futura Tech",
    "Global Trade Dynamics",
    "Hyperion Enterprises",
    "Initech Software",
    "Jupiter Solutions",
    "Keystone Manufacturing",
    "Lumina Health",
    "Matrix Operations",
    "Nexus Distribution",
    "Omni Consumer Products",
    "Pinnacle Energy",
    "Quantum Dynamics",
    "Radiant Networks",
    "Starlight Media",
    "Titan Heavy Industries",
]

VENDORS = ["VEND-101", "VEND-102", "VEND-103", "VEND-104", "VEND-105"]


def _generate_dataset_for_year(year: int, record_count: int = 80):
    base_date = date(year, 1, 5)
    orders = []
    pos = []

    for i in range(1, record_count + 1):
        order_id = f"ORD-{year * 1000 + i}"
        customer = random.choice(CUSTOMERS)
        order_day_offset = (i * 2) % 80
        order_date = base_date + timedelta(days=order_day_offset)
        base_amount = round(random.uniform(15000.0, 250000.0), 2)
        po_id = f"PO-{(year % 100) * 10000 + 8000 + i}"
        po_reference = po_id
        vendor = random.choice(VENDORS)

        # Categorize into deterministic test scenarios:
        # 1..45 (45 records): Exact Matches
        if i <= 45:
            po_amount = base_amount
            orders.append(
                OrderIntake(
                    order_id=order_id,
                    customer_name=customer,
                    order_date=order_date,
                    order_amount=base_amount,
                    currency="USD",
                    po_reference=po_reference,
                    source_system="SAP_ECC",
                    status="BOOKED",
                )
            )
            pos.append(
                PurchaseOrder(
                    po_id=po_id,
                    order_id=order_id,
                    vendor_id=vendor,
                    po_date=order_date - timedelta(days=random.randint(1, 4)),
                    po_amount=po_amount,
                    currency="USD",
                    source_system="RED_BOX_PO",
                    status="APPROVED",
                )
            )

        # 46..60 (15 records): Minor Differences within 10% threshold (e.g. 2% to 8%)
        elif i <= 60:
            variance_pct = random.uniform(0.02, 0.08)
            direction = 1 if i % 2 == 0 else -1
            po_amount = round(base_amount * (1.0 + (direction * variance_pct)), 2)
            orders.append(
                OrderIntake(
                    order_id=order_id,
                    customer_name=customer,
                    order_date=order_date,
                    order_amount=base_amount,
                    currency="USD",
                    po_reference=po_reference,
                    source_system="SAP_ECC",
                    status="BOOKED",
                )
            )
            pos.append(
                PurchaseOrder(
                    po_id=po_id,
                    order_id=order_id,
                    vendor_id=vendor,
                    po_date=order_date - timedelta(days=random.randint(1, 4)),
                    po_amount=po_amount,
                    currency="USD",
                    source_system="RED_BOX_PO",
                    status="APPROVED",
                )
            )

        # 61..70 (10 records): Major Differences exceeding 10% threshold (15% to 75% mismatch) -> AMOUNT_MISMATCH
        elif i <= 70:
            variance_pct = random.uniform(0.15, 0.75)
            direction = 1 if i % 2 == 0 else -1
            po_amount = round(base_amount * (1.0 + (direction * variance_pct)), 2)
            orders.append(
                OrderIntake(
                    order_id=order_id,
                    customer_name=customer,
                    order_date=order_date,
                    order_amount=base_amount,
                    currency="USD",
                    po_reference=po_reference,
                    source_system="SAP_ECC",
                    status="BOOKED",
                )
            )
            pos.append(
                PurchaseOrder(
                    po_id=po_id,
                    order_id=order_id,
                    vendor_id=vendor,
                    po_date=order_date - timedelta(days=random.randint(1, 4)),
                    po_amount=po_amount,
                    currency="USD",
                    source_system="RED_BOX_PO",
                    status="APPROVED",
                )
            )

        # 71..78 (8 records): Missing POs in Red Box -> MISSING_PO
        elif i <= 78:
            orders.append(
                OrderIntake(
                    order_id=order_id,
                    customer_name=customer,
                    order_date=order_date,
                    order_amount=base_amount,
                    currency="USD",
                    po_reference=None,
                    source_system="SAP_ECC",
                    status="BOOKED",
                )
            )
            # No matching Purchase Order created!

        # 79..80 (2 records): Ambiguous / Duplicate Matches -> AMBIGUOUS_MATCH
        else:
            orders.append(
                OrderIntake(
                    order_id=order_id,
                    customer_name=customer,
                    order_date=order_date,
                    order_amount=base_amount,
                    currency="USD",
                    po_reference=f"PO-{po_id}-A",
                    source_system="SAP_ECC",
                    status="BOOKED",
                )
            )
            # Create two POs with same order_id
            pos.append(
                PurchaseOrder(
                    po_id=f"PO-{po_id}-A",
                    order_id=order_id,
                    vendor_id=vendor,
                    po_date=order_date - timedelta(days=2),
                    po_amount=round(base_amount * 0.6, 2),
                    currency="USD",
                    source_system="RED_BOX_PO",
                    status="APPROVED",
                )
            )
            pos.append(
                PurchaseOrder(
                    po_id=f"PO-{po_id}-B",
                    order_id=order_id,
                    vendor_id=vendor,
                    po_date=order_date - timedelta(days=1),
                    po_amount=round(base_amount * 0.5, 2),
                    currency="USD",
                    source_system="RED_BOX_PO",
                    status="APPROVED",
                )
            )

    return orders, pos


def seed_financial_data(db: Session, record_count: int = 80) -> None:
    """Generate deterministic order intake and PO records for both 2024 and 2026 windows."""
    random.seed(42)  # Fixed seed for strict determinism

    # Clear existing financial records
    db.query(OrderIntake).delete()
    db.query(PurchaseOrder).delete()
    db.commit()

    all_orders = []
    all_pos = []

    # Seed for 2026 (current benchmark)
    o26, p26 = _generate_dataset_for_year(2026, record_count=record_count)
    all_orders.extend(o26)
    all_pos.extend(p26)

    # Seed for 2024 (historical / swagger benchmark)
    o24, p24 = _generate_dataset_for_year(2024, record_count=record_count)
    all_orders.extend(o24)
    all_pos.extend(p24)

    db.add_all(all_orders)
    db.add_all(all_pos)
    db.commit()

    print(f"Successfully seeded {len(all_orders)} Order Intake records and {len(all_pos)} Purchase Orders across 2024 & 2026.")



if __name__ == "__main__":
    init_db()
    session = SessionLocal()
    try:
        seed_financial_data(session)
    finally:
        session.close()
