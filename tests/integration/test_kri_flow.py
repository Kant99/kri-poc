"""Integration Tests for Full End-to-End KRI Workflow and Audit Execution."""

import pytest
from datetime import date


def test_full_kri_audit_lifecycle(client):
    """Execute complete end-to-end flow:

    1. Retrieve meta catalogs (process areas, data sources, operations, capabilities)
    2. Create a new KRI with a real data source assignment
    3. Add natural language test steps (plan is interpreted on every write)
    4. Add a variance threshold
    5. Inspect the read-only plan history
    6. Activate the KRI
    7. Trigger an audit run, which executes the interpreted plan
    8. Verify calculated metrics, exception counts and source attribution
    9. Retrieve filtered exceptions, trace, evidence and logs
    10. Replay the run from its pinned plan version
    """
    # 1. Check catalogs
    resp_pa = client.get("/api/v1/kris/meta/process-areas")
    assert resp_pa.status_code == 200
    assert len(resp_pa.json()) > 0
    o2c_pa_id = resp_pa.json()[0]["id"]

    resp_ds = client.get("/api/v1/kris/meta/data-sources")
    assert resp_ds.status_code == 200
    ds_list = resp_ds.json()
    by_code = {d["code"]: d for d in ds_list}
    assert by_code["SAP_ECC"]["is_queryable"] is True
    # SAP_ECC is a real ERP source: it carries both the order intake population and the
    # debookings that reverse it, so a source-only mention is genuinely ambiguous.
    assert set(by_code["SAP_ECC"]["entity_codes"]) >= {"ORDER_INTAKE", "OI_DEBOOKING"}
    assert by_code["RED_BOX_PO"]["entity_codes"] == ["PURCHASE_ORDER"]
    # The catalog is honest about what has no data behind it.
    assert by_code["SAPIENS"]["is_queryable"] is False
    assert by_code["SAPIENS"]["entity_codes"] == []

    resp_ops = client.get("/api/v1/kris/meta/operations")
    assert resp_ops.status_code == 200
    assert {o["operation"] for o in resp_ops.json()} >= {
        "EXTRACT_POPULATION", "MATCH_RECORDS", "CALCULATE_METRICS", "BUILD_EVIDENCE"
    }

    resp_caps = client.get("/api/v1/kris/meta/data-source-capabilities")
    assert resp_caps.status_code == 200
    caps = resp_caps.json()
    assert {"SAP_ECC", "RED_BOX_PO"}.issubset({s["code"] for s in caps["data_sources"]})
    # Everything else in the catalog is reported as having nothing queryable behind it.
    assert {u["code"] for u in caps["unavailable_data_sources"]} == set(by_code) - {s["code"] for s in caps["data_sources"]}

    # 2. Create KRI with only the two queryable sources assigned
    kri_payload = {
        "identifier": "KRI-INT-TEST-001",
        "name": "Integration Test Order vs PO Audit",
        "process_area_id": o2c_pa_id,
        "indicator_type": "LEADING",
        "risk_description": "Integration testing end-to-end audit risk evaluation.",
        "end_goal": "Validate 100% order to PO alignment within 10% tolerance.",
        "data_source_ids": [by_code["SAP_ECC"]["id"], by_code["RED_BOX_PO"]["id"]],
    }
    resp_kri = client.post("/api/v1/kris", json=kri_payload)
    assert resp_kri.status_code == 201
    kri_id = resp_kri.json()["id"]

    # 3. Add test steps. Each write re-interprets the plan (R3), so a step that cannot be
    #    resolved is rejected with a 422 instead of being stored.
    steps = [
        {"step_number": 1, "title": "Extract Orders", "instruction": "Extract the population of Order Intake records from SAP ECC for the audit period."},
        {"step_number": 2, "title": "Extract POs", "instruction": "Extract the population of Purchase Orders from Red Box PO for the audit period."},
        {"step_number": 3, "title": "Match Records", "instruction": "Match Order Intake with Purchase Orders using order_id."},
        {"step_number": 4, "title": "Evaluate Variance", "instruction": "Compare amounts and flag every variance exceeding 10%."},
        {"step_number": 5, "title": "Metrics and Evidence", "instruction": "Compile aggregate metrics and reproducible evidence packages."},
    ]

    for index, s in enumerate(steps):
        resp_step = client.post(f"/api/v1/kris/{kri_id}/steps", json=s)
        assert resp_step.status_code == 201, resp_step.text
        if index == 0:
            # A plan that matches two populations needs a variance limit, so the threshold is
            # configured as soon as the first extraction step exists. Step 1 alone does not
            # require one, which is why it can be saved first.
            resp_th = client.post(
                f"/api/v1/kris/{kri_id}/thresholds",
                json={
                    "name": "10% Variance Threshold",
                    "threshold_type": "PERCENTAGE_DIFFERENCE",
                    "operator": ">",
                    "threshold_value": 10.0,
                    "is_active": True,
                },
            )
            assert resp_th.status_code == 201, resp_th.text

    # An unresolvable step is a hard error, and the stored steps are not corrupted.
    resp_bad = client.post(
        f"/api/v1/kris/{kri_id}/steps",
        json={"step_number": 6, "title": "Vague", "instruction": "Consider the situation and decide appropriately."},
    )
    assert resp_bad.status_code == 422
    detail = resp_bad.json()["detail"]
    assert detail["steps"][0]["step_number"] == 6
    assert "could not be mapped to an approved operation" in detail["steps"][0]["error"]
    # The rejected step was rolled back entirely.
    assert len(client.get(f"/api/v1/kris/{kri_id}").json()["test_steps"]) == 5

    # 4. The 10% threshold is configured; confirm it is stored.
    stored = client.get(f"/api/v1/kris/{kri_id}").json()
    assert stored["thresholds"][0]["threshold_value"] == 10.0
    assert len(stored["test_steps"]) == 5

    # 5. Inspect the read-only plan history
    resp_plans = client.get(f"/api/v1/kris/{kri_id}/plans")
    assert resp_plans.status_code == 200
    plans = resp_plans.json()
    assert plans
    latest = plans[0]
    assert latest["is_current"] is True
    assert latest["is_valid"] is True
    assert latest["step_count"] >= 5

    resp_plan = client.get(f"/api/v1/kris/{kri_id}/plans/{latest['id']}")
    assert resp_plan.status_code == 200
    plan_payload = resp_plan.json()["plan_payload"]
    assert plan_payload["data_sources_used"]
    assert {u["data_source_code"] for u in plan_payload["data_sources_used"]} == {"SAP_ECC", "RED_BOX_PO"}
    assert plan_payload["exception_rules"]

    # 6. Validate, then activate
    resp_val = client.post(f"/api/v1/kris/{kri_id}/validate")
    assert resp_val.status_code == 200
    assert resp_val.json()["is_valid"] is True

    resp_act = client.patch(f"/api/v1/kris/{kri_id}/status", json={"status": "ACTIVE"})
    assert resp_act.status_code == 200
    assert resp_act.json()["status"] == "ACTIVE"

    # 7. Trigger the audit run; it executes the interpreted plan, not the step text.
    resp_run = client.post(
        "/api/v1/audits/run",
        json={"kri_id": kri_id, "start_date": "2026-01-01", "end_date": "2026-03-31"},
    )
    assert resp_run.status_code == 200, resp_run.text
    run_data = resp_run.json()

    # 8. Verify calculated metrics
    assert run_data["status"] == "COMPLETED", run_data.get("error_message")
    assert run_data["total_transactions"] == 80
    assert run_data["missing_po_count"] == 8
    assert run_data["amount_mismatch_count"] == 10
    assert run_data["total_exceptions"] == 20
    assert run_data["total_order_value"] > 0
    assert run_data["exception_order_value"] > 0
    assert run_data["mismatch_value_percentage"] > 0
    assert run_data["exception_rate_by_count"] == 25.0

    run_id = run_data["id"]
    run_reference = run_data["run_reference"]

    # 9. Source attribution appears in the tool trace
    resp_trace = client.get(f"/api/v1/audits/{run_id}/trace")
    assert resp_trace.status_code == 200
    trace_data = resp_trace.json()
    tools_in_trace = [inv["tool_name"] for inv in trace_data["invocations"]]
    assert "fetch_financial_data" in tools_in_trace
    assert "compare_records" in tools_in_trace
    reads = [inv for inv in trace_data["invocations"] if inv["tool_name"] == "fetch_financial_data"]
    assert {inv["input_payload"]["data_source_code"] for inv in reads} == {"SAP_ECC", "RED_BOX_PO"}

    # 10. Filtered exceptions
    resp_excs = client.get(f"/api/v1/audits/{run_id}/exceptions?exception_type=MISSING_PO")
    assert resp_excs.status_code == 200
    missing_excs = resp_excs.json()
    assert len(missing_excs) == 8
    for exc in missing_excs:
        assert exc["exception_type"] == "MISSING_PO"
        assert exc["severity"] == "HIGH"

    resp_mismatch = client.get(f"/api/v1/audits/{run_id}/exceptions?exception_type=AMOUNT_MISMATCH")
    assert resp_mismatch.status_code == 200
    assert len(resp_mismatch.json()) == 10

    # 11. Review status
    first_exc = missing_excs[0]
    resp_update = client.patch(
        f"/api/v1/audits/exceptions/{first_exc['exception_reference']}/status",
        json={"review_status": "APPROVED"},
    )
    assert resp_update.status_code == 200
    assert resp_update.json()["review_status"] == "APPROVED"

    # 12. Evidence
    exc_detail = client.get(f"/api/v1/audits/{run_id}/exceptions/{first_exc['id']}")
    assert exc_detail.status_code == 200
    ev_ref = exc_detail.json().get("evidence_reference")
    assert ev_ref
    resp_ev = client.get(f"/api/v1/audits/evidence/{ev_ref}")
    assert resp_ev.status_code == 200
    assert resp_ev.json()["order_id"] == first_exc["order_id"]
    assert resp_ev.json()["explanation"]

    # 13. Logs include the plan lifecycle
    resp_logs = client.get(f"/api/v1/audits/{run_id}/logs")
    assert resp_logs.status_code == 200
    stages = [ev["stage"] for ev in resp_logs.json()["events"]]
    for stage in (
        "RUN_INITIALIZATION", "PLAN_LOADED", "PLAN_STEP_STARTED", "PLAN_STEP_COMPLETED",
        "TOOL_EXECUTION", "EXCEPTION_FLAGGED", "METRICS_CALCULATED", "RUN_COMPLETED",
    ):
        assert stage in stages, f"missing log stage {stage}"

    resp_log_file = client.get(f"/api/v1/audits/{run_id}/logs/file")
    assert resp_log_file.status_code == 200
    assert "CONTINUOUS INTERNAL AUDIT KRI ENGINE - EXECUTION TRACE LOG" in resp_log_file.text
    assert run_reference in resp_log_file.text
    assert "AUDIT RUN FINISHED" in resp_log_file.text

    # 14. Replay re-executes the same pinned plan version
    resp_replay = client.post(f"/api/v1/audits/{run_id}/replay")
    assert resp_replay.status_code == 200, resp_replay.text
    replay = resp_replay.json()
    assert replay["status"] == "COMPLETED"
    assert replay["total_transactions"] == run_data["total_transactions"]
    assert replay["total_exceptions"] == run_data["total_exceptions"]
    assert replay["run_reference"] != run_reference


def test_unavailable_data_source_blocks_activation(client):
    """A KRI assigned a source with no queryable entity cannot be activated (R8)."""
    pa_id = client.get("/api/v1/kris/meta/process-areas").json()[0]["id"]
    ds = {d["code"]: d for d in client.get("/api/v1/kris/meta/data-sources").json()}

    resp = client.post(
        "/api/v1/kris",
        json={
            "identifier": "KRI-INT-SAPIENS",
            "name": "Sapiens Policy KRI",
            "process_area_id": pa_id,
            "indicator_type": "LEADING",
            "risk_description": "Policy administration coverage risk for integration testing.",
            "end_goal": "Every issued policy is traceable to an approved quotation.",
            "data_source_ids": [ds["SAPIENS"]["id"]],
        },
    )
    assert resp.status_code == 201
    kri_id = resp.json()["id"]

    # The step is fine on its own; the failure is that nothing can be read.
    resp_step = client.post(
        f"/api/v1/kris/{kri_id}/steps",
        json={"step_number": 1, "title": "Extract", "instruction": "Extract the policy population for the period."},
    )
    assert resp_step.status_code == 422
    assert "expose a queryable entity" in resp_step.json()["detail"]["message"]

    resp_act = client.patch(f"/api/v1/kris/{kri_id}/status", json={"status": "ACTIVE"})
    assert resp_act.status_code == 422
    assert "Cannot activate" in resp_act.json()["detail"]["message"]


def test_step_edit_regenerates_the_plan(client, db_session):
    """Editing a step text produces a new plan version, and the run uses the new one."""
    from app.repositories.kri_repository import KRIRepository

    pa_id = client.get("/api/v1/kris/meta/process-areas").json()[0]["id"]
    ds = {d["code"]: d for d in client.get("/api/v1/kris/meta/data-sources").json()}

    kri_id = client.post(
        "/api/v1/kris",
        json={
            "identifier": "KRI-INT-EDIT",
            "name": "Edit invalidation KRI",
            "process_area_id": pa_id,
            "indicator_type": "LAGGING",
            "risk_description": "Testing that a step edit invalidates the stored plan.",
            "end_goal": "Confirm the plan is regenerated whenever a step changes.",
            "data_source_ids": [ds["SAP_ECC"]["id"], ds["RED_BOX_PO"]["id"]],
        },
    ).json()["id"]

    # A plan that matches two populations needs a variance limit.
    client.post(
        f"/api/v1/kris/{kri_id}/thresholds",
        json={
            "name": "10% Variance Threshold",
            "threshold_type": "PERCENTAGE_DIFFERENCE",
            "operator": ">",
            "threshold_value": 10.0,
            "is_active": True,
        },
    )

    client.post(f"/api/v1/kris/{kri_id}/steps", json={"step_number": 1, "title": "Extract Orders", "instruction": "Extract the population of Order Intake records from SAP ECC."})
    client.post(f"/api/v1/kris/{kri_id}/steps", json={"step_number": 2, "title": "Extract POs", "instruction": "Extract the population of Purchase Orders from Red Box PO."})
    client.post(f"/api/v1/kris/{kri_id}/steps", json={"step_number": 3, "title": "Match", "instruction": "Match the populations using order_id."})

    v1 = client.get(f"/api/v1/kris/{kri_id}/plans").json()[0]
    versions_before = len(client.get(f"/api/v1/kris/{kri_id}/plans").json())

    resp_edit = client.put(
        f"/api/v1/kris/{kri_id}/steps/2",
        json={"instruction": "Extract the population of Order Intake records from SAP ECC using order_id."},
    )
    assert resp_edit.status_code == 200

    plans = client.get(f"/api/v1/kris/{kri_id}/plans").json()
    assert len(plans) == versions_before + 1
    assert plans[0]["version"] > v1["version"]
    assert plans[0]["is_current"] is True

    # Rewriting a step so it can no longer be interpreted is rejected, and the previously
    # committed plan version is left untouched rather than being replaced by a broken one.
    resp_bad = client.put(
        f"/api/v1/kris/{kri_id}/steps/3",
        json={"title": "Vague", "instruction": "Consider the situation and decide appropriately."},
    )
    assert resp_bad.status_code == 422
    assert client.get(f"/api/v1/kris/{kri_id}/plans").json()[0]["version"] == plans[0]["version"]
    # The rejected edit was rolled back, so the step text is unchanged.
    steps_now = client.get(f"/api/v1/kris/{kri_id}").json()["test_steps"]
    assert all("decide appropriately" not in s["instruction"] for s in steps_now)
    assert client.get(f"/api/v1/kris/{kri_id}").json()["test_steps"][2]["title"] == "Match"


def test_ui_bridge_uses_the_supplied_window(client):
    """POST /api/runs must honour the requested window, not a hardcoded one."""
    pa_id = client.get("/api/v1/kris/meta/process-areas").json()[0]["id"]
    ds = {d["code"]: d for d in client.get("/api/v1/kris/meta/data-sources").json()}

    resp = client.post(
        "/api/kris",
        json={
            "id": "KRI-UI-WINDOW",
            "name": "UI window KRI",
            "area": "O2C",
            "kind": "Lagging",
            "status": "active",
            "risk": "Verify the UI run window is honoured end to end.",
            "objective": "Every run covers exactly the requested period.",
            "sources": ["SAP_ECC", "RED_BOX_PO"],
            "steps": [
                {"instruction": "Extract the population of Order Intake records from SAP ECC."},
                {"instruction": "Extract the population of Purchase Orders from Red Box PO."},
                {"instruction": "Match the populations using order_id."},
            ],
            "thresholds": [{"key": "variance", "label": "Variance limit", "value": 10, "unit": "%"}],
        },
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert saved["currentPlan"]["isCurrent"] is True
    assert saved["thresholds"][0]["value"] == 10

    # January only: 2026-01-01..2026-01-31, so a much smaller population than Q1.
    resp_run = client.post("/api/runs", json={"kriId": "KRI-UI-WINDOW", "startDate": "2026-01-01", "endDate": "2026-01-31"})
    assert resp_run.status_code == 200, resp_run.text
    january = resp_run.json()["run"]
    assert january["period"] == "2026-01-01 to 2026-01-31"
    assert january["tested"] < 80

    # A period code resolves to a window too.
    resp_q1 = client.post("/api/runs", json={"kriId": "KRI-UI-WINDOW", "period": "2026-Q1"})
    assert resp_q1.status_code == 200, resp_q1.text
    assert resp_q1.json()["run"]["period"] == "2026-01-01 to 2026-03-31"
    assert resp_q1.json()["run"]["tested"] == 80

    # No window at all is an explicit error rather than a silent default.
    resp_bad = client.post("/api/runs", json={"kriId": "KRI-UI-WINDOW"})
    assert resp_bad.status_code == 400
    assert "audit window" in resp_bad.json()["detail"]


def test_ui_bridge_rejects_an_unknown_data_source(client):
    """A source with no catalog entry and no data behind it is rejected, never invented."""
    pa_id = client.get("/api/v1/kris/meta/process-areas").json()[0]["id"]
    resp = client.post(
        "/api/kris",
        json={
            "id": "KRI-UI-BADSRC",
            "name": "Bad source KRI",
            "area": "O2C",
            "kind": "Lagging",
            "status": "draft",
            "risk": "Ensure unknown data sources are rejected.",
            "objective": "Never invent a data source that has no data behind it.",
            "sources": ["TOTALLY_MADE_UP"],
            "steps": [{"instruction": "Extract the population."}],
        },
    )
    assert resp.status_code == 400
    assert "No queryable data source backs" in resp.json()["detail"]


def test_ui_bridge_maps_legacy_source_ids_onto_catalog_codes(client):
    """The UI picker sends legacy display ids; they must resolve to the real catalog codes."""
    pa_id = client.get("/api/v1/kris/meta/process-areas").json()[0]["id"]
    resp = client.post(
        "/api/kris",
        json={
            "id": "KRI-UI-LEGACY",
            "name": "Legacy id KRI",
            "area": "O2C",
            "kind": "Lagging",
            "status": "active",
            "risk": "The UI sends legacy identifiers that must map to real catalog codes.",
            "objective": "Both legacy ids resolve to SAP_ECC and RED_BOX_PO.",
            "sources": ["sap", "red_box_po"],
            "steps": [
                {"instruction": "Extract the population of Order Intake records from SAP ECC."},
                {"instruction": "Extract the population of Purchase Orders from Red Box PO."},
                {"instruction": "Match the populations using order_id."},
                {"instruction": "Flag every variance exceeding 10%."},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert set(saved["sources"]) == {"sap_ecc", "red_box_po"}
    assert saved["currentPlan"]["isCurrent"] is True
    used = {u["dataSourceCode"] for u in saved["currentPlan"]["dataSourcesUsed"]}
    assert used == {"SAP_ECC", "RED_BOX_PO"}


def test_ui_bridge_rejects_a_legacy_source_with_no_data(client):
    """'obs' and friends are UI placeholders with no queryable data behind them."""
    resp = client.post(
        "/api/kris",
        json={
            "id": "KRI-UI-OBS",
            "name": "Unbacked source KRI",
            "area": "O2C",
            "kind": "Lagging",
            "status": "draft",
            "risk": "Placeholder UI sources have no data behind them.",
            "objective": "Reject a source that cannot be read.",
            "sources": ["obs"],
            "steps": [{"instruction": "Extract the population."}],
        },
    )
    assert resp.status_code == 400
    assert "obs" in resp.json()["detail"]
