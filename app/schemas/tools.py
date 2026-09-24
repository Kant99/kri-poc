"""Pydantic Schemas for All 7 Controlled Audit Tools."""

from datetime import date
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


# Tool 1: fetch_financial_data
class FetchFinancialDataInput(BaseModel):
    source_system: str = Field(..., description="Approved source system (e.g. SAP_ECC, RED_BOX_PO)")
    entity: str = Field(..., description="Financial entity to query (e.g. ORDER_INTAKE, PURCHASE_ORDER)")
    start_date: date = Field(..., description="Start of date window (YYYY-MM-DD)")
    end_date: date = Field(..., description="End of date window (YYYY-MM-DD)")
    filters: Dict[str, Any] = Field(default_factory=dict, description="Optional attribute filters (e.g. customer_name)")


class FetchFinancialDataOutput(BaseModel):
    tool_name: str = "fetch_financial_data"
    status: str = "SUCCESS"
    dataset_reference: str
    entity: str
    record_count: int
    records_sample: List[Dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None


# Tool 2: compare_records
class MatchConfiguration(BaseModel):
    left_field: str = Field(default="order_id", description="Field on left dataset")
    right_field: str = Field(default="order_id", description="Field on right dataset")
    fallback_field: Optional[str] = Field(default="po_reference", description="Fallback field for matching")


class CompareRecordsInput(BaseModel):
    left_dataset_reference: str = Field(..., description="Reference ID for left dataset (orders)")
    right_dataset_reference: str = Field(..., description="Reference ID for right dataset (POs)")
    match_configuration: MatchConfiguration = Field(default_factory=MatchConfiguration)


class CompareRecordsOutput(BaseModel):
    tool_name: str = "compare_records"
    status: str = "SUCCESS"
    comparison_reference: str
    matched_count: int
    unmatched_orders_count: int
    unmatched_pos_count: int
    ambiguous_count: int
    exception_candidates_count: int
    summary: Dict[str, Any] = Field(default_factory=dict)


# Tool 3: calculate_difference
class CalculateDifferenceInput(BaseModel):
    left_value: float = Field(..., description="Order amount")
    right_value: Optional[float] = Field(None, description="PO amount (or None if missing)")
    calculation_type: str = Field(default="ABSOLUTE_PERCENTAGE_DIFFERENCE")
    denominator_policy: str = Field(default="RIGHT_VALUE_ABSOLUTE")


class CalculateDifferenceOutput(BaseModel):
    status: str = "SUCCESS"
    left_value: float
    right_value: Optional[float]
    difference: float
    absolute_difference: float
    difference_percentage: Optional[float]
    note: Optional[str] = None


# Tool 4: apply_threshold
class ApplyThresholdInput(BaseModel):
    value: Optional[float] = Field(..., description="Value to test against threshold (e.g. diff percentage)")
    threshold_type: str = Field(default="PERCENTAGE_DIFFERENCE")
    operator: str = Field(default=">")
    threshold_value: float = Field(..., description="Configured threshold limit")
    is_missing_counterpart: bool = Field(default=False, description="Whether matching record is entirely missing")


class ApplyThresholdOutput(BaseModel):
    is_exception: bool
    reason_code: str
    actual_value: Optional[float]
    threshold_value: float
    operator: str
    severity: str = "MEDIUM"


# Tool 5: calculate_kri_metrics
class MetricCalculationPolicy(BaseModel):
    mismatch_value_definition: str = "ORDER_VALUE_OF_QUALIFYING_EXCEPTIONS"
    include_missing_po_in_mismatch_value: bool = True


class CalculateKRIMetricsInput(BaseModel):
    audit_run_reference: str = Field(..., description="Audit run reference ID")
    calculation_policy: MetricCalculationPolicy = Field(default_factory=MetricCalculationPolicy)


class CalculateKRIMetricsOutput(BaseModel):
    tool_name: str = "calculate_kri_metrics"
    status: str = "SUCCESS"
    total_transactions: int
    total_order_value: float
    matched_count: int
    missing_po_count: int
    amount_mismatch_count: int
    total_exceptions: int
    exception_order_value: float
    mismatch_value_percentage: float
    exception_rate_by_count: float
    exception_rate_by_value: float
    counts_by_exception_type: Dict[str, int]
    counts_by_severity: Dict[str, int]
    metric_definitions: Dict[str, str] = Field(default_factory=dict)


# Tool 6: build_evidence
class BuildEvidenceInput(BaseModel):
    audit_run_reference: str = Field(..., description="Audit run reference ID")
    exception_references: Optional[List[str]] = Field(default=None, description="Optional subset of exception IDs")


class EvidenceDetail(BaseModel):
    evidence_reference: str
    exception_reference: str
    order_id: str
    source_system: str
    order_record: Dict[str, Any]
    po_record: Optional[Dict[str, Any]] = None
    calculation_details: Dict[str, Any]
    threshold_details: Dict[str, Any]
    explanation: str
    reproducibility_hash: Optional[str] = None


class BuildEvidenceOutput(BaseModel):
    tool_name: str = "build_evidence"
    status: str = "SUCCESS"
    audit_run_reference: str
    evidence_count: int
    evidence_records: List[EvidenceDetail] = Field(default_factory=list)


# Tool 7: generate_explanation
class GenerateExplanationInput(BaseModel):
    exception_type: str = Field(..., description="Type of exception (e.g. AMOUNT_MISMATCH, MISSING_PO)")
    calculation: Dict[str, Any] = Field(default_factory=dict, description="Numerical details")
    evidence_reference: Optional[str] = None


class GenerateExplanationOutput(BaseModel):
    status: str = "SUCCESS"
    explanation: str
    reason_code: str
    is_deterministic: bool = True
