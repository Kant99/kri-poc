"""Integration & Unit Tests for Leading KRI-WBS-001: Work Breakdown Structure (WBS) Usage Accuracy."""

import pytest
from datetime import date
from app.schemas.tools import AnalyzeWBSIntegrityInput, WBSRule
from app.tools.analyze_wbs_integrity import analyze_wbs_integrity_handler


def test_wbs_kri_metadata_and_configuration(client, sample_active_kri):
    """Verify KRI-WBS-001 catalog registration, thresholds, and data source bindings."""
    resp = client.get("/api/v1/kris")
    assert resp.status_code == 200, resp.text
    kris = resp.json()

    kri = next((k for k in kris if k["identifier"] == "KRI-WBS-001"), None)
    assert kri is not None, "KRI-WBS-001 not found in KRI catalog"
    assert kri["indicator_type"] == "LEADING"
    assert kri["status"] == "ACTIVE"
    assert "Work Breakdown Structure" in kri["name"]

    # Verify thresholds
    resp_detail = client.get(f"/api/v1/kris/{kri['id']}")
    assert resp_detail.status_code == 200
    detail = resp_detail.json()
    thresholds = detail.get("thresholds", [])
    assert len(thresholds) >= 2
    assert any(th["threshold_value"] == 5.0 for th in thresholds)
    assert any(th["threshold_value"] == 1.0 for th in thresholds)

    # Verify bound sources include SAP_ECC, SCRM, RED_BOX_PO
    source_codes = {ds["code"] for ds in detail.get("data_sources", [])}
    assert "SAP_ECC" in source_codes
    assert "SCRM" in source_codes
    assert "RED_BOX_PO" in source_codes


def test_wbs_kri_end_to_end_audit_execution(client, sample_active_kri):
    """Execute complete end-to-end audit run on KRI-WBS-001 and verify all 5 exception types."""
    resp_kris = client.get("/api/v1/kris")
    kris = resp_kris.json()
    kri_id = next(k["id"] for k in kris if k["identifier"] == "KRI-WBS-001")

    # 1. Trigger audit run across 2026-Q1
    resp_run = client.post(
        "/api/v1/audits/run",
        json={"kri_id": kri_id, "start_date": "2026-01-01", "end_date": "2026-06-30"},
    )
    assert resp_run.status_code == 200, resp_run.text
    run_data = resp_run.json()

    assert run_data["status"] == "COMPLETED", run_data.get("error_message")
    assert run_data["total_transactions"] > 0
    assert run_data["total_exceptions"] > 0
    run_id = run_data["id"]

    # 2. Verify tool trace contains fetch_financial_data, analyze_wbs_integrity, calculate_kri_metrics, build_evidence
    resp_trace = client.get(f"/api/v1/audits/{run_id}/trace")
    assert resp_trace.status_code == 200
    trace_data = resp_trace.json()
    tools = [inv["tool_name"] for inv in trace_data["invocations"]]
    assert "fetch_financial_data" in tools
    assert "analyze_wbs_integrity" in tools
    assert "calculate_kri_metrics" in tools
    assert "build_evidence" in tools

    # 3. Verify all 5 key exception categories are detected
    resp_excs = client.get(f"/api/v1/audits/{run_id}/exceptions")
    assert resp_excs.status_code == 200
    exceptions = resp_excs.json()
    types_found = {e["exception_type"] for e in exceptions}

    assert "MULTI_OPPORTUNITY_COMMINGLING" in types_found
    assert "MULTI_CUSTOMER_COMMINGLING" in types_found
    assert "UNCOVERED_WBS" in types_found
    assert "REVENUE_OVER_RECOGNITION" in types_found
    assert "ORPHAN_COST_PARKING" in types_found

    # 4. Verify specific WBS elements and severities
    # Multi-opportunity commingling on WBS-PRJ-COMMINGLED-02
    commingled = [e for e in exceptions if e["exception_type"] == "MULTI_OPPORTUNITY_COMMINGLING"]
    assert any(e["order_id"] == "WBS-PRJ-COMMINGLED-02" and e["severity"] == "CRITICAL" for e in commingled)

    # Multi-customer commingling on WBS-PRJ-MULTICUST-03
    multi_cust = [e for e in exceptions if e["exception_type"] == "MULTI_CUSTOMER_COMMINGLING"]
    assert any(e["order_id"] == "WBS-PRJ-MULTICUST-03" and e["severity"] == "CRITICAL" for e in multi_cust)

    # Uncovered PO on WBS-PRJ-UNCOVERED-04
    uncovered = [e for e in exceptions if e["exception_type"] == "UNCOVERED_WBS"]
    assert any(e["order_id"] == "WBS-PRJ-UNCOVERED-04" and e["severity"] == "HIGH" for e in uncovered)

    # Revenue over-recognition beyond tolerance on WBS-PRJ-REVEXCEED-05
    rev_exceed = [e for e in exceptions if e["exception_type"] == "REVENUE_OVER_RECOGNITION"]
    assert any(e["order_id"] == "WBS-PRJ-REVEXCEED-05" and e["severity"] == "HIGH" for e in rev_exceed)

    # Orphan cost parking on WBS-PRJ-ORPHAN-06
    orphan_costs = [e for e in exceptions if e["exception_type"] == "ORPHAN_COST_PARKING"]
    assert any(e["order_id"] == "WBS-PRJ-ORPHAN-06" and e["severity"] == "HIGH" for e in orphan_costs)

    # 5. Verify tolerance safeguard: WBS-PRJ-TOLERANCE-07 (3% variance within 5% tolerance) is NOT flagged
    tolerance_wbs = [e for e in rev_exceed if e["order_id"] == "WBS-PRJ-TOLERANCE-07"]
    assert len(tolerance_wbs) == 0, "WBS-PRJ-TOLERANCE-07 should not be flagged under 5% tolerance"

    # Clean WBS WBS-PRJ-001 should have no exceptions
    clean_wbs = [e for e in exceptions if e["order_id"] == "WBS-PRJ-001"]
    assert len(clean_wbs) == 0, "Clean WBS-PRJ-001 should have zero exceptions"

    # 6. Verify evidence generation and retrieval
    for exc in exceptions[:5]:
        resp_detail = client.get(f"/api/v1/audits/{run_id}/exceptions/{exc['id']}")
        assert resp_detail.status_code == 200
        ev_ref = resp_detail.json().get("evidence_reference")
        assert ev_ref is not None
        resp_ev = client.get(f"/api/v1/audits/evidence/{ev_ref}")
        assert resp_ev.status_code == 200
        ev_data = resp_ev.json()
        assert ev_data["order_id"] == exc["order_id"]
        assert ev_data["explanation"]


def test_analyze_wbs_integrity_tolerance_safeguard():
    """Unit test for analyze_wbs_integrity handler testing strict vs tolerant revenue checks."""
    from unittest.mock import MagicMock

    mock_context = MagicMock()
    mock_context.datasets = {
        "ds_wbs": [{"wbs_code": "WBS-TEST-01", "project_name": "Test Project"}],
        "ds_opp": [{"opportunity_id": "OPP-01", "customer_name": "Customer A"}],
        "ds_oi": [{"order_id": "ORD-01", "wbs_element": "WBS-TEST-01", "opportunity_id": "OPP-01", "order_amount": 100000.0, "customer_name": "Customer A"}],
        "ds_po": [{"po_id": "PO-01", "wbs_element": "WBS-TEST-01", "opportunity_id": "OPP-01", "po_amount": 100000.0, "customer_name": "Customer A"}],
        "ds_yra": [{"revenue_id": "REV-01", "wbs_element": "WBS-TEST-01", "opportunity_id": "OPP-01", "revenue_amount": 103000.0, "customer_name": "Customer A"}],
        "ds_yca": [{"cost_id": "CST-01", "wbs_element": "WBS-TEST-01", "cost_amount": 60000.0}],
    }
    mock_context.dataset_metadata = {}

    # 1. With 5% tolerance, 103,000 <= 100,000 * 1.05 + 5000 -> NO exception
    out_tolerant = analyze_wbs_integrity_handler(
        AnalyzeWBSIntegrityInput(
            audit_run_reference="RUN-TEST-01",
            wbs_dataset_reference="ds_wbs",
            opportunities_dataset_reference="ds_opp",
            order_intake_dataset_reference="ds_oi",
            customer_pos_dataset_reference="ds_po",
            yra_revenue_dataset_reference="ds_yra",
            yca_cost_dataset_reference="ds_yca",
            revenue_tolerance_percentage=0.05,
            revenue_tolerance_amount=0.0,
        ),
        context=mock_context,
    )
    rev_flags = [f for f in out_tolerant.findings if f["code"] == "REVENUE_OVER_RECOGNITION"]
    assert len(rev_flags) == 0

    # 2. With 0% tolerance and 0 amount buffer, 103,000 > 100,000 -> EXCEPTION flagged
    out_strict = analyze_wbs_integrity_handler(
        AnalyzeWBSIntegrityInput(
            audit_run_reference="RUN-TEST-02",
            wbs_dataset_reference="ds_wbs",
            opportunities_dataset_reference="ds_opp",
            order_intake_dataset_reference="ds_oi",
            customer_pos_dataset_reference="ds_po",
            yra_revenue_dataset_reference="ds_yra",
            yca_cost_dataset_reference="ds_yca",
            revenue_tolerance_percentage=0.0,
            revenue_tolerance_amount=0.0,
        ),
        context=mock_context,
    )
    rev_flags_strict = [f for f in out_strict.findings if f["code"] == "REVENUE_OVER_RECOGNITION"]
    assert len(rev_flags_strict) == 1
    assert rev_flags_strict[0]["excess_amount"] == 3000.0
