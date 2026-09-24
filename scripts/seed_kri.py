"""KRI Catalog and Master Configuration Seeder.

Seeds Process Areas, Data Sources, the Primary Order Intake vs PO KRI,
5 natural language test steps, thresholds, schedules, and reviewer configurations.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal, init_db
from app.repositories.kri_repository import KRIRepository
from app.repositories.audit_repository import AuditRepository
from app.schemas.kri import (
    KRICreate,
    KRITestStepCreate,
    KRIThresholdCreate,
    KRIScheduleCreate,
    ReviewerCreate,
    IndicatorTypeEnum,
    ThresholdTypeEnum,
    ComparisonOperatorEnum,
)
from app.services.plan_service import PlanService


def seed_kri_configuration(db: Session) -> int:
    """Seed catalog metadata and primary O2C KRI."""
    kri_repo = KRIRepository(db)
    audit_repo = AuditRepository(db)

    # 1. Seed Process Areas
    pa_o2c = kri_repo.get_or_create_process_area(
        name="Order to Cash",
        code="O2C",
        description="End-to-end customer order processing, fulfillment, billing, and cash collection.",
    )
    kri_repo.get_or_create_process_area(
        name="Procure to Pay",
        code="P2P",
        description="Requisitioning, purchasing, receiving, paying for goods and services.",
    )
    kri_repo.get_or_create_process_area(
        name="Record to Report",
        code="R2R",
        description="Financial closing, general ledger, and statutory reporting.",
    )

    # 2. Seed Data Sources Catalog
    ds_sap = kri_repo.get_or_create_data_source(
        name="SAP ECC",
        code="SAP_ECC",
        system_type="ERP",
        description="Core enterprise resource planning system for sales and order processing.",
    )
    kri_repo.get_or_create_data_source(
        name="Sapiens",
        code="SAPIENS",
        system_type="POLICY_CORE",
        description="Insurance and policy administration system.",
    )
    ds_po = kri_repo.get_or_create_data_source(
        name="Red Box / PO",
        code="RED_BOX_PO",
        system_type="PO_ENGINE",
        description="Centralized procurement and purchase order repository.",
    )
    kri_repo.get_or_create_data_source(
        name="Blue Planet",
        code="BLUE_PLANET",
        system_type="BILLING",
        description="Enterprise billing and revenue management platform.",
    )
    kri_repo.get_or_create_data_source(
        name="EMS / SharePoint",
        code="EMS_SHAREPOINT",
        system_type="DOC_STORAGE",
        description="Contract repository and documentation management.",
    )

    # 3. Check or Create Primary KRI
    existing_kri = kri_repo.get_kri_by_identifier("KRI-O2C-001")
    if existing_kri:
        print(f"KRI {existing_kri.identifier} already exists (ID: {existing_kri.id}).")
        return existing_kri.id

    kri = kri_repo.create_kri(
        KRICreate(
            identifier="KRI-O2C-001",
            name="Order Intake vs Purchase Order Reconciliation",
            process_area_id=pa_o2c.id,
            indicator_type=IndicatorTypeEnum.LEADING,
            risk_description=(
                "Risk of revenue leakage, unapproved commercial commitments, and financial statement misstatements "
                "caused by discrepancies between Customer Orders booked in SAP ECC and approved Purchase Orders in Red Box PO."
            ),
            end_goal=(
                "Continuously verify that 100% of customer orders booked in SAP ECC have an approved, matching PO in Red Box "
                "with an amount variance within the configured 10% threshold."
            ),
            data_source_ids=[ds_sap.id, ds_po.id],
        )
    )

    # 4. Add Administrator-defined natural language test steps
    test_steps = [
        (1, "Extract Order Intake Population", "Extract the population of Order Intake records from SAP ECC for the audit period."),
        (2, "Extract Purchase Orders Population", "Extract the population of Purchase Orders from Red Box PO for the audit period."),
        (3, "Match Orders to Purchase Orders", "Match Order Intake records with corresponding Purchase Orders using order_id and po_reference."),
        (4, "Compare Amounts and Apply Threshold", "Compare order amounts with PO amounts and flag all variance mismatches exceeding 10%."),
        (5, "Compile Metrics and Evidence", "Calculate aggregate KRI metrics and compile reproducible evidence packages for all identified exceptions."),
    ]
    for step_num, title, instruction in test_steps:
        kri_repo.add_test_step(
            kri_id=kri.id,
            step_in=KRITestStepCreate(
                step_number=step_num,
                title=title,
                instruction=instruction,
                is_active=True,
            ),
        )

    # 5. Add 10% Threshold Configuration
    kri_repo.add_threshold(
        kri_id=kri.id,
        threshold_in=KRIThresholdCreate(
            name="10% Amount Variance Limit",
            threshold_type=ThresholdTypeEnum.PERCENTAGE_DIFFERENCE,
            operator=ComparisonOperatorEnum.GREATER_THAN,
            threshold_value=10.0,
            is_active=True,
        ),
    )

    # 6. Add Schedule Configuration
    kri_repo.create_or_update_schedule(
        kri_id=kri.id,
        schedule_in=KRIScheduleCreate(
            run_frequency="DAILY",
            fetch_data_delay_days=1,
            align_to_close_calendar=True,
            population_percentage=100.0,
        ),
    )

    # 7. Add Reviewer Configuration
    kri_repo.add_reviewer(
        kri_id=kri.id,
        reviewer_in=ReviewerCreate(
            reviewer_name="Internal Audit Lead",
            reviewer_email="audit-lead@example.com",
            human_review_setting="ALL_EXCEPTIONS",
            lifecycle_status="ACTIVE",
        ),
    )

    # 8. Generate and Validate Structured Plan
    reloaded_kri = kri_repo.get_kri_by_id(kri.id)
    structured_plan = PlanService.generate_plan_from_steps(reloaded_kri)
    val_result = PlanService.validate_plan(reloaded_kri, structured_plan)

    audit_repo.create_execution_plan(
        kri_id=kri.id,
        plan_payload=structured_plan.model_dump(mode="json"),
        is_valid=val_result.is_valid,
        validation_errors=val_result.errors,
    )

    # 9. Activate KRI
    kri_repo.update_status(kri.id, "ACTIVE")
    print(f"Successfully seeded and activated KRI {kri.identifier} (ID: {kri.id}).")
    return kri.id


if __name__ == "__main__":
    init_db()
    session = SessionLocal()
    try:
        seed_kri_configuration(session)
    finally:
        session.close()
