"""Integration and Unit Tests for KRI-03: Customers with >10% Order Cancellations and Relative Period Resolution."""

import pytest
from datetime import date, timedelta
from app.api.ui_routes import _resolve_ui_window
from app.models.kri import KRI, KRITestStep, ProcessArea, DataSource
from app.repositories.kri_repository import KRIRepository
from app.repositories.financial_repository import FinancialRepository
from app.repositories.audit_repository import AuditRepository
from app.schemas.kri import KRICreate, KRITestStepCreate, IndicatorTypeEnum
from app.schemas.tools import CalculateKRIMetricsInput
from app.services.execution_context import AuditExecutionContext
from app.services.plan_coordinator import PlanCoordinator
from app.tools.calculate_kri_metrics import calculate_kri_metrics_handler


@pytest.fixture
def kri_03_instance(db_session):
    """Seed KRI-03 into the test database session."""
    pa = db_session.query(ProcessArea).filter(ProcessArea.code == "O2C").first()
    ds_sap = db_session.query(DataSource).filter(DataSource.code == "SAP_ECC").first()
    kri_repo = KRIRepository(db_session)

    existing = kri_repo.get_kri_by_identifier("KRI-03")
    if existing:
        return existing

    kri = kri_repo.create_kri(
        KRICreate(
            identifier="KRI-03",
            name="View reversals of customer percentage",
            process_area_id=pa.id,
            indicator_type=IndicatorTypeEnum.LEADING,
            risk_description="Risk of revenue overstatement due to excessive customer order cancellations.",
            end_goal="Identify customers with over 10% order cancellations and investigate uncontracted sales.",
            data_source_ids=[ds_sap.id],
        )
    )
    kri_repo.add_test_step(
        kri_id=kri.id,
        step_in=KRITestStepCreate(
            step_number=1,
            title="Extract the customers",
            instruction="Extract the customers",
            is_active=True,
        ),
    )
    kri_repo.add_test_step(
        kri_id=kri.id,
        step_in=KRITestStepCreate(
            step_number=2,
            title="Identify customers with over 10% order cancellations",
            instruction="Identify customers with over 10% order cancellations",
            is_active=True,
        ),
    )
    kri_repo.update_status(kri.id, "ACTIVE")
    return kri


def test_resolve_ui_window_relative_periods():
    """Verify relative period strings resolve correctly against reference date."""
    ref_date = date.today()

    # Week to date
    start, end = _resolve_ui_window({"period": "Week to date"})
    expected_start = ref_date - timedelta(days=ref_date.weekday())
    expected_end = expected_start + timedelta(days=6)
    assert start == expected_start
    assert end == expected_end

    # Case-insensitivity and shorthand
    s2, e2 = _resolve_ui_window({"period": "wtd"})
    assert (s2, e2) == (start, end)

    # Month to date
    s_mtd, e_mtd = _resolve_ui_window({"period": "Month to date"})
    assert s_mtd == date(ref_date.year, ref_date.month, 1)

    # Quarter to date
    s_qtd, e_qtd = _resolve_ui_window({"period": "Quarter to date"})
    quarter = (ref_date.month - 1) // 3 + 1
    assert s_qtd == date(ref_date.year, 3 * (quarter - 1) + 1, 1)

    # Year to date
    s_ytd, e_ytd = _resolve_ui_window({"period": "Year to date"})
    assert s_ytd == date(ref_date.year, 1, 1)
    assert e_ytd == date(ref_date.year, 12, 31)


def test_empty_population_calculate_metrics(db_session, sample_active_kri):
    """Verify calculate_kri_metrics returns cleanly with 0 items when population is empty."""
    fin_repo = FinancialRepository(db_session)
    audit_repo = AuditRepository(db_session)
    run = audit_repo.create_audit_run(sample_active_kri.id, date(2025, 1, 1), date(2025, 1, 31))
    context = AuditExecutionContext(
        run.id, run.run_reference, sample_active_kri, db_session, fin_repo, audit_repo, date(2025, 1, 1), date(2025, 1, 31)
    )
    # Put an empty list under alias
    context.datasets["pop_empty"] = []

    inp = CalculateKRIMetricsInput(
        audit_run_reference=run.run_reference,
        population_dataset_reference="pop_empty",
    )
    out = calculate_kri_metrics_handler(inp, context)
    assert out.status == "SUCCESS"
    assert out.total_transactions == 0
    assert out.total_exceptions == 0
    assert out.exception_rate_by_count == 0.0


def test_kri_03_plan_generation_and_threshold(db_session, kri_03_instance):
    """Verify KRI-03 plan interpretation correctly binds 'Extract the customers' and '10% order cancellations'."""
    coordinator = PlanCoordinator(db_session)
    plan = coordinator.regenerate(kri_03_instance.id)
    assert plan.is_valid is True

    payload = plan.plan_payload
    steps = payload.get("steps", [])
    assert len(steps) >= 3

    # Step 1 should resolve to ORDER_INTAKE from 'Extract the customers'
    step1 = steps[0]
    assert step1["entity_code"] == "ORDER_INTAKE"
    assert step1["data_source_code"] == "SAP_ECC"

    # Step 3 (analyze_oi_debookings) should have 0.10 threshold and CUSTOMER_CANCELLED rule
    analyze_step = next((s for s in steps if s.get("operation") == "ANALYZE_DEBOOKINGS"), None)
    assert analyze_step is not None
    params = analyze_step.get("parameters", {})
    assert params.get("customer_quarter_ratio_threshold") == 0.1
    rules = params.get("recognition_rules", [])
    cancel_rule = next(
        (r for r in rules if "CUSTOMER_CANCELLED" in r.get("reason_codes", [])),
        None,
    )
    assert cancel_rule is not None


def test_kri_03_audit_execution_week_to_date(client, kri_03_instance):
    """Execute KRI-03 for 'Week to date' and verify completed status with cancellation exceptions."""
    resp = client.post("/api/runs", json={"kriId": "KRI-03", "period": "Week to date"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    run = data.get("run", {})
    assert run.get("status") == "completed"
    assert run.get("tested") > 0
    assert run.get("exceptions") > 0

    exceptions = data.get("exceptions", [])
    # Verify CUSTOMER_CANCELLED exceptions were flagged
    cancel_excs = [e for e in exceptions if "CUSTOMER_CANCELLED" in e.get("summary", "")]
    assert len(cancel_excs) > 0, "Expected CUSTOMER_CANCELLED exceptions to be identified"


def test_kri_03_audit_execution_q1(client, kri_03_instance):
    """Execute KRI-03 for '2026-Q1' and verify completed status."""
    resp = client.post("/api/runs", json={"kriId": "KRI-03", "period": "2026-Q1"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    run = data.get("run", {})
    assert run.get("status") == "completed"
    assert run.get("tested") == 80
    assert run.get("exceptions") > 0
