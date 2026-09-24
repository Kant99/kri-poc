"""Security and Governance Validation Helpers.

Ensures strict parameter validation, allowlisting, and prevents SQL/Python execution injection.
"""

import re
from typing import Any, Dict

ALLOWED_SOURCE_SYSTEMS = {"SAP_ECC", "SAPIENS", "RED_BOX_PO", "BLUE_PLANET", "EMS_SHAREPOINT"}
ALLOWED_FINANCIAL_ENTITIES = {"ORDER_INTAKE", "PURCHASE_ORDER"}
ALLOWED_MATCHING_FIELDS = {"order_id", "po_reference", "customer_name", "vendor_id"}
ALLOWED_COMPARISON_OPERATORS = {">", ">=", "<", "<=", "==", "!="}
ALLOWED_CALCULATION_TYPES = {"ABSOLUTE_DIFFERENCE", "PERCENTAGE_DIFFERENCE", "ABSOLUTE_PERCENTAGE_DIFFERENCE"}
ALLOWED_DENOMINATOR_POLICIES = {"RIGHT_VALUE_ABSOLUTE", "LEFT_VALUE_ABSOLUTE", "AVERAGE_ABSOLUTE"}


def validate_source_system(source_system: str) -> str:
    """Validate that the source system is strictly on the allowlist."""
    normalized = source_system.strip().upper().replace(" ", "_").replace("/", "_")
    if normalized not in ALLOWED_SOURCE_SYSTEMS:
        raise ValueError(f"Unauthorized source system '{source_system}'. Allowed: {ALLOWED_SOURCE_SYSTEMS}")
    return normalized


def validate_entity(entity: str) -> str:
    """Validate that the financial entity is strictly on the allowlist."""
    normalized = entity.strip().upper()
    if normalized not in ALLOWED_FINANCIAL_ENTITIES:
        raise ValueError(f"Unauthorized financial entity '{entity}'. Allowed: {ALLOWED_FINANCIAL_ENTITIES}")
    return normalized


def validate_matching_field(field_name: str) -> str:
    """Validate matching field against safe allowlist to prevent arbitrary attribute access."""
    cleaned = field_name.strip().lower()
    if cleaned not in ALLOWED_MATCHING_FIELDS:
        raise ValueError(f"Unauthorized matching field '{field_name}'. Allowed: {ALLOWED_MATCHING_FIELDS}")
    return cleaned


def validate_operator(operator: str) -> str:
    """Validate comparison operator."""
    cleaned = operator.strip()
    if cleaned not in ALLOWED_COMPARISON_OPERATORS:
        raise ValueError(f"Invalid comparison operator '{operator}'. Allowed: {ALLOWED_COMPARISON_OPERATORS}")
    return cleaned


def sanitize_string_input(val: str, max_length: int = 500) -> str:
    """Sanitize string inputs, strip dangerous control characters, and limit length."""
    if not isinstance(val, str):
        return str(val)
    # Remove null bytes and dangerous control characters
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", val)
    return sanitized[:max_length].strip()


def mask_sensitive_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Mask known sensitive keys in dictionaries for safe logging."""
    sensitive_keys = {"api_key", "password", "token", "secret", "authorization"}
    masked = {}
    for k, v in data.items():
        if any(s in k.lower() for s in sensitive_keys):
            masked[k] = "********"
        elif isinstance(v, dict):
            masked[k] = mask_sensitive_dict(v)
        else:
            masked[k] = v
    return masked
