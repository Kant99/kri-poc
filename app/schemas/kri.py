"""Pydantic Request and Response Schemas for KRI Configuration."""

from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.common import (
    IndicatorTypeEnum,
    KRIStatusEnum,
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


class DataSourceResponse(DataSourceBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# Test Step Schemas
class KRITestStepCreate(BaseModel):
    step_number: int = Field(..., ge=1)
    title: str = Field(..., max_length=255)
    instruction: str = Field(..., min_length=5)
    is_active: bool = True


class KRITestStepUpdate(BaseModel):
    title: Optional[str] = None
    instruction: Optional[str] = None
    is_active: Optional[bool] = None


class KRITestStepResponse(BaseModel):
    id: int
    kri_id: int
    step_number: int
    title: str
    instruction: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


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
    step_number: int
    operation: str
    tool_name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class StructuredPlan(BaseModel):
    kri_id: int
    version: int = 1
    steps: List[PlannedStepItem] = Field(default_factory=list)


class PlanValidationResult(BaseModel):
    is_valid: bool
    kri_id: int
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    plan: Optional[StructuredPlan] = None
