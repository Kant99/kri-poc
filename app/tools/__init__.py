"""Tools package initialization and tool registration."""

from app.tools.base import RegisteredTool
from app.tools.registry import registry
from app.schemas.tools import (
    FetchFinancialDataInput,
    FetchFinancialDataOutput,
    CompareRecordsInput,
    CompareRecordsOutput,
    CalculateDifferenceInput,
    CalculateDifferenceOutput,
    ApplyThresholdInput,
    ApplyThresholdOutput,
    CalculateKRIMetricsInput,
    CalculateKRIMetricsOutput,
    BuildEvidenceInput,
    BuildEvidenceOutput,
    GenerateExplanationInput,
    GenerateExplanationOutput,
)
from app.tools.fetch_financial_data import fetch_financial_data_handler
from app.tools.compare_records import compare_records_handler
from app.tools.calculate_difference import calculate_difference_handler
from app.tools.apply_threshold import apply_threshold_handler
from app.tools.calculate_kri_metrics import calculate_kri_metrics_handler
from app.tools.build_evidence import build_evidence_handler
from app.tools.generate_explanation import generate_explanation_handler


def register_all_tools() -> None:
    """Register all 7 deterministic audit tools into the central registry."""
    # Tool 1: fetch_financial_data
    registry.register(
        RegisteredTool(
            name="fetch_financial_data",
            description="Extract financial records (Order Intake or Purchase Orders) from approved source systems for a given date range.",
            input_model=FetchFinancialDataInput,
            output_model=FetchFinancialDataOutput,
            handler=fetch_financial_data_handler,
        )
    )

    # Tool 2: compare_records
    registry.register(
        RegisteredTool(
            name="compare_records",
            description="Match financial records deterministically between two scoped datasets (e.g. Order Intake vs Purchase Orders).",
            input_model=CompareRecordsInput,
            output_model=CompareRecordsOutput,
            handler=compare_records_handler,
        )
    )

    # Tool 3: calculate_difference
    registry.register(
        RegisteredTool(
            name="calculate_difference",
            description="Calculate numerical amount differences and percentage variances deterministically with zero-denominator safeguards.",
            input_model=CalculateDifferenceInput,
            output_model=CalculateDifferenceOutput,
            handler=calculate_difference_handler,
        )
    )

    # Tool 4: apply_threshold
    registry.register(
        RegisteredTool(
            name="apply_threshold",
            description="Evaluate variance against configured KRI threshold limits and determine exception severity and reason code.",
            input_model=ApplyThresholdInput,
            output_model=ApplyThresholdOutput,
            handler=apply_threshold_handler,
        )
    )

    # Tool 5: calculate_kri_metrics
    registry.register(
        RegisteredTool(
            name="calculate_kri_metrics",
            description="Calculate aggregate KRI audit metrics including population values, exception counts, mismatch rates, and severity distributions.",
            input_model=CalculateKRIMetricsInput,
            output_model=CalculateKRIMetricsOutput,
            handler=calculate_kri_metrics_handler,
        )
    )

    # Tool 6: build_evidence
    registry.register(
        RegisteredTool(
            name="build_evidence",
            description="Construct complete, reproducible audit evidence packages for all identified exceptions with provenance records.",
            input_model=BuildEvidenceInput,
            output_model=BuildEvidenceOutput,
            handler=build_evidence_handler,
        )
    )

    # Tool 7: generate_explanation
    registry.register(
        RegisteredTool(
            name="generate_explanation",
            description="Generate authoritative, plain-English audit finding explanations using deterministic templates.",
            input_model=GenerateExplanationInput,
            output_model=GenerateExplanationOutput,
            handler=generate_explanation_handler,
        )
    )


# Automatically register on import
register_all_tools()

__all__ = ["registry", "register_all_tools"]
