"""FastAPI Router for Audit Execution, Results, Exceptions, Evidence, and Traces."""

from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.repositories.audit_repository import AuditRepository
from app.services.audit_service import AuditService
from app.schemas.audit import (
    AuditRunRequest,
    AuditRunDetailResponse,
    AuditRunSummary,
    AuditExceptionSummary,
    AuditExceptionDetailResponse,
    ExceptionStatusUpdate,
    AuditTraceResponse,
    ToolInvocationTraceItem,
    AuditLogResponse,
    AuditLogEventItem,
)
from app.schemas.tools import EvidenceDetail


router = APIRouter(prefix="/audits", tags=["Audit Execution & Results"])


@router.post("/run", response_model=AuditRunDetailResponse, status_code=status.HTTP_200_OK)
def run_audit(req: AuditRunRequest, db: Session = Depends(get_db)):
    """Trigger an autonomous agentic audit run for a target KRI and date window."""
    try:
        service = AuditService(db)
        return service.execute_audit_run(req)
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(val_err))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Audit execution failed: {e}")


@router.get("", response_model=List[AuditRunSummary])
def list_audit_runs(
    kri_id: Optional[int] = Query(None, description="Filter by KRI ID"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List historical audit runs with summary statistics."""
    runs = AuditRepository(db).list_audit_runs(kri_id=kri_id, limit=limit)
    return runs


@router.get("/{run_id}", response_model=AuditRunDetailResponse)
def get_audit_run(run_id: int, db: Session = Depends(get_db)):
    """Retrieve details and calculated aggregate metrics for an audit run."""
    try:
        return AuditService(db).get_audit_run_details(run_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/{run_id}/exceptions", response_model=List[AuditExceptionSummary])
def list_audit_exceptions(
    run_id: int,
    exception_type: Optional[str] = Query(None, description="AMOUNT_MISMATCH, MISSING_PO, AMBIGUOUS_MATCH"),
    severity: Optional[str] = Query(None, description="LOW, MEDIUM, HIGH, CRITICAL"),
    review_status: Optional[str] = Query(None, description="PENDING_REVIEW, APPROVED, REJECTED, SUPPRESSED"),
    order_id: Optional[str] = Query(None, description="Filter by specific order ID"),
    db: Session = Depends(get_db),
):
    """Retrieve flagged audit exceptions with multi-field filtering."""
    repo = AuditRepository(db)
    if not repo.get_audit_run_by_id(run_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit run {run_id} not found.")

    return repo.list_exceptions(
        audit_run_id=run_id,
        exception_type=exception_type,
        severity=severity,
        review_status=review_status,
        order_id=order_id,
    )


@router.get("/{run_id}/exceptions/{exception_id}", response_model=AuditExceptionDetailResponse)
def get_audit_exception_detail(run_id: int, exception_id: int, db: Session = Depends(get_db)):
    """Retrieve detailed exception report with explanation and evidence link."""
    repo = AuditRepository(db)
    exc = (
        db.query(repo.db.query(AuditRepository).model if hasattr(repo.db, 'model') else None)
        if False
        else None
    )
    # Direct query
    from app.models.audit import AuditException
    exc = db.query(AuditException).filter(AuditException.id == exception_id, AuditException.audit_run_id == run_id).first()
    if not exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Exception {exception_id} not found in run {run_id}.")

    # Lookup linked evidence record if available
    ev_record = repo.get_evidence_by_reference(exc.evidence_reference) if exc.evidence_reference else None

    return AuditExceptionDetailResponse(
        id=exc.id,
        exception_reference=exc.exception_reference,
        order_id=exc.order_id,
        po_id=exc.po_id,
        exception_type=exc.exception_type,
        severity=exc.severity,
        reason_code=exc.reason_code,
        order_amount=exc.order_amount,
        po_amount=exc.po_amount,
        difference_amount=exc.difference_amount,
        difference_percentage=exc.difference_percentage,
        threshold_value=exc.threshold_value,
        review_status=exc.review_status,
        explanation=exc.explanation,
        evidence_reference=exc.evidence_reference,
        created_at=exc.created_at,
        order_record=ev_record.order_payload if ev_record else None,
        po_record=ev_record.po_payload if ev_record else None,
        calculation_details=ev_record.calculation_details if ev_record else None,
    )


@router.patch("/exceptions/{exception_reference}/status", response_model=AuditExceptionSummary)
def update_exception_review_status(
    exception_reference: str,
    status_in: ExceptionStatusUpdate,
    db: Session = Depends(get_db),
):
    """Update human reviewer status for a specific exception."""
    exc = AuditRepository(db).update_exception_review_status(exception_reference, status_in.review_status)
    if not exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exception with reference '{exception_reference}' not found.",
        )
    return exc


@router.get("/{run_id}/trace", response_model=AuditTraceResponse)
def get_audit_tool_trace(run_id: int, db: Session = Depends(get_db)):
    """Retrieve sanitized tool invocation trace for auditability and governance."""
    repo = AuditRepository(db)
    run = repo.get_audit_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit run {run_id} not found.")

    invocations = repo.list_tool_invocations(run_id)
    trace_items = [
        ToolInvocationTraceItem(
            step_number=inv.step_number,
            tool_name=inv.tool_name,
            input_payload=inv.input_payload,
            output_payload=inv.output_payload,
            status=inv.status,
            error_message=inv.error_message,
            execution_time_ms=inv.execution_time_ms,
            created_at=inv.created_at,
        )
        for inv in invocations
    ]

    return AuditTraceResponse(
        run_reference=run.run_reference,
        total_tool_calls=len(trace_items),
        invocations=trace_items,
    )


@router.get("/evidence/{evidence_reference}", response_model=EvidenceDetail)
def get_evidence_package(evidence_reference: str, db: Session = Depends(get_db)):
    """Retrieve complete, reproducible evidence package for an audit finding."""
    ev = AuditRepository(db).get_evidence_by_reference(evidence_reference)
    if not ev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence package '{evidence_reference}' not found.",
        )

    return EvidenceDetail(
        evidence_reference=ev.evidence_reference,
        exception_reference=ev.exception_reference,
        order_id=ev.order_id,
        source_system=ev.source_system,
        order_record=ev.order_payload,
        po_record=ev.po_payload,
        calculation_details=ev.calculation_details,
        threshold_details=ev.threshold_details,
        explanation=ev.explanation,
    )


@router.get("/{run_id}/logs", response_model=AuditLogResponse)
def get_audit_run_logs(run_id: int, db: Session = Depends(get_db)):
    """Retrieve structured execution logs and step events for an audit run."""
    repo = AuditRepository(db)
    run = repo.get_audit_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit run {run_id} not found.")

    db_logs = repo.list_run_logs(run_id)
    log_file = Path(settings.logs_dir) / f"{run.run_reference}.log"
    log_file_path_str = str(log_file.resolve()) if log_file.exists() else None

    events = [
        AuditLogEventItem(
            id=l.id,
            step_number=l.step_number,
            stage=l.stage,
            message=l.message,
            payload=l.payload,
            created_at=l.created_at,
        )
        for l in db_logs
    ]

    return AuditLogResponse(
        run_reference=run.run_reference,
        audit_run_id=run.id,
        total_events=len(events),
        log_file_path=log_file_path_str,
        events=events,
    )


@router.get("/{run_id}/logs/file", response_class=PlainTextResponse)
def get_audit_run_log_file(run_id: int, db: Session = Depends(get_db)):
    """Download or view the raw human-readable log file for an audit run."""
    repo = AuditRepository(db)
    run = repo.get_audit_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit run {run_id} not found.")

    log_file = Path(settings.logs_dir) / f"{run.run_reference}.log"
    if not log_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log file for run '{run.run_reference}' not found on disk.",
        )

    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()

    return PlainTextResponse(content=content, media_type="text/plain; charset=utf-8")

