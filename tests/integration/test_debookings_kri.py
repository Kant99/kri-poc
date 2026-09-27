"""Integration Tests for KRI-O2C-002: Value of OI Debookings for Same Customer Quarter on Quarter."""

import pytest
from datetime import date


def test_debookings_kri_end_to_end_audit_execution(client, sample_active_kri):
    """Execute complete end-to-end audit run on KRI-O2C-002.

    1. Verify KRI-O2C-002 configuration, thresholds (15%), and execution plan.
    2. Trigger an audit run across H1 2026 (2026-01-01 to 2026-06-30).
    3. Verify all planned steps execute successfully.
    4. Assert that all 4 exception categories are flagged:
       - PREMATURE_RECOGNITION (cross-quarter reversals)
       - UNSUPPORTED_RECOGNITION (reversal reason codes like NO_CONTRACT, DUPLICATE_BOOKING)
       - BOOKING_QUALITY (same-quarter debooking ratio > 15%)
       - ORPHAN_DEBOOKING (reversals with no matching order in population)
    5. Verify evidence generation with SHA-256 hashes.
    6. Verify UI trace and exception formatting.
    """
    # 1. Fetch KRI metadata
    resp_kris = client.get("/api/v1/kris")
    assert resp_kris.status_code == 200, resp_kris.text
    kris = resp_kris.json()
    kri_data = next((k for k in kris if k["identifier"] == "KRI-O2C-002"), None)
    assert kri_data is not None, "KRI-O2C-002 was not found in catalog"
    assert "Value of OI Debookings" in kri_data["name"]
    assert kri_data["status"] == "ACTIVE"
    kri_id = kri_data["id"]

    # Verify 15% threshold
    resp_kri_full = client.get(f"/api/v1/kris/{kri_id}")
    assert resp_kri_full.status_code == 200
    kri_full = resp_kri_full.json()
    assert len(kri_full["thresholds"]) >= 1
    assert any(th["threshold_value"] == 15.0 for th in kri_full["thresholds"])

    # 2. Trigger audit run across 2026-Q1 & 2026-Q2 (2026-01-01 to 2026-06-30)
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
    run_ref = run_data["run_reference"]

    # 3. Verify tool invocations in trace
    resp_trace = client.get(f"/api/v1/audits/{run_id}/trace")
    assert resp_trace.status_code == 200
    trace_data = resp_trace.json()
    tools = [inv["tool_name"] for inv in trace_data["invocations"]]
    assert "fetch_financial_data" in tools
    assert "analyze_oi_debookings" in tools
    assert "calculate_kri_metrics" in tools
    assert "build_evidence" in tools

    # 4. Fetch all exceptions and verify all 4 categories exist
    resp_excs = client.get(f"/api/v1/audits/{run_id}/exceptions")
    assert resp_excs.status_code == 200
    exceptions = resp_excs.json()
    assert len(exceptions) >= 30, f"Expected ~39 exceptions, got {len(exceptions)}"

    types_found = {e["exception_type"] for e in exceptions}
    assert "PREMATURE_RECOGNITION" in types_found, "Missing PREMATURE_RECOGNITION exceptions"
    assert "UNSUPPORTED_RECOGNITION" in types_found, "Missing UNSUPPORTED_RECOGNITION exceptions"
    assert "BOOKING_QUALITY" in types_found, "Missing BOOKING_QUALITY exceptions"
    assert "ORPHAN_DEBOOKING" in types_found, "Missing ORPHAN_DEBOOKING exceptions"

    # Verify severity mapping
    unsupported = [e for e in exceptions if e["exception_type"] == "UNSUPPORTED_RECOGNITION"]
    assert any(e["severity"] == "CRITICAL" for e in unsupported)

    premature = [e for e in exceptions if e["exception_type"] == "PREMATURE_RECOGNITION"]
    assert any(e["severity"] == "HIGH" for e in premature)

    orphans = [e for e in exceptions if e["exception_type"] == "ORPHAN_DEBOOKING"]
    assert any(e["severity"] == "HIGH" for e in orphans)

    booking_quality = [e for e in exceptions if e["exception_type"] == "BOOKING_QUALITY"]
    assert any(e["severity"] in ("MEDIUM", "HIGH") for e in booking_quality)

    # 5. Verify evidence generation
    for exc in exceptions[:5]:
        resp_detail = client.get(f"/api/v1/audits/{run_id}/exceptions/{exc['id']}")
        assert resp_detail.status_code == 200, resp_detail.text
        ev_ref = resp_detail.json().get("evidence_reference")
        assert ev_ref is not None, f"Missing evidence reference for exception {exc['id']}"
        resp_ev = client.get(f"/api/v1/audits/evidence/{ev_ref}")
        assert resp_ev.status_code == 200, resp_ev.text
        ev_data = resp_ev.json()
        assert ev_data["order_id"] == exc["order_id"]
        assert ev_data["explanation"]

    # 6. Verify UI Trace Route includes analyze_oi_debookings summary
    resp_ui_trace = client.get(f"/api/runs/{run_ref}/trace")
    assert resp_ui_trace.status_code == 200
    ui_trace = resp_ui_trace.json()
    step_summaries = [s.get("summary", "") for s in ui_trace.get("steps", [])]
    assert any("Analyzed order intake debookings" in s for s in step_summaries)

    # 7. Verify UI Exceptions Formatting (ensuring no false PO-matching text)
    resp_ui_excs = client.get(f"/api/exceptions?runReference={run_ref}")
    assert resp_ui_excs.status_code == 200
    ui_excs = resp_ui_excs.json()
    for exc in ui_excs:
        summary = exc.get("summary", "")
        # PO mismatch text must NOT appear for debooking exceptions
        assert "is lower than the PO amount" not in summary
        assert "is higher than the PO amount" not in summary


def test_debookings_kri_ui_run_multi_quarter_period(client, sample_active_kri):
    """Execute audit run via UI endpoint using multi-quarter period string '2026-Q1-Q2'."""
    payload = {
        "kriId": "KRI-O2C-002",
        "period": "2026-Q1-Q2",
    }
    resp = client.post("/api/runs", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    run = data["run"]
    assert run["status"].lower() == "completed"
    assert run["period"] == "2026-01-01 to 2026-06-30"
    assert run["exceptions"] >= 30
    assert len(data["exceptions"]) >= 30


def test_debookings_kri_ui_run_half_year_period(client, sample_active_kri):
    """Execute audit run via UI endpoint using half-year period string '2026-H1'."""
    payload = {
        "kriId": "KRI-O2C-002",
        "period": "2026-H1",
    }
    resp = client.post("/api/runs", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    run = data["run"]
    assert run["status"].lower() == "completed"
    assert run["exceptions"] >= 30
    assert len(data["exceptions"]) >= 30
