"""Pydantic Request and Response Schemas for Audit Runs, Exceptions, and Traces."""

from datetime import datetime, date
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.tools import CalculateKRIMetricsOutput, EvidenceDetail


# Audit Run Trigger
class AuditRunRequest(BaseModel):
    kri_id: int = Field(1, description="Target KRI ID (e.g., 1 for KRI-O2C-001)")
    start_date: date = Field(..., description="Audit window start date (YYYY-MM-DD)")
    end_date: date = Field(..., description="Audit window end date (YYYY-MM-DD)")
    customer_name: Optional[str] = Field(None, description="Optional customer filter (leave null or omit for all customers)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "kri_id": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-03-31",
                "customer_name": None,
            }
        }
    )



class AuditRunSummary(BaseModel):
    id: int
    run_reference: str
    kri_id: int
    status: str
    start_date: date
    end_date: date
    customer_filter: Optional[str] = None
    total_transactions: int
    matched_count: int
    missing_po_count: int
    amount_mismatch_count: int
    total_exceptions: int
    total_order_value: float
    exception_order_value: float
    mismatch_value_percentage: float
    exception_rate_by_count: float
    exception_rate_by_value: float
    started_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class AuditRunDetailResponse(AuditRunSummary):
    metrics: Optional[Dict[str, Any]] = None
    exception_count: int = 0
    tool_invocation_count: int = 0


# Audit Exception Schemas
class AuditExceptionSummary(BaseModel):
    id: int
    exception_reference: str
    order_id: str
    po_id: Optional[str] = None
    exception_type: str
    severity: str
    reason_code: str
    order_amount: float
    po_amount: Optional[float] = None
    difference_amount: Optional[float] = None
    difference_percentage: Optional[float] = None
    threshold_value: Optional[float] = None
    review_status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AuditExceptionDetailResponse(AuditExceptionSummary):
    explanation: str
    evidence_reference: Optional[str] = None
    order_record: Optional[Dict[str, Any]] = None
    po_record: Optional[Dict[str, Any]] = None
    calculation_details: Optional[Dict[str, Any]] = None


class ExceptionStatusUpdate(BaseModel):
    review_status: str = Field(..., description="PENDING_REVIEW, APPROVED, REJECTED, SUPPRESSED")


# Tool Invocation Trace Schemas
class ToolInvocationTraceItem(BaseModel):
    step_number: int
    tool_name: str
    input_payload: Dict[str, Any]
    output_payload: Dict[str, Any]
    status: str
    error_message: Optional[str] = None
    execution_time_ms: float
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AuditTraceResponse(BaseModel):
    run_reference: str
    total_tool_calls: int
    invocations: List[ToolInvocationTraceItem] = Field(default_factory=list)


# Step-by-Step Execution Logs Schemas
class AuditLogEventItem(BaseModel):
    id: Optional[int] = None
    step_number: int
    stage: str
    message: str
    payload: Optional[Dict[str, Any]] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AuditLogResponse(BaseModel):
    run_reference: str
    audit_run_id: int
    total_events: int
    log_file_path: Optional[str] = None
    events: List[AuditLogEventItem] = Field(default_factory=list)

