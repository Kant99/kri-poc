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
from app.services.plan_service import PlanService
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
        self.orchestrator = AgentOrchestrator(llm_provider=llm_provider)

    def execute_audit_run(self, req: AuditRunRequest) -> AuditRunDetailResponse:
        """Execute a full audit run synchronously."""
        kri = self.kri_repo.get_kri_by_id(req.kri_id)
        if not kri:
            raise ValueError(f"KRI with ID {req.kri_id} not found.")

        # Clean customer_name input (handle Swagger UI 'string' placeholder)
        cleaned_cust = None
        if req.customer_name:
            c = str(req.customer_name).strip()
            if c and c.lower() not in ("string", "all", "none", "null", "undefined", ""):
                cleaned_cust = c

        # Ensure execution plan exists
        plan_record = self.audit_repo.get_latest_plan_for_kri(kri.id)
        if not plan_record:
            # Generate and validate plan automatically
            structured_plan = PlanService.generate_plan_from_steps(kri)
            val_result = PlanService.validate_plan(kri, structured_plan)
            plan_record = self.audit_repo.create_execution_plan(
                kri_id=kri.id,
                plan_payload=structured_plan.model_dump(mode="json"),
                is_valid=val_result.is_valid,
                validation_errors=val_result.errors,
            )

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

        provider_mode = "MockLLMProvider" if settings.use_mock_llm or not settings.is_azure_configured() else f"AzureOpenAI ({settings.azure_openai_deployment_name})"
        run_logger.log_run_init(
            kri_identifier=kri.identifier,
            kri_name=kri.name,
            start_date=req.start_date,
            end_date=req.end_date,
            customer_filter=cleaned_cust,
            provider_mode=provider_mode,
        )
        if plan_record.plan_payload:
            run_logger.log_plan_loaded(plan_record.plan_payload)

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
        )

        try:
            # Execute Agent Orchestration Loop
            result = self.orchestrator.run_audit(context)
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
