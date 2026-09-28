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
from app.models.financial import (
    OrderIntake,
    OrderIntakeDebooking,
    PurchaseOrder,
    WBSElement,
    SCRMOpportunity,
    YRARevenueRecord,
    YCACostRecord,
)

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

    if year == 2026:
        # Additional records for Q2 and Q3 so that UI audit runs across P7-P9 and Week to date
        # have populated populations, while preserving the exact 80 records in Q1 for test assertions.
        base_q2 = date(year, 4, 6)
        base_q3 = date(year, 7, 6)
        today = date.today()
        current_week_start = (today - timedelta(days=today.weekday())) if today.year == year else date(year, 9, 21)

        for i in range(81, 141):
            order_id = f"ORD-{year * 1000 + i}"
            customer = CUSTOMERS[(i * 3) % len(CUSTOMERS)]
            base_amount = round(random.uniform(20000.0, 180000.0), 2)
            po_id = f"PO-{(year % 100) * 10000 + 8000 + i}"
            vendor = VENDORS[i % len(VENDORS)]

            if i <= 105:
                # Q2 records (April - June)
                order_date = base_q2 + timedelta(days=(i - 81) * 3)
            elif i <= 130:
                # Early/Mid Q3 records (July - August)
                order_date = base_q3 + timedelta(days=(i - 106) * 2)
            else:
                # Late September 2026 / current week records (Sep 21 - Sep 25)
                order_date = current_week_start + timedelta(days=(i - 131) % 5)

            orders.append(
                OrderIntake(
                    order_id=order_id,
                    customer_name=customer,
                    order_date=order_date,
                    order_amount=base_amount,
                    currency="USD",
                    po_reference=po_id,
                    source_system="SAP_ECC",
                    status="BOOKED",
                )
            )
            pos.append(
                PurchaseOrder(
                    po_id=po_id,
                    order_id=order_id,
                    vendor_id=vendor,
                    po_date=order_date - timedelta(days=2),
                    po_amount=base_amount,
                    currency="USD",
                    source_system="RED_BOX_PO",
                    status="APPROVED",
                )
            )

    return orders, pos


def _generate_debookings_for_year(year: int, order_ids_by_customer):
    """Build Order Intake debookings with a deterministic, categorised scenario mix.

    Debookings reverse bookings that ``_generate_dataset_for_year`` already produced, so the
    reverse always references a real ``order_id``. The mix is chosen to exercise every
    recognition-quality test:

    * same-quarter reversals for a handful of "noisy" customers (booking-quality signal)
    * reversals landing in a *later* quarter than the booking (premature recognition)
    * reason codes that mean the recognition was never supported
    * one debooking pointing at an order id that does not exist (orphan)
    """
    debookings = []

    def add(seq, order_id, customer, booking_date, debooking_date, amount, reason, status="POSTED"):
        debookings.append(
            OrderIntakeDebooking(
                debooking_id=f"DBK-{year}-{seq:04d}",
                order_id=order_id,
                customer_name=customer,
                booking_date=booking_date,
                debooking_date=debooking_date,
                debooking_amount=round(abs(amount), 2),
                currency="USD",
                reason_code=reason,
                source_system="SAP_ECC",
                status=status,
            )
        )

    q1_start = date(year, 1, 1)
    q1_end = date(year, 3, 31)
    q2_start = date(year, 4, 1)
    q2_end = date(year, 6, 30)

    def orders_for(customer, limit=6):
        return order_ids_by_customer.get(customer, [])[:limit]

    seq = 0

    # 1. Same-quarter reversals, "noisy" customers -> high debooking ratio for the quarter.
    #    Reason PRICE_CORRECTION is commercially benign, so these should surface as a
    #    BOOKING_QUALITY concentration finding rather than a recognition failure.
    noisy_customers = [CUSTOMERS[0], CUSTOMERS[3], CUSTOMERS[7], CUSTOMERS[11]]
    for customer in noisy_customers:
        for order_id in orders_for(customer, 5):
            seq += 1
            booking_date = q1_start + timedelta(days=(seq * 5) % 55)
            # Same quarter: debooked within the same 90 days.
            debooking_date = booking_date + timedelta(days=10 + (seq % 12))
            if debooking_date > q1_end:
                debooking_date = q1_end - timedelta(days=seq % 5)
            add(seq, order_id, customer, booking_date, debooking_date, 15000 + seq * 900, "PRICE_CORRECTION")

    # 2. Cross-quarter reversals -> revenue was recognised in an earlier period than the
    #    reversal. This is the premature-recognition population.
    for customer in [CUSTOMERS[1], CUSTOMERS[5], CUSTOMERS[9], CUSTOMERS[13], CUSTOMERS[17]]:
        for order_id in orders_for(customer, 4):
            seq += 1
            booking_date = q1_start + timedelta(days=(seq * 7) % 70)
            debooking_date = q2_start + timedelta(days=(seq * 3) % 80)
            add(seq, order_id, customer, booking_date, debooking_date, 22000 + seq * 1500, "TIMING_SHIFT")

    # 3. Recognition that was never supported.
    unsupported = [
        ("NO_CONTRACT", CUSTOMERS[2], 2),
        ("CUSTOMER_CANCELLED", CUSTOMERS[6], 2),
        ("DUPLICATE_BOOKING", CUSTOMERS[8], 2),
        ("CREDIT_NOTE", CUSTOMERS[10], 2),
        ("WRONG_CUSTOMER", CUSTOMERS[15], 1),
    ]
    for reason, customer, count in unsupported:
        for order_id in orders_for(customer, count + 1):
            seq += 1
            booking_date = q1_start + timedelta(days=(seq * 11) % 75)
            # Same quarter, so the *reason* is the only signal here.
            debooking_date = booking_date + timedelta(days=6 + (seq % 20))
            if debooking_date > q1_end:
                debooking_date = q1_end - timedelta(days=seq % 6)
            add(seq, order_id, customer, booking_date, debooking_date, 31000 + seq * 2100, reason)

    # 4. Orphans: a reversal with no matching order intake record.
    for i in range(2):
        seq += 1
        booking_date = q1_start + timedelta(days=(seq * 9) % 60)
        debooking_date = booking_date + timedelta(days=4 + i)
        add(
            seq,
            f"ORD-{year * 1000 + 9000 + i}",
            CUSTOMERS[19],
            booking_date,
            debooking_date,
            45000 + i * 5000,
            "PRICE_CORRECTION",
        )

    # 5. Healthy customers: a single benign same-quarter correction, so the run shows
    #    contrast rather than flagging everything.
    for customer in [CUSTOMERS[4], CUSTOMERS[12], CUSTOMERS[16], CUSTOMERS[18]]:
        order_id = orders_for(customer, 1)
        if not order_id:
            continue
        seq += 1
        booking_date = q1_start + timedelta(days=(seq * 13) % 65)
        debooking_date = booking_date + timedelta(days=5)
        add(seq, order_id[0], customer, booking_date, debooking_date, 9000 + seq * 400, "PRICE_CORRECTION")

    if year == 2026:
        # Add Q3 debookings including customer cancellations exceeding 10% for specific customers
        q3_cancelled_customers = [CUSTOMERS[6], CUSTOMERS[2]]  # Global Trade Dynamics, Blue Horizon Ltd
        today = date.today()
        cw_start = (today - timedelta(days=today.weekday())) if today.year == year else date(year, 9, 21)
        for customer in q3_cancelled_customers:
            cust_orders = orders_for(customer, 4)
            for ord_id in cust_orders:
                seq += 1
                booking_date = cw_start + timedelta(days=(seq % 3))
                debooking_date = booking_date + timedelta(days=2)
                add(seq, ord_id, customer, booking_date, debooking_date, 35000.0, "CUSTOMER_CANCELLED")

        # Some benign price corrections in Q3
        for customer in [CUSTOMERS[0], CUSTOMERS[4]]:
            cust_orders = orders_for(customer, 2)
            for ord_id in cust_orders:
                seq += 1
                booking_date = date(year, 9, 15)
                debooking_date = date(year, 9, 22)
                add(seq, ord_id, customer, booking_date, debooking_date, 5000.0, "PRICE_CORRECTION")

    return debookings


def _apply_wbs_scenarios(orders, pos):
    """Decorate the existing 2026 Q1 orders and POs with deterministic WBS integrity scenarios.
    
    This preserves the exact 80 Q1 orders, 74 POs, and 8 missing PO cases required by existing tests,
    while attaching rich multi-opportunity and WBS commingling structures.
    """
    # 1. Clean WBS: WBS-PRJ-001 (Cyberdyne Systems)
    orders[0].wbs_element = "WBS-PRJ-001"
    orders[0].opportunity_id = "OPP-2026-001"
    pos[0].wbs_element = "WBS-PRJ-001"
    pos[0].opportunity_id = "OPP-2026-001"

    # 2. Multi-Opportunity Commingled WBS: WBS-PRJ-COMMINGLED-02 (Hyperion Enterprises - 2 opps)
    orders[1].wbs_element = "WBS-PRJ-COMMINGLED-02"
    orders[1].opportunity_id = "OPP-2026-002A"
    pos[1].wbs_element = "WBS-PRJ-COMMINGLED-02"
    pos[1].opportunity_id = "OPP-2026-002A"
    orders[5].wbs_element = "WBS-PRJ-COMMINGLED-02"
    orders[5].opportunity_id = "OPP-2026-002B"
    pos[5].wbs_element = "WBS-PRJ-COMMINGLED-02"
    pos[5].opportunity_id = "OPP-2026-002B"

    # 3. Multi-Customer Commingled WBS: WBS-PRJ-MULTICUST-03 (Starlight Media & Global Trade Dynamics)
    orders[2].wbs_element = "WBS-PRJ-MULTICUST-03"
    orders[2].opportunity_id = "OPP-2026-003"
    pos[2].wbs_element = "WBS-PRJ-MULTICUST-03"
    pos[2].opportunity_id = "OPP-2026-003"
    orders[3].wbs_element = "WBS-PRJ-MULTICUST-03"
    orders[3].opportunity_id = "OPP-2026-003"
    pos[3].wbs_element = "WBS-PRJ-MULTICUST-03"
    pos[3].opportunity_id = "OPP-2026-003"

    # 4. Uncovered WBS (Missing Customer PO): WBS-PRJ-UNCOVERED-04 (Initech Software - from 71..78 missing PO pool)
    orders[70].wbs_element = "WBS-PRJ-UNCOVERED-04"
    orders[70].opportunity_id = "OPP-2026-004"

    # 5. Revenue Over-Recognition Beyond 5% Tolerance: WBS-PRJ-REVEXCEED-05 (Radiant Networks)
    orders[4].wbs_element = "WBS-PRJ-REVEXCEED-05"
    orders[4].opportunity_id = "OPP-2026-005"
    pos[4].wbs_element = "WBS-PRJ-REVEXCEED-05"
    pos[4].opportunity_id = "OPP-2026-005"

    # 6. Orphan Cost Parking WBS: WBS-PRJ-ORPHAN-06 (No orders or POs assigned)

    # 7. Minor Revenue Within Tolerance: WBS-PRJ-TOLERANCE-07 (Futura Tech)
    orders[6].wbs_element = "WBS-PRJ-TOLERANCE-07"
    orders[6].opportunity_id = "OPP-2026-007"
    pos[6].wbs_element = "WBS-PRJ-TOLERANCE-07"
    pos[6].opportunity_id = "OPP-2026-007"


def _generate_wbs_data():
    """Generate realistic, deterministic WBS elements, SCRM opportunities, YRA revenue, and YCA costs."""
    wbs_list = []
    opp_list = []
    yra_list = []
    yca_list = []

    # 1. Clean WBS: WBS-PRJ-001 (Cyberdyne Systems)
    wbs_list.append(
        WBSElement(
            wbs_code="WBS-PRJ-001",
            project_name="Cloud Modernization Enterprise",
            responsible_person="Sarah Jenkins",
            customer_name="Cyberdyne Systems",
            budget_amount=500000.0,
            status="ACTIVE",
            created_date=date(2026, 1, 10),
            source_system="SAP_ECC",
        )
    )
    opp_list.append(
        SCRMOpportunity(
            opportunity_id="OPP-2026-001",
            contract_id="SCRM-CN-1001",
            opportunity_name="Cyberdyne Cloud Phase 1",
            customer_name="Cyberdyne Systems",
            planned_value=25000.0,
            status="WON",
            close_date=date(2026, 1, 5),
            source_system="SCRM",
        )
    )
    yra_list.append(
        YRARevenueRecord(
            revenue_id="YRA-REV-001",
            wbs_element="WBS-PRJ-001",
            opportunity_id="OPP-2026-001",
            billing_doc="INV-9001",
            customer_name="Cyberdyne Systems",
            revenue_date=date(2026, 2, 20),
            revenue_amount=20000.0,
            currency="USD",
            fiscal_period="2026-Q1",
            status="POSTED",
            source_system="SAP_ECC",
        )
    )
    yca_list.append(
        YCACostRecord(
            cost_id="YCA-CST-001",
            wbs_element="WBS-PRJ-001",
            cost_element="LABOR",
            vendor_or_person="Consulting Team Alpha",
            cost_date=date(2026, 2, 15),
            cost_amount=15000.0,
            currency="USD",
            fiscal_period="2026-Q1",
            status="POSTED",
            source_system="SAP_ECC",
        )
    )

    # 2. Multi-Opportunity Commingled WBS: WBS-PRJ-COMMINGLED-02 (2 distinct SCRM opportunities on 1 WBS)
    wbs_list.append(
        WBSElement(
            wbs_code="WBS-PRJ-COMMINGLED-02",
            project_name="Shared Core Infrastructure Rollout",
            responsible_person="Michael Chang",
            customer_name="Hyperion Enterprises",
            budget_amount=800000.0,
            status="ACTIVE",
            created_date=date(2026, 1, 10),
            source_system="SAP_ECC",
        )
    )
    opp_list.extend([
        SCRMOpportunity(
            opportunity_id="OPP-2026-002A",
            contract_id="SCRM-CN-2001",
            opportunity_name="Hyperion ERP Module Upgrade",
            customer_name="Hyperion Enterprises",
            planned_value=50000.0,
            status="WON",
            close_date=date(2026, 1, 8),
            source_system="SCRM",
        ),
        SCRMOpportunity(
            opportunity_id="OPP-2026-002B",
            contract_id="SCRM-CN-2002",
            opportunity_name="Hyperion Analytics Platform",
            customer_name="Hyperion Enterprises",
            planned_value=120000.0,
            status="WON",
            close_date=date(2026, 1, 14),
            source_system="SCRM",
        ),
    ])
    yra_list.append(
        YRARevenueRecord(
            revenue_id="YRA-REV-002",
            wbs_element="WBS-PRJ-COMMINGLED-02",
            opportunity_id="OPP-2026-002A",
            billing_doc="INV-9002",
            customer_name="Hyperion Enterprises",
            revenue_date=date(2026, 2, 28),
            revenue_amount=40000.0,
            currency="USD",
            fiscal_period="2026-Q1",
            status="POSTED",
            source_system="SAP_ECC",
        )
    )

    # 3. Multi-Customer Commingled WBS: WBS-PRJ-MULTICUST-03 (Starlight Media & Global Trade Dynamics)
    wbs_list.append(
        WBSElement(
            wbs_code="WBS-PRJ-MULTICUST-03",
            project_name="Shared Data Gateway Service",
            responsible_person="David Miller",
            customer_name="Starlight Media",
            budget_amount=600000.0,
            status="ACTIVE",
            created_date=date(2026, 1, 10),
            source_system="SAP_ECC",
        )
    )
    opp_list.append(
        SCRMOpportunity(
            opportunity_id="OPP-2026-003",
            contract_id="SCRM-CN-3001",
            opportunity_name="Cross-Org Data Gateway",
            customer_name="Starlight Media",
            planned_value=114000.0,
            status="WON",
            close_date=date(2026, 1, 10),
            source_system="SCRM",
        )
    )

    # 4. Uncovered WBS (Missing Customer PO): WBS-PRJ-UNCOVERED-04
    wbs_list.append(
        WBSElement(
            wbs_code="WBS-PRJ-UNCOVERED-04",
            project_name="AI Pilot Exploration",
            responsible_person="Elena Rostova",
            customer_name="Initech Software",
            budget_amount=200000.0,
            status="ACTIVE",
            created_date=date(2026, 1, 10),
            source_system="SAP_ECC",
        )
    )
    opp_list.append(
        SCRMOpportunity(
            opportunity_id="OPP-2026-004",
            contract_id="SCRM-CN-4001",
            opportunity_name="Initech AI Pilot",
            customer_name="Initech Software",
            planned_value=40000.0,
            status="WON",
            close_date=date(2026, 1, 10),
            source_system="SCRM",
        )
    )
    yra_list.append(
        YRARevenueRecord(
            revenue_id="YRA-REV-004",
            wbs_element="WBS-PRJ-UNCOVERED-04",
            opportunity_id="OPP-2026-004",
            billing_doc="INV-9004",
            customer_name="Initech Software",
            revenue_date=date(2026, 2, 25),
            revenue_amount=30000.0,
            currency="USD",
            fiscal_period="2026-Q1",
            status="POSTED",
            source_system="SAP_ECC",
        )
    )

    # 5. Revenue Over-Recognition Beyond 5% Tolerance: WBS-PRJ-REVEXCEED-05
    wbs_list.append(
        WBSElement(
            wbs_code="WBS-PRJ-REVEXCEED-05",
            project_name="Network Security Hardening",
            responsible_person="Alex Turner",
            customer_name="Radiant Networks",
            budget_amount=250000.0,
            status="ACTIVE",
            created_date=date(2026, 1, 10),
            source_system="SAP_ECC",
        )
    )
    opp_list.append(
        SCRMOpportunity(
            opportunity_id="OPP-2026-005",
            contract_id="SCRM-CN-5001",
            opportunity_name="Radiant SecOps Hardening",
            customer_name="Radiant Networks",
            planned_value=60000.0,
            status="WON",
            close_date=date(2026, 1, 10),
            source_system="SCRM",
        )
    )
    # Booked OI is $61,726.85. 5% tolerance + $5,000 = $69,813.19 limit. YRA revenue is $95,000.
    yra_list.append(
        YRARevenueRecord(
            revenue_id="YRA-REV-005",
            wbs_element="WBS-PRJ-REVEXCEED-05",
            opportunity_id="OPP-2026-005",
            billing_doc="INV-9005",
            customer_name="Radiant Networks",
            revenue_date=date(2026, 3, 1),
            revenue_amount=95000.0,
            currency="USD",
            fiscal_period="2026-Q1",
            status="POSTED",
            source_system="SAP_ECC",
        )
    )

    # 6. Orphan Cost Parking WBS: WBS-PRJ-ORPHAN-06 (YCA costs incurred, zero OI, zero Opp)
    wbs_list.append(
        WBSElement(
            wbs_code="WBS-PRJ-ORPHAN-06",
            project_name="Internal Innovation Reserve",
            responsible_person="Rachel Green",
            customer_name=None,
            budget_amount=100000.0,
            status="ACTIVE",
            created_date=date(2026, 1, 5),
            source_system="SAP_ECC",
        )
    )
    yca_list.append(
        YCACostRecord(
            cost_id="YCA-CST-006",
            wbs_element="WBS-PRJ-ORPHAN-06",
            cost_element="SUBCONTRACT",
            vendor_or_person="Subcontractor Beta",
            cost_date=date(2026, 2, 10),
            cost_amount=85000.0,
            currency="USD",
            fiscal_period="2026-Q1",
            status="POSTED",
            source_system="SAP_ECC",
        )
    )

    # 7. Minor Revenue Within Tolerance: WBS-PRJ-TOLERANCE-07 (YRA exceeds OI within 5% tolerance)
    wbs_list.append(
        WBSElement(
            wbs_code="WBS-PRJ-TOLERANCE-07",
            project_name="Enterprise CRM Migration",
            responsible_person="James Wilson",
            customer_name="Futura Tech",
            budget_amount=300000.0,
            status="ACTIVE",
            created_date=date(2026, 1, 10),
            source_system="SAP_ECC",
        )
    )
    opp_list.append(
        SCRMOpportunity(
            opportunity_id="OPP-2026-007",
            contract_id="SCRM-CN-7001",
            opportunity_name="Futura Migration",
            customer_name="Futura Tech",
            planned_value=175000.0,
            status="WON",
            close_date=date(2026, 1, 10),
            source_system="SCRM",
        )
    )
    # OI = $179,062.76. Limit = $179,062.76 * 1.05 + $5,000 = $193,015.90. YRA = $182,000.0
    yra_list.append(
        YRARevenueRecord(
            revenue_id="YRA-REV-007",
            wbs_element="WBS-PRJ-TOLERANCE-07",
            opportunity_id="OPP-2026-007",
            billing_doc="INV-9007",
            customer_name="Futura Tech",
            revenue_date=date(2026, 2, 28),
            revenue_amount=182000.0,
            currency="USD",
            fiscal_period="2026-Q1",
            status="POSTED",
            source_system="SAP_ECC",
        )
    )

    return wbs_list, opp_list, yra_list, yca_list


def seed_financial_data(db: Session, record_count: int = 80) -> None:
    """Generate deterministic order intake, purchase order and debooking records.

    Debookings are generated per year against that year's order intake population, so every
    reversal references a real order except the deliberate orphans.
    """
    random.seed(42)  # Fixed seed for strict determinism

    # Clear existing financial records
    db.query(YCACostRecord).delete()
    db.query(YRARevenueRecord).delete()
    db.query(SCRMOpportunity).delete()
    db.query(WBSElement).delete()
    db.query(OrderIntakeDebooking).delete()
    db.query(OrderIntake).delete()
    db.query(PurchaseOrder).delete()
    db.commit()

    all_orders = []
    all_pos = []
    all_debookings = []

    for year in (2026, 2024):
        orders, pos = _generate_dataset_for_year(year, record_count=record_count)
        if year == 2026:
            _apply_wbs_scenarios(orders, pos)
        all_orders.extend(orders)
        all_pos.extend(pos)
        by_customer = {}
        for order in orders:
            by_customer.setdefault(order.customer_name, []).append(order.order_id)
        all_debookings.extend(_generate_debookings_for_year(year, by_customer))

    # Add WBS and SCRM Opportunity population
    wbs_elements, opps, yra_records, yca_records = _generate_wbs_data()

    db.add_all(wbs_elements)
    db.add_all(opps)
    db.add_all(all_orders)
    db.add_all(all_pos)
    db.add_all(all_debookings)
    db.add_all(yra_records)
    db.add_all(yca_records)
    db.commit()

    print_summary(db, len(all_orders), len(all_pos), len(all_debookings))


def print_summary(db: Session, orders: int, pos: int, debookings: int = 0) -> None:
    """Report the seeded population, broken down by the data source that owns it."""
    print(
        f"Successfully seeded {orders} Order Intake records, {pos} Purchase Orders and "
        f"{debookings} Order Intake debookings across 2024 & 2026."
    )

    for source in sorted({o.source_system for o in db.query(OrderIntake).all()}):
        count = db.query(OrderIntake).filter(OrderIntake.source_system == source).count()
        print(f"  order_intake.source_system        = {source:<12} -> {count} records")
    for source in sorted({p.source_system for p in db.query(PurchaseOrder).all()}):
        count = db.query(PurchaseOrder).filter(PurchaseOrder.source_system == source).count()
        print(f"  purchase_orders.source_system     = {source:<12} -> {count} records")
    for source in sorted({d.source_system for d in db.query(OrderIntakeDebooking).all()}):
        count = db.query(OrderIntakeDebooking).filter(
            OrderIntakeDebooking.source_system == source
        ).count()
        print(f"  order_intake_debookings.source    = {source:<12} -> {count} records")
    for source in sorted({w.source_system for w in db.query(WBSElement).all()}):
        count = db.query(WBSElement).filter(WBSElement.source_system == source).count()
        print(f"  wbs_elements.source_system        = {source:<12} -> {count} records")
    for source in sorted({s.source_system for s in db.query(SCRMOpportunity).all()}):
        count = db.query(SCRMOpportunity).filter(SCRMOpportunity.source_system == source).count()
        print(f"  scrm_opportunities.source_system  = {source:<12} -> {count} records")
    for source in sorted({y.source_system for y in db.query(YRARevenueRecord).all()}):
        count = db.query(YRARevenueRecord).filter(YRARevenueRecord.source_system == source).count()
        print(f"  yra_revenue_records.source_system = {source:<12} -> {count} records")
    for source in sorted({c.source_system for c in db.query(YCACostRecord).all()}):
        count = db.query(YCACostRecord).filter(YCACostRecord.source_system == source).count()
        print(f"  yca_cost_records.source_system    = {source:<12} -> {count} records")

    unmatched = db.query(OrderIntake).filter(OrderIntake.po_reference.is_(None)).count()
    print(f"  orders with no PO reference (expected MISSING_PO population): {unmatched}")

    reasons = db.query(OrderIntakeDebooking.reason_code).all()
    if reasons:
        from collections import Counter

        counts = Counter(r[0] for r in reasons)
        print("  debooking reason codes: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def verify_seeded_data(db: Session) -> bool:
    """Sanity-check that every entity is populated under the expected source codes."""
    problems = []
    for model, source in (
        (OrderIntake, "SAP_ECC"),
        (PurchaseOrder, "RED_BOX_PO"),
        (OrderIntakeDebooking, "SAP_ECC"),
        (WBSElement, "SAP_ECC"),
        (SCRMOpportunity, "SCRM"),
        (YRARevenueRecord, "SAP_ECC"),
        (YCACostRecord, "SAP_ECC"),
    ):
        count = db.query(model).filter(model.source_system == source).count()
        if count == 0:
            problems.append(f"No {model.__tablename__} records for source {source}.")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return False
    print(
        "Seed verification passed: order intake, purchase orders and debookings are populated "
        "under their bound source codes."
    )
    return True



if __name__ == "__main__":
    init_db()
    session = SessionLocal()
    try:
        seed_financial_data(session)
        verify_seeded_data(session)
    finally:
        session.close()
