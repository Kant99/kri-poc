"""Database Reset and Seeding Utility Script.

Drops all tables, recreates the schema, and seeds both KRI configuration and 80 mock records.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from app.core.database import Base, engine, SessionLocal
from scripts.seed_kri import seed_kri_configuration
from scripts.seed_data import seed_financial_data
from app.models.kri import KRI, ProcessArea, DataSource
from app.models.financial import OrderIntake, PurchaseOrder


def reset_and_seed_database() -> None:
    """Drop, recreate, and seed the entire database."""
    print("Resetting database tables...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("Tables recreated successfully.")

    db: Session = SessionLocal()
    try:
        print("\n--- 1. Seeding KRI Master Configuration ---")
        kri_id = seed_kri_configuration(db)

        print("\n--- 2. Seeding 80 Mock Financial Transactions ---")
        seed_financial_data(db, record_count=80)

        # Summary Verification
        pa_count = db.query(ProcessArea).count()
        ds_count = db.query(DataSource).count()
        kri_count = db.query(KRI).count()
        orders_count = db.query(OrderIntake).count()
        pos_count = db.query(PurchaseOrder).count()

        print("\n==================================================")
        print("DATABASE INITIALIZATION & SEED SUMMARY")
        print("==================================================")
        print(f"Process Areas Seeded : {pa_count}")
        print(f"Data Sources Seeded  : {ds_count}")
        print(f"KRIs Configured      : {kri_count} (Primary Active KRI ID: {kri_id})")
        print(f"Order Intake Records : {orders_count} (SAP ECC)")
        print(f"Purchase Orders      : {pos_count} (Red Box PO)")
        print("==================================================")
        print("Database is ready for audit execution!")

    finally:
        db.close()


if __name__ == "__main__":
    reset_and_seed_database()
