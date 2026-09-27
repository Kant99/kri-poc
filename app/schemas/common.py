"""Common Enums and Shared Schemas."""

from enum import Enum
from typing import Generic, TypeVar, Optional, List, Any
from pydantic import BaseModel

T = TypeVar("T")


class IndicatorTypeEnum(str, Enum):
    LEADING = "LEADING"
    LAGGING = "LAGGING"


class KRIStatusEnum(str, Enum):
    DRAFT = "DRAFT"
    IN_ASSESSMENT = "IN_ASSESSMENT"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class SourceSystemEnum(str, Enum):
    SAP_ECC = "SAP_ECC"
    SAPIENS = "SAPIENS"
    RED_BOX_PO = "RED_BOX_PO"
    BLUE_PLANET = "BLUE_PLANET"
    EMS_SHAREPOINT = "EMS_SHAREPOINT"


class FinancialEntityEnum(str, Enum):
    ORDER_INTAKE = "ORDER_INTAKE"
    PURCHASE_ORDER = "PURCHASE_ORDER"


class OperationTypeEnum(str, Enum):
    """Approved audit operations that a test step may be interpreted into.

    These are the only legal values for ``KRITestStep.operation`` and
    ``PlannedStepItem.operation``. The value doubles as the key into
    ``app.services.plan_service.OPERATION_TOOL_MAP``.
    """

    EXTRACT_POPULATION = "EXTRACT_POPULATION"
    FETCH_DATA = "FETCH_DATA"
    MATCH_RECORDS = "MATCH_RECORDS"
    COMPARE_RECORDS = "COMPARE_RECORDS"
    COMPARE_AMOUNTS = "COMPARE_AMOUNTS"
    CALCULATE_DIFFERENCE = "CALCULATE_DIFFERENCE"
    APPLY_THRESHOLD = "APPLY_THRESHOLD"
    EVALUATE_THRESHOLD = "EVALUATE_THRESHOLD"
    CALCULATE_METRICS = "CALCULATE_METRICS"
    BUILD_EVIDENCE = "BUILD_EVIDENCE"
    GENERATE_EXPLANATION = "GENERATE_EXPLANATION"
    ANALYZE_DEBOOKINGS = "ANALYZE_DEBOOKINGS"
    PREPARE = "PREPARE"


class PlanInterpreterModeEnum(str, Enum):
    """How natural language test steps are translated into a structured plan."""

    RULES = "rules"
    LLM = "llm"
    HYBRID = "hybrid"


class PlanSourceEnum(str, Enum):
    """How a persisted execution plan was produced (provenance / R5)."""

    RULES = "RULES"
    LLM = "LLM"
    MIXED = "MIXED"


class PlanStepOriginEnum(str, Enum):
    """How an individual planned step was produced (provenance / R5)."""

    PINNED = "PINNED"
    LLM = "LLM"
    RULES = "RULES"
    INJECTED = "INJECTED"


class ThresholdTypeEnum(str, Enum):
    PERCENTAGE_DIFFERENCE = "PERCENTAGE_DIFFERENCE"
    ABSOLUTE_DIFFERENCE = "ABSOLUTE_DIFFERENCE"


class ComparisonOperatorEnum(str, Enum):
    GREATER_THAN = ">"
    GREATER_EQUAL = ">="
    LESS_THAN = "<"
    LESS_EQUAL = "<="
    EQUALS = "=="
    NOT_EQUALS = "!="


class ExceptionTypeEnum(str, Enum):
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    MISSING_PO = "MISSING_PO"
    DUPLICATE_MATCH = "DUPLICATE_MATCH"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    # Order Intake debooking / revenue recognition quality
    PREMATURE_RECOGNITION = "PREMATURE_RECOGNITION"
    UNSUPPORTED_RECOGNITION = "UNSUPPORTED_RECOGNITION"
    BOOKING_QUALITY = "BOOKING_QUALITY"
    DEBOOKING_CONCENTRATION = "DEBOOKING_CONCENTRATION"
    ORPHAN_DEBOOKING = "ORPHAN_DEBOOKING"


class SeverityEnum(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ReviewStatusEnum(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPPRESSED = "SUPPRESSED"


class AuditRunStatusEnum(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class StandardResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str = "Operation successful"
    data: Optional[T] = None
    errors: Optional[List[str]] = None
