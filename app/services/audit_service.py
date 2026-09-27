"""Audit Execution Service.

Coordinates end-to-end execution of KRI audits, interfaces with repositories,
instantiates isolated execution contexts, and manages run state transitions.
"""

import logging
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from app.repositories.kri_repository import KRIRepository
from app.repositories.financial_repository import FinancialRepository
from app.repositories.audit_repository import AuditRepository
from app.services.execution_context import AuditExecutionContext
from app.services.plan_coordinator import PlanCoordinator, PlanStaleError
from app.services.agent_orchestrator import AgentOrchestrator
from app.schemas.audit import AuditRunRequest, AuditRunDetailResponse
from app.services.llm_provider import LLMProvider

logger = logging.getLogger(__name__)


from app.core.logging_service import RunLogger
from app.core.config import settings


class AuditService:
    """High-level service orchestrating audit runs and retrieving results."""

    def __init__(self, db: Session, llm_provider: Optional[LLMProvider] = None):
        self.db = db
        self.kri_repo = KRIRepository(db)
        self.financial_repo = FinancialRepository(db)
        self.audit_repo = AuditRepository(db)
        self.plan_coordinator = PlanCoordinator(db, llm_provider=llm_provider)
        self.orchestrator = AgentOrchestrator(llm_provider=llm_provider)

    def _resolve_plan(self, kri_id: int, execution_plan_id: Optional[int] = None):
        """Return ``(plan_record, payload)`` for this run.

        A caller-supplied ``execution_plan_id`` pins an exact version (used by replay and by
        audit-of-record). Otherwise the plan matching the KRI's *current* configuration is
        used, generating one if the configuration has changed since the last run (R3).
        """
        if execution_plan_id is not None:
            plan_record = self.audit_repo.get_plan_by_id(execution_plan_id)
            if plan_record is None:
                raise ValueError(f"Execution plan {execution_plan_id} not found.")
            if plan_record.kri_id != kri_id:
                raise ValueError(
                    f"Execution plan {execution_plan_id} belongs to KRI {plan_record.kri_id}, "
                    f"not KRI {kri_id}."
                )
            if not plan_record.is_valid:
                raise ValueError(
                    f"Execution plan {execution_plan_id} is not valid and cannot be executed: "
                    f"{plan_record.validation_errors}"
                )
            return plan_record, plan_record.plan_payload or {}

        plan_record = self.plan_coordinator.ensure_current_plan(kri_id, strict_stages=True)
        return plan_record, plan_record.plan_payload or {}

    def execute_audit_run(self, req: AuditRunRequest) -> AuditRunDetailResponse:
        """Execute a full audit run synchronously from its interpreted execution plan."""
        kri = self.kri_repo.get_kri_by_id(req.kri_id)
        if not kri:
            raise ValueError(f"KRI with ID {req.kri_id} not found.")

        # Clean customer_name input (handle Swagger UI 'string' placeholder)
        cleaned_cust = None
        if req.customer_name:
            c = str(req.customer_name).strip()
            if c and c.lower() not in ("string", "all", "none", "null", "undefined", ""):
                cleaned_cust = c

        # The plan is the execution contract: resolve or regenerate it before the run.
        try:
            plan_record, plan_payload = self._resolve_plan(kri.id, req.execution_plan_id)
        except PlanStaleError as exc:
            raise ValueError(
                f"KRI '{kri.identifier}' cannot be run because its test steps could not be "
                f"interpreted into a runnable plan: {exc}"
            ) from exc

        # Create Audit Run record in DB
        audit_run = self.audit_repo.create_audit_run(
            kri_id=kri.id,
            start_date=req.start_date,
            end_date=req.end_date,
            customer_filter=cleaned_cust,
            execution_plan_id=plan_record.id,
        )

        # Initialize Run Logger for persistent logging
        run_logger = RunLogger(
            run_reference=audit_run.run_reference,
            audit_run_id=audit_run.id,
            db=self.db,
        )

        provider_mode = (
            "PlanExecutor"
            if settings.is_plan_executor_enabled()
            else ("MockLLMProvider" if settings.use_mock_llm or not settings.is_azure_configured()
                  else f"AzureOpenAI ({settings.azure_openai_deployment_name})")
        )
        run_logger.log_run_init(
            kri_identifier=kri.identifier,
            kri_name=kri.name,
            start_date=req.start_date,
            end_date=req.end_date,
            customer_filter=cleaned_cust,
            provider_mode=provider_mode,
        )
        if plan_payload:
            run_logger.log_plan_loaded(
                {
                    "version": plan_record.version,
                    "plan_id": plan_record.id,
                    "plan_hash": plan_record.plan_hash,
                    "steps_hash": plan_record.steps_hash,
                    "source": plan_record.source,
                    **plan_payload,
                }
            )

        # Initialize Scoped Execution Context
        context = AuditExecutionContext(
            audit_run_id=audit_run.id,
            run_reference=audit_run.run_reference,
            kri=kri,
            db=self.db,
            financial_repo=self.financial_repo,
            audit_repo=self.audit_repo,
            start_date=req.start_date,
            end_date=req.end_date,
            customer_filter=cleaned_cust,
            run_logger=run_logger,
            plan=plan_payload,
        )

        try:
            result = self.orchestrator.run_audit(context, plan=plan_payload)
            metrics = result.get("metrics", {})

            # Update Audit Run to COMPLETED
            updated_run = self.audit_repo.update_audit_run_metrics(
                audit_run_id=audit_run.id,
                status="COMPLETED",
                metrics=metrics,
            )
            run_logger.log_run_complete(
                status="COMPLETED",
                total_exceptions=len(context.candidate_exceptions),
                metrics=metrics,
            )

        except Exception as e:
            logger.exception("Audit run %s encountered a failure: %s", audit_run.run_reference, e)
            updated_run = self.audit_repo.update_audit_run_metrics(
                audit_run_id=audit_run.id,
                status="FAILED",
                metrics={},
                error_message=str(e),
            )
            run_logger.log_run_complete(
                status="FAILED",
                total_exceptions=len(context.candidate_exceptions),
                error_message=str(e),
            )

        return self.get_audit_run_details(audit_run.id)

    def replay_audit_run(self, run_id: int) -> AuditRunDetailResponse:
        """Re-execute a past run's pinned plan against the same audit window."""
        source = self.audit_repo.get_audit_run_by_id(run_id)
        if not source:
            raise ValueError(f"Audit run {run_id} not found.")
        if not source.execution_plan_id:
            raise ValueError(f"Audit run {run_id} has no pinned execution plan to replay.")

        return self.execute_audit_run(
            AuditRunRequest(
                kri_id=source.kri_id,
                start_date=source.start_date,
                end_date=source.end_date,
                customer_name=source.customer_filter,
                execution_plan_id=source.execution_plan_id,
            )
        )

    def get_audit_run_details(self, audit_run_id: int) -> AuditRunDetailResponse:
        """Retrieve audit run details and summary metrics."""
        run = self.audit_repo.get_audit_run_by_id(audit_run_id)
        if not run:
            raise ValueError(f"Audit run {audit_run_id} not found.")

        exceptions_count = len(run.exceptions) if run.exceptions else 0
        invocations_count = len(run.tool_invocations) if run.tool_invocations else 0

        return AuditRunDetailResponse(
            id=run.id,
            run_reference=run.run_reference,
            kri_id=run.kri_id,
            status=run.status,
            start_date=run.start_date,
            end_date=run.end_date,
            customer_filter=run.customer_filter,
            total_transactions=run.total_transactions,
            matched_count=run.matched_count,
            missing_po_count=run.missing_po_count,
            amount_mismatch_count=run.amount_mismatch_count,
            total_exceptions=run.total_exceptions,
            total_order_value=run.total_order_value,
            exception_order_value=run.exception_order_value,
            mismatch_value_percentage=run.mismatch_value_percentage,
            exception_rate_by_count=run.exception_rate_by_count,
            exception_rate_by_value=run.exception_rate_by_value,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error_message=run.error_message,
            metrics=run.metrics_payload,
            exception_count=exceptions_count,
            tool_invocation_count=invocations_count,
        )
