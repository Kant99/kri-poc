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
    AnalyzeOIDebookingsInput,
    AnalyzeOIDebookingsOutput,
    DebookingRule,
    AnalyzeWBSIntegrityInput,
    AnalyzeWBSIntegrityOutput,
    WBSRule,
)
from app.tools.fetch_financial_data import fetch_financial_data_handler
from app.tools.compare_records import compare_records_handler
from app.tools.calculate_difference import calculate_difference_handler
from app.tools.apply_threshold import apply_threshold_handler
from app.tools.calculate_kri_metrics import calculate_kri_metrics_handler
from app.tools.build_evidence import build_evidence_handler
from app.tools.generate_explanation import generate_explanation_handler
from app.tools.analyze_oi_debookings import analyze_oi_debookings_handler
from app.tools.analyze_wbs_integrity import analyze_wbs_integrity_handler


def register_all_tools() -> None:
    """Register all deterministic audit tools into the central registry."""
    # Bootstrapping the capability registry here guarantees the allowlists and the
    # capability manifest are populated before any tool validates its arguments.
    from app.services.data_source_bootstrap import register_all_entities

    register_all_entities()

    # Tool 1: fetch_financial_data
    registry.register(
        RegisteredTool(
            name="fetch_financial_data",
            description=(
                "Extract records for a registered business entity from a catalog data source "
                "over a date window. The data source and entity must be bound in the capability "
                "registry; the run's dates and filters are bound by the execution plan."
            ),
            input_model=FetchFinancialDataInput,
            output_model=FetchFinancialDataOutput,
            handler=fetch_financial_data_handler,
        )
    )

    # Tool 2: compare_records
    registry.register(
        RegisteredTool(
            name="compare_records",
            description=(
                "Match records deterministically between two scoped datasets on a shared field "
                "resolved from the execution plan, identifying matched, unmatched and ambiguous "
                "records."
            ),
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
            description=(
                "Calculate aggregate KRI audit metrics for the population dataset bound by the "
                "execution plan, including exception counts, rates and severity breakdowns."
            ),
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

    # Tool 8: analyze_oi_debookings
    registry.register(
        RegisteredTool(
            name="analyze_oi_debookings",
            description=(
                "Net the order intake population against order intake debookings per customer per "
                "quarter, and report timing, support and concentration findings that indicate "
                "booking-quality issues, premature revenue recognition, or recognition that was "
                "never supported."
            ),
            input_model=AnalyzeOIDebookingsInput,
            output_model=AnalyzeOIDebookingsOutput,
            handler=analyze_oi_debookings_handler,
        )
    )

    # Tool 9: analyze_wbs_integrity
    registry.register(
        RegisteredTool(
            name="analyze_wbs_integrity",
            description=(
                "Reconcile Work Breakdown Structure (WBS) master records, commercial opportunities (SCRM), "
                "Order Intake, Customer Purchase Orders, recognized revenue (YRA report), and actual costs "
                "(YCA report) to detect multi-opportunity commingling, multi-customer commingling, missing POs, "
                "revenue over-recognition, and orphan cost parking."
            ),
            input_model=AnalyzeWBSIntegrityInput,
            output_model=AnalyzeWBSIntegrityOutput,
            handler=analyze_wbs_integrity_handler,
        )
    )


# Automatically register on import
register_all_tools()

__all__ = ["registry", "register_all_tools"]
