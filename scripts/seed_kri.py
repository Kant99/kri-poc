"""KRI Catalog and Master Configuration Seeder.

Seeds Process Areas, the Data Source catalog with entity bindings, the primary
Order Intake vs PO KRI, its natural language test steps, thresholds, schedules and reviewers,
then interprets and persists the execution plan.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal, init_db
from app.repositories.kri_repository import KRIRepository
from app.schemas.kri import (
    KRICreate,
    KRIUpdate,
    KRITestStepCreate,
    KRIThresholdCreate,
    KRIScheduleCreate,
    ReviewerCreate,
    IndicatorTypeEnum,
    ThresholdTypeEnum,
    ComparisonOperatorEnum,
)
from app.services.data_source_bootstrap import (
    ADDITIONAL_SOURCE_BINDINGS,
    DEFAULT_SOURCE_BINDINGS,
)
from app.services.plan_coordinator import PlanCoordinator, PlanStaleError

#: The catalog. Availability is declared, not assumed: a source only becomes queryable once
#: an entity is bound to it.
DATA_SOURCE_CATALOG = [
    {
        "name": "SAP ECC",
        "code": "SAP_ECC",
        "system_type": "ERP",
        "description": "Core enterprise resource planning system for sales and order processing, order intake, and debooking reversals.",
    },
    {
        "name": "Sapiens",
        "code": "SAPIENS",
        "system_type": "POLICY_CORE",
        "description": "Insurance and policy administration system.",
    },
    {
        "name": "Red Box / PO",
        "code": "RED_BOX_PO",
        "system_type": "PO_ENGINE",
        "description": "Centralized procurement and purchase order repository.",
    },
    {
        "name": "Blue Planet",
        "code": "BLUE_PLANET",
        "system_type": "BILLING",
        "description": "Enterprise billing and revenue management platform.",
    },
    {
        "name": "EMS / SharePoint",
        "code": "EMS_SHAREPOINT",
        "system_type": "DOC_STORAGE",
        "description": "Contract repository and documentation management.",
    },
]

#: Natural language test steps. Each names its data source explicitly so the interpreter's
#: mention scan resolves the read target without any guessing.
PRIMARY_TEST_STEPS = [
    (
        1,
        "Extract Order Intake Population",
        "Extract the population of Order Intake records from SAP ECC for the audit period.",
    ),
    (
        2,
        "Extract Purchase Orders Population",
        "Extract the population of Purchase Orders from Red Box PO for the audit period.",
    ),
    (
        3,
        "Match Orders to Purchase Orders",
        "Match Order Intake records with corresponding Purchase Orders using order_id.",
    ),
    (
        4,
        "Compare Amounts and Apply Threshold",
        "Compare order amounts with PO amounts and flag all variance mismatches exceeding the "
        "configured threshold.",
    ),
    (
        5,
        "Compile Metrics and Evidence",
        "Calculate aggregate KRI metrics and compile reproducible evidence packages for all "
        "identified exceptions.",
    ),
]


def seed_data_source_catalog(db: Session) -> dict:
    """Seed the data source catalog and materialise entity bindings from the registry.

    A source is only queryable once an entity is bound to it. ``SAP_ECC`` deliberately
    exposes two entities (the order intake population and its debookings), which is the real
    shape of an ERP and is what makes a step that names only the source ambiguous.
    """
    kri_repo = KRIRepository(db)
    sources = {}

    for entry in DATA_SOURCE_CATALOG:
        code = entry["code"]
        primary = DEFAULT_SOURCE_BINDINGS.get(code)
        extra = ADDITIONAL_SOURCE_BINDINGS.get(code) or []
        bindings = ([(primary[0], primary[1])] if primary else []) + list(extra)

        ds = kri_repo.get_or_create_data_source(
            name=entry["name"],
            code=code,
            system_type=entry["system_type"],
            description=entry["description"],
            is_queryable=bool(bindings),
            availability_note=None if bindings else (
                "No queryable entity is bound to this data source in this environment."
            ),
        )
        kri_repo.set_entity_bindings(ds.id, bindings)
        sources[code] = ds

    kri_repo.sync_data_source_availability()
    return sources


def seed_kri_configuration(db: Session) -> int:
    """Seed catalog metadata and the primary O2C KRI, then interpret its execution plan."""
    kri_repo = KRIRepository(db)

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

    # 2. Seed the Data Source catalog with entity bindings
    sources = seed_data_source_catalog(db)
    ds_sap = sources["SAP_ECC"]
    ds_po = sources["RED_BOX_PO"]

    # 3. Check or Create Primary KRI
    existing_kri = kri_repo.get_kri_by_identifier("KRI-O2C-001")
    if existing_kri:
        print(f"KRI {existing_kri.identifier} already exists (ID: {existing_kri.id}).")
        coordinator = PlanCoordinator(db)
        try:
            plan = coordinator.ensure_current_plan(existing_kri.id)
            print(f"Execution plan v{plan.version} is current for {existing_kri.identifier}.")
        except PlanStaleError as exc:
            print(f"WARNING: could not refresh the execution plan for {existing_kri.identifier}: {exc}")
        kri_id = existing_kri.id
    else:

        kri = kri_repo.create_kri(
            KRICreate(
                identifier="KRI-O2C-001",
                name="Order Intake vs Purchase Order Reconciliation",
                process_area_id=pa_o2c.id,
                indicator_type=IndicatorTypeEnum.LEADING,
                risk_description=(
                    "Risk of revenue leakage, unapproved commercial commitments, and financial statement "
                    "misstatements caused by discrepancies between Customer Orders booked in SAP ECC and "
                    "approved Purchase Orders in Red Box PO."
                ),
                end_goal=(
                    "Continuously verify that 100% of customer orders booked in SAP ECC have an approved, "
                    "matching PO in Red Box with an amount variance within the configured 10% threshold."
                ),
                data_source_ids=[ds_sap.id, ds_po.id],
            )
        )

        # 4. Add administrator-defined natural language test steps
        for step_number, title, instruction in PRIMARY_TEST_STEPS:
            kri_repo.add_test_step(
                kri_id=kri.id,
                step_in=KRITestStepCreate(
                    step_number=step_number,
                    title=title,
                    instruction=instruction,
                    is_active=True,
                ),
            )

        # 5. Add the 10% variance threshold
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

        # 8. Interpret and persist the execution plan. There is no approval gate: the plan is
        #    committed because it is valid, and the user is never asked to review it (R2).
        coordinator = PlanCoordinator(db)
        try:
            plan_record = coordinator.ensure_current_plan(kri.id)
            print(
                f"Interpreted execution plan v{plan_record.version} "
                f"({len((plan_record.plan_payload or {}).get('steps', []))} steps, "
                f"source={plan_record.source})."
            )
        except PlanStaleError as exc:
            raise RuntimeError(
                f"Seeded KRI '{kri.identifier}' could not be interpreted into a runnable plan: {exc}"
            ) from exc

        # 9. Activate KRI
        kri_repo.update_status(kri.id, "ACTIVE")
        print(f"Successfully seeded and activated KRI {kri.identifier} (ID: {kri.id}).")
        kri_id = kri.id

    # A second, independent KRI exercises the debooking analysis. It is seeded after the
    # primary so the primary stays the one callers get back.
    seed_debooking_kri(kri_repo, pa_o2c, ds_sap)
    return kri_id


DEBOOKING_TEST_STEPS = [
    (1, "Extract Bookings", "Extract all order intake bookings from SAP ECC."),
    (
        2,
        "Extract Debookings",
        "Extract all order intake debookings from SAP ECC for the same customers.",
    ),
    (
        3,
        "Assess Recognition Quality",
        "Compare the debookings against the bookings and report premature recognition, "
        "unsupported recognition, orphan debookings, and booking quality for the same "
        "customer quarter on quarter.",
    ),
]


def seed_debooking_kri(kri_repo: KRIRepository, process_area, ds_sap) -> int:
    """Seed the KRI that tests the quality of order intake recognition via its debookings.

    The test steps are deliberately plain language: the agent decides that this is a debooking
    analysis, that both populations come from SAP ECC, and which recognition problems to look
    for. Nothing is pinned.
    """
    kri_name = "Value of OI Debookings for Same Customer Quarter on Quarter"
    existing = kri_repo.get_kri_by_identifier("KRI-O2C-002")
    if existing:
        kri_repo.update_kri(
            existing.id,
            KRIUpdate(
                name=kri_name,
                process_area_id=process_area.id,
                indicator_type=IndicatorTypeEnum.LEADING,
            ),
        )
        kri = existing
        print(f"KRI {existing.identifier} already exists (ID: {existing.id}), synchronized name.")
    else:
        kri = kri_repo.create_kri(
            KRICreate(
                identifier="KRI-O2C-002",
                name=kri_name,
                process_area_id=process_area.id,
                indicator_type=IndicatorTypeEnum.LEADING,
                risk_description=(
                    "Risk that order intake revenue was recognised without support or too early. "
                    "Debookings raised shortly after booking, debookings for business reasons such as "
                    "no contract or a duplicate booking, and debookings that cannot be tied to any "
                    "order indicate recognition that did not hold up."
                ),
                end_goal=(
                    "Demonstrate that order intake booked for a customer in a quarter survives that "
                    "quarter, and that any debooking is either a legitimate timing shift or is "
                    "investigated as an unsupported or premature recognition."
                ),
                data_source_ids=[ds_sap.id],
            )
        )

        for step_number, title, instruction in DEBOOKING_TEST_STEPS:
            kri_repo.add_test_step(
                kri_id=kri.id,
                step_in=KRITestStepCreate(
                    step_number=step_number,
                    title=title,
                    instruction=instruction,
                    is_active=True,
                ),
            )

        kri_repo.create_or_update_schedule(
            kri_id=kri.id,
            schedule_in=KRIScheduleCreate(
                run_frequency="WEEKLY",
                fetch_data_delay_days=2,
                align_to_close_calendar=True,
                population_percentage=100.0,
            ),
        )

        kri_repo.add_reviewer(
            kri_id=kri.id,
            reviewer_in=ReviewerCreate(
                reviewer_name="Revenue Assurance Lead",
                reviewer_email="revenue-assurance@example.com",
                human_review_setting="ALL_EXCEPTIONS",
                lifecycle_status="ACTIVE",
            ),
        )

    # Ensure active 15% debooking ratio threshold exists
    if not kri.thresholds:
        kri_repo.add_threshold(
            kri_id=kri.id,
            threshold_in=KRIThresholdCreate(
                name="15% Debooking Ratio Threshold",
                threshold_type=ThresholdTypeEnum.PERCENTAGE_DIFFERENCE,
                operator=ComparisonOperatorEnum.GREATER_THAN,
                threshold_value=15.0,
                is_active=True,
            ),
        )

    coordinator = PlanCoordinator(kri_repo.db)
    try:
        plan_record = coordinator.ensure_current_plan(kri.id)
        print(
            f"Interpreted execution plan v{plan_record.version} "
            f"({len((plan_record.plan_payload or {}).get('steps', []))} steps, "
            f"source={plan_record.source})."
        )
    except PlanStaleError as exc:
        raise RuntimeError(
            f"Seeded KRI '{kri.identifier}' could not be interpreted into a runnable plan: {exc}"
        ) from exc

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
