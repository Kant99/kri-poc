"""SQLAlchemy Models for Audit Runs, Exceptions, Tool Trace, Datasets, Plans, and Evidence."""

import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Date,
    ForeignKey,
    Text,
    JSON,
    Boolean,
    Index,
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class ExecutionPlan(Base):
    """Structured execution plan translated from administrator test steps."""
    __tablename__ = "execution_plans"

    id = Column(Integer, primary_key=True, index=True)
    kri_id = Column(Integer, ForeignKey("kris.id", ondelete="CASCADE"), nullable=False, index=True)
    audit_run_id = Column(Integer, nullable=True, index=True)
    version = Column(Integer, default=1, nullable=False)
    plan_payload = Column(JSON, nullable=False)  # Structured steps and operations
    is_valid = Column(Boolean, default=True, nullable=False)
    validation_errors = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class AuditRun(Base):
    """Audit Run execution entity containing parameters and aggregate metrics."""
    __tablename__ = "audit_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_reference = Column(String(100), unique=True, nullable=False, index=True)
    kri_id = Column(Integer, ForeignKey("kris.id"), nullable=False, index=True)
    status = Column(String(50), default="RUNNING", nullable=False, index=True)  # RUNNING, COMPLETED, FAILED, TIMED_OUT
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    customer_filter = Column(String(255), nullable=True)

    # Aggregate Metrics
    total_transactions = Column(Integer, default=0, nullable=False)
    matched_count = Column(Integer, default=0, nullable=False)
    missing_po_count = Column(Integer, default=0, nullable=False)
    amount_mismatch_count = Column(Integer, default=0, nullable=False)
    total_exceptions = Column(Integer, default=0, nullable=False)
    total_order_value = Column(Float, default=0.0, nullable=False)
    exception_order_value = Column(Float, default=0.0, nullable=False)
    mismatch_value_percentage = Column(Float, default=0.0, nullable=False)
    exception_rate_by_count = Column(Float, default=0.0, nullable=False)
    exception_rate_by_value = Column(Float, default=0.0, nullable=False)

    execution_plan_id = Column(Integer, ForeignKey("execution_plans.id"), nullable=True)
    started_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    metrics_payload = Column(JSON, nullable=True)

    kri = relationship("KRI", back_populates="audit_runs")
    exceptions = relationship("AuditException", back_populates="audit_run", cascade="all, delete-orphan")
    tool_invocations = relationship("AuditToolInvocation", back_populates="audit_run", cascade="all, delete-orphan")
    datasets = relationship("ExecutionDataset", back_populates="audit_run", cascade="all, delete-orphan")
    evidence_records = relationship("EvidenceRecord", back_populates="audit_run", cascade="all, delete-orphan")
    run_logs = relationship("AuditRunLog", back_populates="audit_run", cascade="all, delete-orphan")


class AuditException(Base):
    """Audit Exceptions flagged during execution."""
    __tablename__ = "audit_exceptions"

    id = Column(Integer, primary_key=True, index=True)
    audit_run_id = Column(Integer, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    exception_reference = Column(String(100), unique=True, nullable=False, index=True)
    order_id = Column(String(100), nullable=False, index=True)
    po_id = Column(String(100), nullable=True, index=True)
    exception_type = Column(String(50), nullable=False, index=True)  # AMOUNT_MISMATCH, MISSING_PO, DUPLICATE_MATCH, AMBIGUOUS_MATCH
    severity = Column(String(50), default="MEDIUM", nullable=False, index=True)  # LOW, MEDIUM, HIGH, CRITICAL
    reason_code = Column(String(100), nullable=False)
    order_amount = Column(Float, nullable=False)
    po_amount = Column(Float, nullable=True)
    difference_amount = Column(Float, nullable=True)
    difference_percentage = Column(Float, nullable=True)
    threshold_value = Column(Float, nullable=True)
    explanation = Column(Text, nullable=False)
    review_status = Column(String(50), default="PENDING_REVIEW", nullable=False, index=True)  # PENDING_REVIEW, APPROVED, REJECTED, SUPPRESSED
    evidence_reference = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    audit_run = relationship("AuditRun", back_populates="exceptions")


class AuditToolInvocation(Base):
    """Tool invocation trace for auditability and governance."""
    __tablename__ = "audit_tool_invocations"

    id = Column(Integer, primary_key=True, index=True)
    audit_run_id = Column(Integer, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)
    tool_name = Column(String(100), nullable=False, index=True)
    input_payload = Column(JSON, nullable=False)
    output_payload = Column(JSON, nullable=False)
    status = Column(String(50), default="SUCCESS", nullable=False)  # SUCCESS, ERROR
    error_message = Column(Text, nullable=True)
    execution_time_ms = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    audit_run = relationship("AuditRun", back_populates="tool_invocations")


class ExecutionDataset(Base):
    """Scoped intermediate datasets stored per audit run."""
    __tablename__ = "execution_datasets"

    id = Column(Integer, primary_key=True, index=True)
    audit_run_id = Column(Integer, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_reference = Column(String(100), nullable=False, index=True)
    entity_name = Column(String(50), nullable=False)
    source_system = Column(String(50), nullable=False)
    record_count = Column(Integer, default=0, nullable=False)
    dataset_payload = Column(JSON, nullable=False)  # Store serialized records safely
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_dataset_run_ref", "audit_run_id", "dataset_reference", unique=True),
    )

    audit_run = relationship("AuditRun", back_populates="datasets")


class EvidenceRecord(Base):
    """Complete, reproducible audit evidence record for a flagged exception."""
    __tablename__ = "evidence_records"

    id = Column(Integer, primary_key=True, index=True)
    audit_run_id = Column(Integer, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_reference = Column(String(100), unique=True, nullable=False, index=True)
    exception_reference = Column(String(100), nullable=True, index=True)
    order_id = Column(String(100), nullable=False, index=True)
    source_system = Column(String(50), nullable=False)
    order_payload = Column(JSON, nullable=False)
    po_payload = Column(JSON, nullable=True)
    calculation_details = Column(JSON, nullable=False)
    threshold_details = Column(JSON, nullable=False)
    explanation = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    audit_run = relationship("AuditRun", back_populates="evidence_records")



class AuditRunLog(Base):
    """Execution step-by-step logs for an audit run."""
    __tablename__ = "audit_run_logs"

    id = Column(Integer, primary_key=True, index=True)
    audit_run_id = Column(Integer, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number = Column(Integer, default=0, nullable=False)
    stage = Column(String(50), nullable=False, index=True)  # RUN_INIT, PLAN_LOADED, LLM_PROMPT, LLM_RESPONSE, TOOL_CALL, EXCEPTION_FLAGGED, METRICS_CALCULATED, EVIDENCE_BUILT, RUN_COMPLETED, ERROR
    message = Column(Text, nullable=False)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    audit_run = relationship("AuditRun", back_populates="run_logs")

