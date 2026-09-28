"""Database Reset and Seeding Utility Script.

Drops all tables, recreates the schema, seeds the KRI configuration (including data source
entity bindings) and the mock Order Intake / Purchase Order records, then verifies the result.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from app.core.database import Base, engine, SessionLocal
from app.services.data_source_bootstrap import register_all_entities
from scripts.seed_kri import seed_kri_configuration
from scripts.seed_data import seed_financial_data, verify_seeded_data
from app.models.kri import KRI, ProcessArea, DataSource, DataSourceEntity
from app.models.audit import ExecutionPlan
from app.models.financial import (
    OrderIntake,
    PurchaseOrder,
    OrderIntakeDebooking,
    WBSElement,
    SCRMOpportunity,
    YRARevenueRecord,
    YCACostRecord,
)


def reset_and_seed_database() -> None:
    """Drop, recreate, and seed the entire database."""
    register_all_entities()
    print("Resetting database tables...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("Tables recreated successfully.")

    db: Session = SessionLocal()
    try:
        print("\n--- 1. Seeding KRI Master Configuration ---")
        kri_id = seed_kri_configuration(db)

        print("\n--- 2. Seeding Mock Financial Transactions ---")
        seed_financial_data(db, record_count=80)

        print("\n--- 3. Verifying ---")
        verify_seeded_data(db)

        pa_count = db.query(ProcessArea).count()
        ds_count = db.query(DataSource).count()
        binding_count = db.query(DataSourceEntity).count()
        plan_count = db.query(ExecutionPlan).count()
        kri_count = db.query(KRI).count()
        orders_count = db.query(OrderIntake).count()
        pos_count = db.query(PurchaseOrder).count()
        wbs_count = db.query(WBSElement).count()
        opp_count = db.query(SCRMOpportunity).count()
        yra_count = db.query(YRARevenueRecord).count()
        yca_count = db.query(YCACostRecord).count()
        queryable = db.query(DataSource).filter(DataSource.is_queryable == True).count()

        print("\n==================================================")
        print("DATABASE INITIALIZATION & SEED SUMMARY")
        print("==================================================")
        print(f"Process Areas Seeded   : {pa_count}")
        print(f"Data Sources Seeded    : {ds_count} ({queryable} queryable, {binding_count} entity bindings)")
        print(f"KRIs Configured        : {kri_count} (Primary Active KRI ID: {kri_id})")
        print(f"Execution Plans Stored : {plan_count}")
        print(f"WBS Elements           : {wbs_count} (SAP ECC)")
        print(f"Commercial Opps (SCRM) : {opp_count} (SCRM)")
        print(f"Order Intake Records   : {orders_count} (SAP ECC)")
        print(f"Purchase Orders        : {pos_count} (Red Box PO)")
        print(f"YRA Revenue Records    : {yra_count} (SAP ECC)")
        print(f"YCA Cost Records       : {yca_count} (SAP ECC)")
        print("==================================================")
        print("Database is ready for audit execution!")

    finally:
        db.close()


if __name__ == "__main__":
    reset_and_seed_database()
