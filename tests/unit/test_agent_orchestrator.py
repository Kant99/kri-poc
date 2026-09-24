"""Unit Tests for Agent Orchestrator and Tool Invocation Loop."""

import pytest
from datetime import date
from app.repositories.financial_repository import FinancialRepository
from app.repositories.audit_repository import AuditRepository
from app.services.execution_context import AuditExecutionContext
from app.services.agent_orchestrator import AgentOrchestrator
from app.services.llm_provider import MockLLMProvider


def test_agent_orchestrator_loop_with_mock_llm(db_session, sample_active_kri):
    """Test full agent orchestration loop with MockLLMProvider."""
    fin_repo = FinancialRepository(db_session)
    audit_repo = AuditRepository(db_session)
    run = audit_repo.create_audit_run(sample_active_kri.id, date(2026, 1, 1), date(2026, 3, 31))
    context = AuditExecutionContext(
        run.id, run.run_reference, sample_active_kri, db_session, fin_repo, audit_repo, date(2026, 1, 1), date(2026, 3, 31)
    )

    orchestrator = AgentOrchestrator(llm_provider=MockLLMProvider())
    result = orchestrator.run_audit(context)

    assert result["status"] == "COMPLETED"
    assert result["metrics"] is not None
    assert result["metrics"]["total_transactions"] == 80
    assert result["metrics"]["missing_po_count"] == 8
    assert result["metrics"]["amount_mismatch_count"] == 10
    assert result["total_exceptions"] == 20  # 8 missing + 10 mismatch + 2 ambiguous
    assert result["tool_invocations_count"] >= 5


def test_agent_orchestrator_mandatory_stages_enforced(db_session, sample_active_kri):
    """Test that mandatory metrics calculation and evidence generation are enforced even if LLM finishes early."""
    class EarlyStoppingMockLLM:
        def create_response(self, messages, tools=None):
            return {"role": "assistant", "content": "Done without calling tools.", "tool_calls": None}

    fin_repo = FinancialRepository(db_session)
    audit_repo = AuditRepository(db_session)
    run = audit_repo.create_audit_run(sample_active_kri.id, date(2026, 1, 1), date(2026, 3, 31))
    context = AuditExecutionContext(
        run.id, run.run_reference, sample_active_kri, db_session, fin_repo, audit_repo, date(2026, 1, 1), date(2026, 3, 31)
    )

    orchestrator = AgentOrchestrator(llm_provider=EarlyStoppingMockLLM())
    result = orchestrator.run_audit(context)

    assert result["status"] == "COMPLETED"
    assert context.calculated_metrics is not None
