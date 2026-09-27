"""Pydantic Request and Response Schemas for KRI Configuration."""

from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.common import (
    IndicatorTypeEnum,
    KRIStatusEnum,
    OperationTypeEnum,
    PlanInterpreterModeEnum,
    PlanSourceEnum,
    PlanStepOriginEnum,
    ThresholdTypeEnum,
    ComparisonOperatorEnum,
)


# Process Area Schemas
class ProcessAreaBase(BaseModel):
    name: str = Field(..., max_length=100)
    code: str = Field(..., max_length=50)
    description: Optional[str] = None


class ProcessAreaResponse(ProcessAreaBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# Data Source Schemas
class DataSourceBase(BaseModel):
    name: str = Field(..., max_length=100)
    code: str = Field(..., max_length=50)
    system_type: str = Field(..., max_length=50)
    description: Optional[str] = None


class DataSourceEntityResponse(BaseModel):
    entity_code: str
    is_primary: bool = True
    is_active: bool = True
    model_config = ConfigDict(from_attributes=True)


class DataSourceResponse(DataSourceBase):
    id: int
    is_queryable: bool = False
    availability_note: Optional[str] = None
    entity_codes: List[str] = Field(default_factory=list)
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# Capability Manifest Schemas
class CapabilityField(BaseModel):
    name: str
    type: str
    nullable: bool
    role: str


class CapabilityEntity(BaseModel):
    entity_code: str
    aliases: List[str] = Field(default_factory=list)
    population_role: str = "population"
    fields: List[CapabilityField] = Field(default_factory=list)
    match_fields: List[str] = Field(default_factory=list)
    date_column: str
    amount_column: str
    id_column: str
    record_key_field: str
    fallback_match: Optional[Dict[str, str]] = None
    sample_rows: Optional[List[Dict[str, Any]]] = None


class CapabilityDataSource(BaseModel):
    code: str
    name: str
    system_type: str
    description: Optional[str] = None
    is_queryable: bool = False
    aliases: List[str] = Field(default_factory=list)
    entities: List[CapabilityEntity] = Field(default_factory=list)


class UnboundSource(BaseModel):
    code: str
    name: str
    reason: str


class EntityPair(BaseModel):
    left_entity: str
    right_entity: str
    shared_match_fields: List[str] = Field(default_factory=list)


class CapabilityManifest(BaseModel):
    """The only enumeration of legal data sources/entities/fields for a KRI (R7)."""

    kri_id: Optional[int] = None
    kri_identifier: Optional[str] = None
    data_sources: List[CapabilityDataSource] = Field(default_factory=list)
    unavailable_data_sources: List[CapabilityDataSource] = Field(default_factory=list)
    unbound_assigned_sources: List[UnboundSource] = Field(default_factory=list)
    reachable_entities: List[str] = Field(default_factory=list)
    entity_pairs: List[EntityPair] = Field(default_factory=list)


class OperationDescriptor(BaseModel):
    """Drives the UI operation dropdown; keeps the frontend free of hardcoded names."""

    operation: str
    tool_name: str
    description: str
    required_parameters: List[str] = Field(default_factory=list)


# Test Step Schemas
class KRITestStepCreate(BaseModel):
    step_number: int = Field(..., ge=1)
    title: str = Field(..., max_length=255)
    instruction: str = Field(..., min_length=5)
    is_active: bool = True
    operation: Optional[OperationTypeEnum] = None
    parameters: Optional[Dict[str, Any]] = None
    data_source_id: Optional[int] = None
    entity_code: Optional[str] = None
    expected_output: Optional[str] = None


class KRITestStepUpdate(BaseModel):
    step_number: Optional[int] = Field(None, ge=1)
    title: Optional[str] = None
    instruction: Optional[str] = None
    is_active: Optional[bool] = None
    operation: Optional[OperationTypeEnum] = None
    parameters: Optional[Dict[str, Any]] = None
    data_source_id: Optional[int] = None
    entity_code: Optional[str] = None
    expected_output: Optional[str] = None


class KRITestStepResponse(BaseModel):
    id: int
    kri_id: int
    step_number: int
    title: str
    instruction: str
    is_active: bool
    operation: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    data_source_id: Optional[int] = None
    entity_code: Optional[str] = None
    expected_output: Optional[str] = None
    content_hash: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TestStepBulkUpdate(BaseModel):
    """Full ordered replacement of a KRI's test steps, written and interpreted atomically."""

    steps: List[KRITestStepCreate] = Field(..., min_length=1)
    data_source_ids: Optional[List[int]] = None


class StepReorderItem(BaseModel):
    step_id: int
    new_step_number: int = Field(..., ge=1)


class StepReorderRequest(BaseModel):
    reorder_list: List[StepReorderItem]


# Threshold Schemas
class KRIThresholdCreate(BaseModel):
    name: str = Field(..., max_length=100)
    threshold_type: ThresholdTypeEnum = ThresholdTypeEnum.PERCENTAGE_DIFFERENCE
    operator: ComparisonOperatorEnum = ComparisonOperatorEnum.GREATER_THAN
    threshold_value: float
    key: Optional[str] = None
    unit: Optional[str] = None
    is_active: bool = True


class KRIThresholdUpdate(BaseModel):
    name: Optional[str] = None
    threshold_type: Optional[ThresholdTypeEnum] = None
    operator: Optional[ComparisonOperatorEnum] = None
    threshold_value: Optional[float] = None
    key: Optional[str] = None
    unit: Optional[str] = None
    is_active: Optional[bool] = None


class KRIThresholdResponse(BaseModel):
    id: int
    kri_id: int
    name: str
    threshold_type: str
    operator: str
    threshold_value: float
    key: Optional[str] = None
    unit: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# Schedule Schemas
class KRIScheduleCreate(BaseModel):
    run_frequency: str = Field(default="DAILY")
    fetch_data_delay_days: int = Field(default=1, ge=0)
    align_to_close_calendar: bool = False
    population_percentage: float = Field(default=100.0, ge=0.0, le=100.0)
    custom_date: Optional[str] = None


class KRIScheduleResponse(KRIScheduleCreate):
    id: int
    kri_id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# Reviewer Schemas
class ReviewerCreate(BaseModel):
    reviewer_name: str
    reviewer_email: str
    human_review_setting: str = "ALL_EXCEPTIONS"
    lifecycle_status: str = "ACTIVE"


class ReviewerResponse(ReviewerCreate):
    id: int
    kri_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# KRI Schemas
class KRICreate(BaseModel):
    identifier: str = Field(..., max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(..., max_length=255)
    process_area_id: int
    indicator_type: IndicatorTypeEnum = IndicatorTypeEnum.LEADING
    risk_description: str = Field(..., min_length=10)
    end_goal: str = Field(..., min_length=10)
    data_source_ids: List[int] = Field(default_factory=list)
    owner: Optional[str] = "Internal Audit"
    note: Optional[str] = None


class KRIUpdate(BaseModel):
    name: Optional[str] = None
    process_area_id: Optional[int] = None
    indicator_type: Optional[IndicatorTypeEnum] = None
    risk_description: Optional[str] = None
    end_goal: Optional[str] = None
    data_source_ids: Optional[List[int]] = None
    owner: Optional[str] = None
    note: Optional[str] = None


class KRIStatusUpdate(BaseModel):
    status: KRIStatusEnum


class KRIResponse(BaseModel):
    id: int
    identifier: str
    name: str
    process_area_id: int
    process_area: Optional[ProcessAreaResponse] = None
    indicator_type: str
    risk_description: str
    end_goal: str
    status: str
    owner: Optional[str] = "Internal Audit"
    note: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    validated_at: Optional[datetime] = None
    data_sources: List[DataSourceResponse] = Field(default_factory=list)
    test_steps: List[KRITestStepResponse] = Field(default_factory=list)
    thresholds: List[KRIThresholdResponse] = Field(default_factory=list)
    schedules: List[KRIScheduleResponse] = Field(default_factory=list)
    reviewers: List[ReviewerResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


# Plan Generation and Validation Schemas
class PlannedStepItem(BaseModel):
    """One executable step of the interpreted execution plan."""

    step_number: int
    operation: str
    tool_name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None
    # Data source resolution (R7) - only populated for extract operations.
    data_source_id: Optional[int] = None
    data_source_code: Optional[str] = None
    entity_code: Optional[str] = None
    selector_reason: Optional[str] = None
    # Provenance (R5)
    origin: str = PlanStepOriginEnum.RULES.value
    rationale: Optional[str] = None


class ExceptionRule(BaseModel):
    """Declarative rule turning a comparison bucket into candidate exceptions."""

    rule: str
    source_bucket: str
    exception_type: str
    create_exception: bool = True
    only_if_threshold_breached: bool = False
    severity_override: Optional[str] = None
    reason_code: Optional[str] = None


class DataSourceUse(BaseModel):
    """Summary of one read target used by the plan, for audit attribution."""

    data_source_id: int
    data_source_code: str
    entity_code: str
    extract_step_number: int
    selector_reason: Optional[str] = None


class StructuredPlan(BaseModel):
    """The authoritative execution contract consumed by the plan executor (R1)."""

    kri_id: int
    version: int = 1
    steps: List[PlannedStepItem] = Field(default_factory=list)
    # The single variance limit the exception rules are evaluated against. Resolved once at
    # interpretation time (from the KRI threshold row, or from an explicit percentage in a
    # step) so execution never has to guess it.
    threshold: Optional[float] = None
    threshold_origin: Optional[str] = None
    required_stages: List[str] = Field(default_factory=list)
    exception_rules: List[ExceptionRule] = Field(default_factory=list)
    data_sources_used: List[DataSourceUse] = Field(default_factory=list)
    interpreter_meta: Dict[str, Any] = Field(default_factory=dict)


class PlanValidationResult(BaseModel):
    is_valid: bool
    kri_id: int
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    plan: Optional[StructuredPlan] = None
    steps_hash: Optional[str] = None
    manifest: Optional[CapabilityManifest] = None


class PlanStepInterpretation(BaseModel):
    """Per-step interpretation feedback surfaced to the user when a save is rejected (R4)."""

    step_number: Optional[int] = None
    step_id: Optional[int] = None
    title: Optional[str] = None
    error: str
    candidates: List[str] = Field(default_factory=list)


class PlanInterpretationErrorResponse(BaseModel):
    """422 body: the user's steps could not be turned into a runnable plan."""

    message: str
    steps: List[PlanStepInterpretation] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class ExecutionPlanSummary(BaseModel):
    id: int
    kri_id: int
    version: int
    source: str
    is_valid: bool
    plan_hash: Optional[str] = None
    steps_hash: Optional[str] = None
    is_current: bool = False
    step_count: int = 0
    validation_errors: List[str] = Field(default_factory=list)
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ExecutionPlanDetail(ExecutionPlanSummary):
    plan_payload: Dict[str, Any]
    interpreter_meta: Optional[Dict[str, Any]] = None
    created_by: Optional[str] = None


class PlanDiffEntry(BaseModel):
    step_number: int
    change: str  # ADDED | REMOVED | CHANGED | UNCHANGED
    before: Optional[PlannedStepItem] = None
    after: Optional[PlannedStepItem] = None


class PlanDiffResponse(BaseModel):
    from_version: int
    to_version: int
    from_plan_id: int
    to_plan_id: int
    entries: List[PlanDiffEntry] = Field(default_factory=list)
