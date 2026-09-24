"""Integration Tests for Full End-to-End KRI Workflow and Audit Execution."""

import pytest
from datetime import date


def test_full_kri_audit_lifecycle(client):
    """Execute complete end-to-end flow:

    1. Retrieve meta catalogs (process areas, data sources)
    2. Create new KRI
    3. Add 5 natural language test steps
    4. Add 10% variance threshold
    5. Generate structured execution plan & validate
    6. Activate KRI
    7. Trigger audit run via POST /api/v1/audits/run
    8. Verify calculated metrics & exception counts
    9. Retrieve filtered exceptions
    10. Retrieve audit tool invocation trace
    11. Update exception review status
    12. Retrieve reproducible evidence package
    """
    # 1. Check catalogs
    resp_pa = client.get("/api/v1/kris/meta/process-areas")
    assert resp_pa.status_code == 200
    pa_list = resp_pa.json()
    assert len(pa_list) > 0
    o2c_pa_id = pa_list[0]["id"]

    resp_ds = client.get("/api/v1/kris/meta/data-sources")
    assert resp_ds.status_code == 200
    ds_list = resp_ds.json()
    ds_ids = [d["id"] for d in ds_list]

    # 2. Create KRI
    kri_payload = {
        "identifier": "KRI-INT-TEST-001",
        "name": "Integration Test Order vs PO Audit",
        "process_area_id": o2c_pa_id,
        "indicator_type": "LEADING",
        "risk_description": "Integration testing end-to-end audit risk evaluation.",
        "end_goal": "Validate 100% order to PO alignment within 10% tolerance.",
        "data_source_ids": ds_ids,
    }
    resp_kri = client.post("/api/v1/kris", json=kri_payload)
    assert resp_kri.status_code == 201
    kri_data = resp_kri.json()
    kri_id = kri_data["id"]

    # 3. Add test steps
    steps = [
        {"step_number": 1, "title": "Extract Orders", "instruction": "Extract order intake records from SAP ECC."},
        {"step_number": 2, "title": "Extract POs", "instruction": "Extract purchase orders from Red Box PO."},
        {"step_number": 3, "title": "Match Records", "instruction": "Match order intake with purchase orders on order_id."},
        {"step_number": 4, "title": "Evaluate Variance", "instruction": "Compare amounts and flag variance over 10%."},
        {"step_number": 5, "title": "Metrics and Evidence", "instruction": "Calculate KRI metrics and compile evidence."},
    ]
    for s in steps:
        resp_step = client.post(f"/api/v1/kris/{kri_id}/steps", json=s)
        assert resp_step.status_code == 201

    # 4. Add 10% Threshold
    th_payload = {
        "name": "10% Variance Threshold",
        "threshold_type": "PERCENTAGE_DIFFERENCE",
        "operator": ">",
        "threshold_value": 10.0,
        "is_active": True,
    }
    resp_th = client.post(f"/api/v1/kris/{kri_id}/thresholds", json=th_payload)
    assert resp_th.status_code == 201

    # 5. Generate plan and validate
    resp_plan = client.post(f"/api/v1/kris/{kri_id}/generate-plan")
    assert resp_plan.status_code == 200
    plan_data = resp_plan.json()
    assert len(plan_data["steps"]) >= 5

    resp_val = client.post(f"/api/v1/kris/{kri_id}/validate")
    assert resp_val.status_code == 200
    assert resp_val.json()["is_valid"] is True

    # 6. Activate KRI
    resp_act = client.patch(f"/api/v1/kris/{kri_id}/status", json={"status": "ACTIVE"})
    assert resp_act.status_code == 200
    assert resp_act.json()["status"] == "ACTIVE"

    # 7. Trigger Audit Run
    audit_req = {
        "kri_id": kri_id,
        "start_date": "2026-01-01",
        "end_date": "2026-03-31",
        "customer_name": None,
    }
    resp_run = client.post("/api/v1/audits/run", json=audit_req)
    assert resp_run.status_code == 200
    run_data = resp_run.json()

    # 8. Verify calculated metrics
    assert run_data["status"] == "COMPLETED"
    assert run_data["total_transactions"] == 80
    assert run_data["missing_po_count"] == 8
    assert run_data["amount_mismatch_count"] == 10
    assert run_data["total_exceptions"] == 20
    assert run_data["total_order_value"] > 0
    assert run_data["exception_order_value"] > 0
    assert run_data["mismatch_value_percentage"] > 0
    assert run_data["exception_rate_by_count"] == 25.0

    run_id = run_data["id"]

    # 9. Retrieve filtered exceptions
    resp_excs = client.get(f"/api/v1/audits/{run_id}/exceptions?exception_type=MISSING_PO")
    assert resp_excs.status_code == 200
    missing_excs = resp_excs.json()
    assert len(missing_excs) == 8
    for exc in missing_excs:
        assert exc["exception_type"] == "MISSING_PO"
        assert exc["severity"] == "HIGH"

    resp_mismatch = client.get(f"/api/v1/audits/{run_id}/exceptions?exception_type=AMOUNT_MISMATCH")
    assert resp_mismatch.status_code == 200
    mismatch_excs = resp_mismatch.json()
    assert len(mismatch_excs) == 10

    # 10. Retrieve tool invocation trace
    resp_trace = client.get(f"/api/v1/audits/{run_id}/trace")
    assert resp_trace.status_code == 200
    trace_data = resp_trace.json()
    assert trace_data["total_tool_calls"] >= 5
    tools_in_trace = [inv["tool_name"] for inv in trace_data["invocations"]]
    assert "fetch_financial_data" in tools_in_trace
    assert "compare_records" in tools_in_trace

    # 11. Update exception review status
    first_exc = missing_excs[0]
    exc_ref = first_exc["exception_reference"]
    resp_update_status = client.patch(
        f"/api/v1/audits/exceptions/{exc_ref}/status",
        json={"review_status": "APPROVED"},
    )
    assert resp_update_status.status_code == 200
    assert resp_update_status.json()["review_status"] == "APPROVED"

    # 12. Retrieve evidence record
    exc_detail = client.get(f"/api/v1/audits/{run_id}/exceptions/{first_exc['id']}")
    assert exc_detail.status_code == 200
    ev_ref = exc_detail.json().get("evidence_reference")
    if ev_ref:
        resp_ev = client.get(f"/api/v1/audits/evidence/{ev_ref}")
        assert resp_ev.status_code == 200
        ev_data = resp_ev.json()
        assert ev_data["order_id"] == first_exc["order_id"]
        assert "explanation" in ev_data

    # 13. Retrieve structured execution logs
    resp_logs = client.get(f"/api/v1/audits/{run_id}/logs")
    assert resp_logs.status_code == 200
    logs_data = resp_logs.json()
    assert logs_data["total_events"] > 0
    stages = [ev["stage"] for ev in logs_data["events"]]
    assert "RUN_INITIALIZATION" in stages
    assert "PLAN_LOADED" in stages
    assert "TOOL_EXECUTION" in stages
    assert "EXCEPTION_FLAGGED" in stages
    assert "METRICS_CALCULATED" in stages
    assert "RUN_COMPLETED" in stages

    # 14. Retrieve raw plain text log file
    resp_log_file = client.get(f"/api/v1/audits/{run_id}/logs/file")
    assert resp_log_file.status_code == 200
    assert "CONTINUOUS INTERNAL AUDIT KRI ENGINE - EXECUTION TRACE LOG" in resp_log_file.text
    assert run_data["run_reference"] in resp_log_file.text
    assert "AUDIT RUN FINISHED" in resp_log_file.text

