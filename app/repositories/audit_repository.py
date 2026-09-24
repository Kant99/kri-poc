"""Repository for Audit Runs, Exceptions, Tool Trace, Datasets, and Evidence."""

import uuid
from datetime import datetime, date
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, desc
from app.models.audit import (
    AuditRun,
    AuditException,
    AuditToolInvocation,
    ExecutionDataset,
    ExecutionPlan,
    EvidenceRecord,
)


class AuditRepository:
    """Handles audit execution and governance persistence."""

    def __init__(self, db: Session):
        self.db = db

    # Execution Plan Operations
    def create_execution_plan(
        self,
        kri_id: int,
        plan_payload: Dict[str, Any],
        is_valid: bool = True,
        validation_errors: Optional[List[str]] = None,
        audit_run_id: Optional[int] = None,
    ) -> ExecutionPlan:
        # Determine next version
        latest = (
            self.db.query(ExecutionPlan)
            .filter(ExecutionPlan.kri_id == kri_id)
            .order_by(desc(ExecutionPlan.version))
            .first()
        )
        version = (latest.version + 1) if latest else 1

        plan = ExecutionPlan(
            kri_id=kri_id,
            audit_run_id=audit_run_id,
            version=version,
            plan_payload=plan_payload,
            is_valid=is_valid,
            validation_errors=validation_errors or [],
        )
        self.db.add(plan)
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def get_latest_plan_for_kri(self, kri_id: int) -> Optional[ExecutionPlan]:
        return (
            self.db.query(ExecutionPlan)
            .filter(ExecutionPlan.kri_id == kri_id, ExecutionPlan.is_valid == True)
            .order_by(desc(ExecutionPlan.version))
            .first()
        )

    # Audit Run Operations
    def create_audit_run(
        self,
        kri_id: int,
        start_date: date,
        end_date: date,
        customer_filter: Optional[str] = None,
        execution_plan_id: Optional[int] = None,
    ) -> AuditRun:
        run_reference = f"run_{uuid.uuid4().hex[:8]}"
        run = AuditRun(
            run_reference=run_reference,
            kri_id=kri_id,
            status="RUNNING",
            start_date=start_date,
            end_date=end_date,
            customer_filter=customer_filter,
            execution_plan_id=execution_plan_id,
            started_at=datetime.utcnow(),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_audit_run_by_reference(self, run_reference: str) -> Optional[AuditRun]:
        return (
            self.db.query(AuditRun)
            .options(
                joinedload(AuditRun.kri),
                joinedload(AuditRun.exceptions),
                joinedload(AuditRun.tool_invocations),
            )
            .filter(AuditRun.run_reference == run_reference)
            .first()
        )

    def get_audit_run_by_id(self, run_id: int) -> Optional[AuditRun]:
        return (
            self.db.query(AuditRun)
            .options(
                joinedload(AuditRun.kri),
                joinedload(AuditRun.exceptions),
                joinedload(AuditRun.tool_invocations),
            )
            .filter(AuditRun.id == run_id)
            .first()
        )

    def list_audit_runs(self, kri_id: Optional[int] = None, limit: int = 50) -> List[AuditRun]:
        query = self.db.query(AuditRun).options(joinedload(AuditRun.kri))
        if kri_id:
            query = query.filter(AuditRun.kri_id == kri_id)
        return query.order_by(desc(AuditRun.started_at)).limit(limit).all()

    def update_audit_run_metrics(
        self,
        audit_run_id: int,
        status: str,
        metrics: Dict[str, Any],
        error_message: Optional[str] = None,
    ) -> Optional[AuditRun]:
        run = self.db.query(AuditRun).filter(AuditRun.id == audit_run_id).first()
        if not run:
            return None

        run.status = status
        run.completed_at = datetime.utcnow()
        run.error_message = error_message
        run.metrics_payload = metrics

        run.total_transactions = metrics.get("total_transactions", 0)
        run.matched_count = metrics.get("matched_count", 0)
        run.missing_po_count = metrics.get("missing_po_count", 0)
        run.amount_mismatch_count = metrics.get("amount_mismatch_count", 0)
        run.total_exceptions = metrics.get("total_exceptions", 0)
        run.total_order_value = metrics.get("total_order_value", 0.0)
        run.exception_order_value = metrics.get("exception_order_value", 0.0)
        run.mismatch_value_percentage = metrics.get("mismatch_value_percentage", 0.0)
        run.exception_rate_by_count = metrics.get("exception_rate_by_count", 0.0)
        run.exception_rate_by_value = metrics.get("exception_rate_by_value", 0.0)

        self.db.commit()
        self.db.refresh(run)
        return run

    # Execution Datasets Operations
    def store_dataset(
        self,
        audit_run_id: int,
        dataset_reference: str,
        entity_name: str,
        source_system: str,
        record_count: int,
        dataset_payload: List[Dict[str, Any]],
    ) -> ExecutionDataset:
        ds = ExecutionDataset(
            audit_run_id=audit_run_id,
            dataset_reference=dataset_reference,
            entity_name=entity_name,
            source_system=source_system,
            record_count=record_count,
            dataset_payload=dataset_payload,
        )
        self.db.add(ds)
        self.db.commit()
        self.db.refresh(ds)
        return ds

    def get_dataset(self, audit_run_id: int, dataset_reference: str) -> Optional[ExecutionDataset]:
        return (
            self.db.query(ExecutionDataset)
            .filter(
                ExecutionDataset.audit_run_id == audit_run_id,
                ExecutionDataset.dataset_reference == dataset_reference,
            )
            .first()
        )

    # Audit Exceptions Operations
    def create_exception(
        self,
        audit_run_id: int,
        order_id: str,
        exception_type: str,
        severity: str,
        reason_code: str,
        order_amount: float,
        explanation: str,
        po_id: Optional[str] = None,
        po_amount: Optional[float] = None,
        difference_amount: Optional[float] = None,
        difference_percentage: Optional[float] = None,
        threshold_value: Optional[float] = None,
        evidence_reference: Optional[str] = None,
        exception_reference: Optional[str] = None,
    ) -> AuditException:
        exc_ref = exception_reference or f"exc_{uuid.uuid4().hex[:8]}"
        exc = AuditException(
            audit_run_id=audit_run_id,
            exception_reference=exc_ref,
            order_id=order_id,
            po_id=po_id,
            exception_type=exception_type,
            severity=severity,
            reason_code=reason_code,
            order_amount=order_amount,
            po_amount=po_amount,
            difference_amount=difference_amount,
            difference_percentage=difference_percentage,
            threshold_value=threshold_value,
            explanation=explanation,
            review_status="PENDING_REVIEW",
            evidence_reference=evidence_reference,
        )
        self.db.add(exc)
        self.db.commit()
        self.db.refresh(exc)
        return exc

    def list_exceptions(
        self,
        audit_run_id: int,
        exception_type: Optional[str] = None,
        severity: Optional[str] = None,
        review_status: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> List[AuditException]:
        query = self.db.query(AuditException).filter(AuditException.audit_run_id == audit_run_id)
        if exception_type:
            query = query.filter(AuditException.exception_type == exception_type)
        if severity:
            query = query.filter(AuditException.severity == severity)
        if review_status:
            query = query.filter(AuditException.review_status == review_status)
        if order_id:
            query = query.filter(AuditException.order_id == order_id)
        return query.order_by(AuditException.id.asc()).all()

    def get_exception_by_reference(self, exception_reference: str) -> Optional[AuditException]:
        return (
            self.db.query(AuditException)
            .filter(AuditException.exception_reference == exception_reference)
            .first()
        )

    def update_exception_review_status(self, exception_reference: str, status: str) -> Optional[AuditException]:
        exc = self.get_exception_by_reference(exception_reference)
        if not exc:
            return None
        exc.review_status = status
        self.db.commit()
        self.db.refresh(exc)
        return exc

    # Tool Invocations Trace Operations
    def record_tool_invocation(
        self,
        audit_run_id: int,
        step_number: int,
        tool_name: str,
        input_payload: Dict[str, Any],
        output_payload: Dict[str, Any],
        status: str = "SUCCESS",
        error_message: Optional[str] = None,
        execution_time_ms: float = 0.0,
    ) -> AuditToolInvocation:
        inv = AuditToolInvocation(
            audit_run_id=audit_run_id,
            step_number=step_number,
            tool_name=tool_name,
            input_payload=input_payload,
            output_payload=output_payload,
            status=status,
            error_message=error_message,
            execution_time_ms=execution_time_ms,
        )
        self.db.add(inv)
        self.db.commit()
        self.db.refresh(inv)
        return inv

    def list_tool_invocations(self, audit_run_id: int) -> List[AuditToolInvocation]:
        return (
            self.db.query(AuditToolInvocation)
            .filter(AuditToolInvocation.audit_run_id == audit_run_id)
            .order_by(AuditToolInvocation.id.asc())
            .all()
        )

    # Evidence Record Operations
    def store_evidence(
        self,
        audit_run_id: int,
        exception_reference: str,
        order_id: str,
        source_system: str,
        order_payload: Dict[str, Any],
        calculation_details: Dict[str, Any],
        threshold_details: Dict[str, Any],
        explanation: str,
        po_payload: Optional[Dict[str, Any]] = None,
    ) -> EvidenceRecord:
        ev_ref = f"ev_{uuid.uuid4().hex[:8]}"
        ev = EvidenceRecord(
            audit_run_id=audit_run_id,
            evidence_reference=ev_ref,
            exception_reference=exception_reference,
            order_id=order_id,
            source_system=source_system,
            order_payload=order_payload,
            po_payload=po_payload,
            calculation_details=calculation_details,
            threshold_details=threshold_details,
            explanation=explanation,
            created_at=datetime.utcnow(),
        )
        self.db.add(ev)
        self.db.commit()
        self.db.refresh(ev)
        return ev

    def get_evidence_by_reference(self, evidence_reference: str) -> Optional[EvidenceRecord]:
        return (
            self.db.query(EvidenceRecord)
            .filter(EvidenceRecord.evidence_reference == evidence_reference)
            .first()
        )

    def list_run_logs(self, audit_run_id: int):
        from app.models.audit import AuditRunLog
        return (
            self.db.query(AuditRunLog)
            .filter(AuditRunLog.audit_run_id == audit_run_id)
            .order_by(AuditRunLog.id.asc())
            .all()
        )
