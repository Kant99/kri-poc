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
