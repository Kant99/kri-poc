"""Unit Tests for All 7 Controlled Audit Tools."""

import pytest
from datetime import date
from app.repositories.financial_repository import FinancialRepository
from app.repositories.audit_repository import AuditRepository
from app.services.execution_context import AuditExecutionContext
from app.schemas.tools import (
    FetchFinancialDataInput,
    CompareRecordsInput,
    MatchConfiguration,
    CalculateDifferenceInput,
    ApplyThresholdInput,
    CalculateKRIMetricsInput,
    BuildEvidenceInput,
    GenerateExplanationInput,
)
from app.tools.fetch_financial_data import fetch_financial_data_handler
from app.tools.compare_records import compare_records_handler
from app.tools.calculate_difference import calculate_difference_handler
from app.tools.apply_threshold import apply_threshold_handler
from app.tools.calculate_kri_metrics import calculate_kri_metrics_handler
from app.tools.build_evidence import build_evidence_handler
from app.tools.generate_explanation import generate_explanation_handler


def test_fetch_financial_data_orders(db_session, sample_active_kri):
    """Test Tool 1: fetch_financial_data for Order Intake."""
    fin_repo = FinancialRepository(db_session)
    audit_repo = AuditRepository(db_session)
    run = audit_repo.create_audit_run(sample_active_kri.id, date(2026, 1, 1), date(2026, 3, 31))
    context = AuditExecutionContext(
        run.id, run.run_reference, sample_active_kri, db_session, fin_repo, audit_repo, date(2026, 1, 1), date(2026, 3, 31)
    )

    inp = FetchFinancialDataInput(
        source_system="SAP_ECC",
        entity="ORDER_INTAKE",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 31),
    )
    out = fetch_financial_data_handler(inp, context)
    assert out.status == "SUCCESS"
    assert out.record_count == 80
    assert out.dataset_reference in context.datasets


def test_fetch_financial_data_pos(db_session, sample_active_kri):
    """Test Tool 1: fetch_financial_data for Purchase Orders."""
    fin_repo = FinancialRepository(db_session)
    audit_repo = AuditRepository(db_session)
    run = audit_repo.create_audit_run(sample_active_kri.id, date(2026, 1, 1), date(2026, 3, 31))
    context = AuditExecutionContext(
        run.id, run.run_reference, sample_active_kri, db_session, fin_repo, audit_repo, date(2026, 1, 1), date(2026, 3, 31)
    )

    inp = FetchFinancialDataInput(
        source_system="RED_BOX_PO",
        entity="PURCHASE_ORDER",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 31),
    )
    out = fetch_financial_data_handler(inp, context)
    assert out.status == "SUCCESS"
    assert out.record_count == 74
    assert out.dataset_reference in context.datasets


def test_compare_records_matching(db_session, sample_active_kri):
    """Test Tool 2: compare_records matching orders with POs."""
    fin_repo = FinancialRepository(db_session)
    audit_repo = AuditRepository(db_session)
    run = audit_repo.create_audit_run(sample_active_kri.id, date(2026, 1, 1), date(2026, 3, 31))
    context = AuditExecutionContext(
        run.id, run.run_reference, sample_active_kri, db_session, fin_repo, audit_repo, date(2026, 1, 1), date(2026, 3, 31)
    )

    out_orders = fetch_financial_data_handler(
        FetchFinancialDataInput(source_system="SAP_ECC", entity="ORDER_INTAKE", start_date=date(2026, 1, 1), end_date=date(2026, 3, 31)),
        context,
    )
    out_pos = fetch_financial_data_handler(
        FetchFinancialDataInput(source_system="RED_BOX_PO", entity="PURCHASE_ORDER", start_date=date(2026, 1, 1), end_date=date(2026, 3, 31)),
        context,
    )

    comp_inp = CompareRecordsInput(
        left_dataset_reference=out_orders.dataset_reference,
        right_dataset_reference=out_pos.dataset_reference,
        match_configuration=MatchConfiguration(left_field="order_id", right_field="order_id", fallback_field="po_reference"),
    )
    comp_out = compare_records_handler(comp_inp, context)

    assert comp_out.status == "SUCCESS"
    assert comp_out.matched_count > 0
    assert comp_out.unmatched_orders_count == 8  # 8 missing PO cases
    assert comp_out.ambiguous_count == 2  # 2 ambiguous match cases


def test_calculate_difference():
    """Test Tool 3: calculate_difference deterministic variance calculations."""
    # 20% difference
    out = calculate_difference_handler(CalculateDifferenceInput(left_value=120000.0, right_value=100000.0))
    assert out.status == "SUCCESS"
    assert out.difference == 20000.0
    assert out.absolute_difference == 20000.0
    assert out.difference_percentage == 20.0

    # Missing counterpart
    out_missing = calculate_difference_handler(CalculateDifferenceInput(left_value=50000.0, right_value=None))
    assert out_missing.difference_percentage == 100.0

    # Zero denominator safeguard
    out_zero = calculate_difference_handler(CalculateDifferenceInput(left_value=0.0, right_value=0.0))
    assert out_zero.difference_percentage == 0.0


def test_apply_threshold():
    """Test Tool 4: apply_threshold threshold comparisons and severity assignments."""
    # Within 10% tolerance
    out_within = apply_threshold_handler(ApplyThresholdInput(value=5.0, threshold_value=10.0, operator=">"))
    assert not out_within.is_exception
    assert out_within.reason_code == "WITHIN_TOLERANCE"

    # Exceeding 10% threshold (Medium severity)
    out_exc_med = apply_threshold_handler(ApplyThresholdInput(value=18.0, threshold_value=10.0, operator=">"))
    assert out_exc_med.is_exception
    assert out_exc_med.reason_code == "AMOUNT_MISMATCH"
    assert out_exc_med.severity == "MEDIUM"

    # Exceeding 50% threshold (Critical severity)
    out_exc_crit = apply_threshold_handler(ApplyThresholdInput(value=65.0, threshold_value=10.0, operator=">"))
    assert out_exc_crit.is_exception
    assert out_exc_crit.severity == "CRITICAL"

    # Missing PO counterpart
    out_missing = apply_threshold_handler(ApplyThresholdInput(value=None, threshold_value=10.0, is_missing_counterpart=True))
    assert out_missing.is_exception
    assert out_missing.reason_code == "MISSING_PO"
    assert out_missing.severity == "HIGH"


def test_calculate_kri_metrics(db_session, sample_active_kri):
    """Test Tool 5: calculate_kri_metrics computation of summary metrics."""
    fin_repo = FinancialRepository(db_session)
    audit_repo = AuditRepository(db_session)
    run = audit_repo.create_audit_run(sample_active_kri.id, date(2026, 1, 1), date(2026, 3, 31))
    context = AuditExecutionContext(
        run.id, run.run_reference, sample_active_kri, db_session, fin_repo, audit_repo, date(2026, 1, 1), date(2026, 3, 31)
    )

    # Store fake dataset
    context.store_dataset(
        "dataset_orders_001",
        "ORDER_INTAKE",
        "SAP_ECC",
        [
            {"order_id": "ORD-1", "order_amount": 100.0},
            {"order_id": "ORD-2", "order_amount": 200.0},
        ],
    )
    context.add_candidate_exception({
        "order_id": "ORD-2",
        "exception_type": "AMOUNT_MISMATCH",
        "severity": "MEDIUM",
        "order_amount": 200.0,
    })

    metrics = calculate_kri_metrics_handler(CalculateKRIMetricsInput(audit_run_reference=run.run_reference), context)
    assert metrics.total_transactions == 2
    assert metrics.total_order_value == 300.0
    assert metrics.total_exceptions == 1
    assert metrics.exception_order_value == 200.0
    assert metrics.mismatch_value_percentage == 66.67
    assert metrics.exception_rate_by_count == 50.0


def test_build_evidence(db_session, sample_active_kri):
    """Test Tool 6: build_evidence packaging and provenance hashes."""
    fin_repo = FinancialRepository(db_session)
    audit_repo = AuditRepository(db_session)
    run = audit_repo.create_audit_run(sample_active_kri.id, date(2026, 1, 1), date(2026, 3, 31))
    context = AuditExecutionContext(
        run.id, run.run_reference, sample_active_kri, db_session, fin_repo, audit_repo, date(2026, 1, 1), date(2026, 3, 31)
    )

    context.add_candidate_exception({
        "exception_reference": "exc_001",
        "order_id": "ORD-100",
        "order_amount": 120000.0,
        "po_amount": 100000.0,
        "difference_percentage": 20.0,
        "threshold_value": 10.0,
        "explanation": "Amount mismatch 20% exceeds 10% limit.",
        "order_record": {"order_id": "ORD-100", "customer_name": "Acme", "source_system": "SAP_ECC"},
        "po_record": {"po_id": "PO-100", "source_system": "RED_BOX_PO"},
    })

    out = build_evidence_handler(BuildEvidenceInput(audit_run_reference=run.run_reference), context)
    assert out.status == "SUCCESS"
    assert out.evidence_count == 1
    ev = out.evidence_records[0]
    assert ev.order_id == "ORD-100"
    assert ev.reproducibility_hash is not None


def test_generate_explanation():
    """Test Tool 7: generate_explanation template generation."""
    out_mismatch = generate_explanation_handler(
        GenerateExplanationInput(
            exception_type="AMOUNT_MISMATCH",
            calculation={"order_amount": 120000.0, "po_amount": 100000.0, "difference_percentage": 20.0, "threshold": 10.0},
        )
    )
    assert "exceeding the configured 10.0% threshold" in out_mismatch.explanation
    assert out_mismatch.reason_code == "AMOUNT_MISMATCH"

    out_missing = generate_explanation_handler(
        GenerateExplanationInput(
            exception_type="MISSING_PO",
            calculation={"order_amount": 50000.0, "currency": "USD"},
        )
    )
    assert "has no corresponding purchase order" in out_missing.explanation
    assert out_missing.reason_code == "MISSING_PO"
