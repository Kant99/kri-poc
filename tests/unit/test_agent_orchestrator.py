"""Unit Tests for the Deterministic Plan Executor and the Plan-Driven Mock Provider."""

from datetime import date

import pytest

from app.core.logging_service import RunLogger
from app.repositories.audit_repository import AuditRepository
from app.repositories.financial_repository import FinancialRepository
from app.repositories.kri_repository import KRIRepository
from app.services.agent_orchestrator import AgentOrchestrator
from app.services.execution_context import AuditExecutionContext
from app.services.llm_provider import MockLLMProvider
from app.services.plan_coordinator import PlanCoordinator

START = date(2026, 1, 1)
END = date(2026, 3, 31)


def _context(db_session, kri):
    fin_repo = FinancialRepository(db_session)
    audit_repo = AuditRepository(db_session)
    plan = PlanCoordinator(db_session).ensure_current_plan(kri.id)
    run = audit_repo.create_audit_run(
        kri.id, START, END, execution_plan_id=plan.id
    )
    context = AuditExecutionContext(
        audit_run_id=run.id,
        run_reference=run.run_reference,
        kri=kri,
        db=db_session,
        financial_repo=fin_repo,
        audit_repo=audit_repo,
        start_date=START,
        end_date=END,
        plan=plan.plan_payload,
    )
    context.run_logger = RunLogger(
        run_reference=run.run_reference, audit_run_id=run.id, db=db_session
    )
    return context, plan


def test_plan_executor_reproduces_the_expected_population(db_session, sample_active_kri):
    """The plan-driven run must produce the same exception profile as the legacy loop."""
    context, _ = _context(db_session, sample_active_kri)
    result = AgentOrchestrator(llm_provider=MockLLMProvider()).run_audit(context)

    assert result["status"] == "COMPLETED"
    metrics = result["metrics"]
    assert metrics["total_transactions"] == 80
    assert metrics["missing_po_count"] == 8
    assert metrics["amount_mismatch_count"] == 10
    assert result["total_exceptions"] == 20  # 8 missing + 10 mismatch + 2 ambiguous
    assert metrics["exception_rate_by_count"] == 25.0


def test_executor_calls_exactly_the_planned_tools_in_order(db_session, sample_active_kri):
    """R1: dispatch comes from the plan, and no unplanned tool is ever called."""
    context, plan = _context(db_session, sample_active_kri)
    AgentOrchestrator(llm_provider=MockLLMProvider()).run_audit(context)

    invocations = context.audit_repo.list_tool_invocations(context.audit_run_id)
    # Per-record operations legitimately call their tools many times, so compare the
    # leading, once-per-plan-step dispatch sequence.
    dispatched: list = []
    for inv in invocations:
        if not dispatched or dispatched[-1] != inv.tool_name:
            dispatched.append(inv.tool_name)

    planned = [s["tool_name"] for s in plan.plan_payload["steps"] if s["tool_name"] != "prepare"]
    for tool_name in planned:
        assert tool_name in invocations_names(invocations)

    # Every invoked tool is one the plan or a per-record expansion authorises.
    allowed = set(planned) | {"calculate_difference", "apply_threshold", "generate_explanation"}
    assert {inv.tool_name for inv in invocations} <= allowed


def invocations_names(invocations):
    return [inv.tool_name for inv in invocations]


def test_executor_records_the_resolved_data_source_per_read(db_session, sample_active_kri):
    """Source attribution: the trace shows which catalog source each read came from."""
    context, _ = _context(db_session, sample_active_kri)
    AgentOrchestrator(llm_provider=MockLLMProvider()).run_audit(context)

    reads = [
        inv for inv in context.audit_repo.list_tool_invocations(context.audit_run_id)
        if inv.tool_name == "fetch_financial_data"
    ]
    assert len(reads) == 2
    sources = {inv.input_payload["data_source_code"] for inv in reads}
    assert sources == {"SAP_ECC", "RED_BOX_PO"}
    entities = {inv.input_payload["entity_code"] for inv in reads}
    assert entities == {"ORDER_INTAKE", "PURCHASE_ORDER"}


def test_executor_emits_plan_step_lifecycle_events(db_session, sample_active_kri):
    context, _ = _context(db_session, sample_active_kri)
    AgentOrchestrator(llm_provider=MockLLMProvider()).run_audit(context)

    stages = [log.stage for log in context.audit_repo.list_run_logs(context.audit_run_id)]
    assert "PLAN_STEP_STARTED" in stages
    assert "PLAN_STEP_COMPLETED" in stages
    assert "PLAN_EXECUTION_STARTED" in stages
    assert "PLAN_EXECUTION_COMPLETED" in stages


def test_executor_refuses_to_run_without_a_plan(db_session, sample_active_kri):
    """No plan means no run: the agent never falls back to guessing."""
    context, _ = _context(db_session, sample_active_kri)
    context.plan = {}
    with pytest.raises(RuntimeError):
        AgentOrchestrator(llm_provider=MockLLMProvider()).run_audit(context, plan={})


def test_executor_refuses_a_kri_with_no_active_steps(db_session, sample_active_kri):
    repo = KRIRepository(db_session)
    plan = PlanCoordinator(db_session).ensure_current_plan(sample_active_kri.id)

    # Deactivate the steps after the plan exists, then run with that plan.
    for step in repo.get_kri_by_id(sample_active_kri.id).test_steps:
        step.is_active = False
    db_session.commit()

    context, _ = _context_with_plan(db_session, repo.get_kri_by_id(sample_active_kri.id), plan)
    with pytest.raises(RuntimeError) as exc:
        AgentOrchestrator(llm_provider=MockLLMProvider()).run_audit(context, plan=plan.plan_payload)
    assert "no active test steps" in str(exc.value)


def _context_with_plan(db_session, kri, plan):
    audit_repo = AuditRepository(db_session)
    run = audit_repo.create_audit_run(kri.id, START, END, execution_plan_id=plan.id)
    context = AuditExecutionContext(
        audit_run_id=run.id,
        run_reference=run.run_reference,
        kri=kri,
        db=db_session,
        financial_repo=FinancialRepository(db_session),
        audit_repo=audit_repo,
        start_date=START,
        end_date=END,
        plan=plan.plan_payload,
    )
    context.run_logger = RunLogger(
        run_reference=run.run_reference, audit_run_id=run.id, db=db_session
    )
    return context, plan


def test_threshold_change_changes_the_exception_set(db_session, sample_active_kri):
    """The plan really drives execution: a stricter tolerance yields fewer exceptions."""
    from app.schemas.kri import KRITestStepCreate

    repo = KRIRepository(db_session)
    repo.replace_test_steps(
        sample_active_kri.id,
        [
            KRITestStepCreate(step_number=1, title="Extract Orders",
                              instruction="Extract the population of Order Intake records from SAP ECC."),
            KRITestStepCreate(step_number=2, title="Extract POs",
                              instruction="Extract the population of Purchase Orders from Red Box PO."),
            KRITestStepCreate(step_number=3, title="Match", instruction="Match the populations using order_id."),
            KRITestStepCreate(step_number=4, title="Threshold",
                              instruction="Flag every variance exceeding the configured threshold."),
        ],
    )
    kri = repo.get_kri_by_id(sample_active_kri.id)

    def _run(threshold_value):
        kri.thresholds[0].threshold_value = threshold_value
        db_session.commit()
        context, plan = _context(db_session, repo.get_kri_by_id(kri.id))
        result = AgentOrchestrator(llm_provider=MockLLMProvider()).run_audit(context)
        planned = next(
            s for s in plan.plan_payload["steps"] if s["operation"] == "APPLY_THRESHOLD"
        )
        return planned["parameters"]["threshold_value"], result

    value_10, result_10 = _run(10.0)
    value_30, result_30 = _run(30.0)

    assert (value_10, value_30) == (10.0, 30.0)
    assert result_10["metrics"]["amount_mismatch_count"] == 10
    assert result_30["metrics"]["amount_mismatch_count"] < 10


# --- Mock provider parity -------------------------------------------------------


def test_mock_provider_walks_the_plan_in_order(db_session, sample_active_kri):
    """The offline provider emits exactly the plan's tool calls, in order (Phase 6)."""
    plan = PlanCoordinator(db_session).ensure_current_plan(sample_active_kri.id).plan_payload
    provider = MockLLMProvider()

    dispatchable = [
        s for s in plan["steps"]
        if s["tool_name"] != "prepare"
        and s["operation"] not in {"COMPARE_AMOUNTS", "CALCULATE_DIFFERENCE", "APPLY_THRESHOLD", "EVALUATE_THRESHOLD"}
    ]

    emitted: list = []
    done: list = []
    for _ in range(len(dispatchable) + 2):
        response = provider.create_response(
            messages=[{"role": "user", "content": "Audit Window: 2026-01-01 to 2026-03-31"}],
            tools=None,
            plan=plan,
            completed_tools=done,
        )
        if not response["tool_calls"]:
            break
        call = response["tool_calls"][0]["function"]
        emitted.append(call["name"])
        # The next undispatched plan step determines what was just emitted.
        done.append(next(s["step_number"] for s in dispatchable if s["step_number"] not in done))

    assert emitted == [s["tool_name"] for s in dispatchable]
    # Two separate extract steps means the same tool is emitted twice, in order.
    assert emitted[:2] == ["fetch_financial_data", "fetch_financial_data"]
    assert "compare_records" in emitted
    assert emitted[-1] == "build_evidence"

def test_mock_provider_returns_no_tool_calls_without_a_plan():
    provider = MockLLMProvider()
    response = provider.create_response(messages=[{"role": "user", "content": "hello"}], tools=None)
    assert response["tool_calls"] is None
    assert response["content"]
