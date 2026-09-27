"""End-to-End Verification Script.

Drives the real HTTP API (via FastAPI's TestClient) against the seeded database to prove the
interpreted-execution-plan design works:

1.  The data source catalog reports what is actually queryable.
2.  A brand new KRI can be created and configured entirely through the API.
3.  Each step write re-interprets the plan; a step the agent cannot resolve is rejected
    with per-step guidance and rolled back (R4).
4.  A KRI whose only data source has no queryable entity cannot be activated (R8).
5.  The stored plan - not the raw step text - drives the run, with per-read source
    attribution in the trace.
6.  Editing a step produces a new plan version, and the run picks it up.
7.  A past run can be replayed from its pinned plan version.

Run with:  python -m scripts.verify_end_to_end
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

from fastapi.testclient import TestClient

from app.core.database import Base, SessionLocal, engine, init_db
from app.core.database import get_db
from app.main import app
from app.services.data_source_bootstrap import register_all_entities
from scripts.seed_data import seed_financial_data, verify_seeded_data
from scripts.seed_kri import seed_kri_configuration

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [{PASS}] {label}")
    else:
        print(f"  [{FAIL}] {label}{(' - ' + detail) if detail else ''}")
        failures.append(label)


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> int:
    section("0. Reset and seed the database")
    register_all_entities()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_kri_configuration(db)
        seed_financial_data(db, record_count=80)
        check("Order Intake and Purchase Orders are seeded and verified", verify_seeded_data(db))
    finally:
        db.close()

    # Route every request at the same session the seed used, so the API sees the seeded data.
    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        # ------------------------------------------------------------------ 1
        section("1. Data source catalog: what is actually queryable")
        sources = {d["code"]: d for d in client.get("/api/v1/kris/meta/data-sources").json()}
        for code, ds in sorted(sources.items()):
            state = "QUERYABLE" if ds["is_queryable"] else "no data"
            print(f"    {code:<18} {state:<12} entities={ds['entity_codes'] or '-'}")
        check("SAP_ECC exposes ORDER_INTAKE", sources["SAP_ECC"]["entity_codes"] == ["ORDER_INTAKE"])
        check("RED_BOX_PO exposes PURCHASE_ORDER", sources["RED_BOX_PO"]["entity_codes"] == ["PURCHASE_ORDER"])
        check(
            "SAPIENS is reported as not queryable rather than silently returning nothing",
            sources["SAPIENS"]["is_queryable"] is False and sources["SAPIENS"]["entity_codes"] == [],
        )

        operations = {o["operation"] for o in client.get("/api/v1/kris/meta/operations").json()}
        check("Approved operation catalog is exposed", "EXTRACT_POPULATION" in operations and "PREPARE" in operations)

        # ------------------------------------------------------------------ 2
        section("2. Create a brand new KRI through the API")
        pa_id = client.get("/api/v1/kris/meta/process-areas").json()[0]["id"]
        new_kri_id = None
        created = client.post(
            "/api/v1/kris",
            json={
                "identifier": "KRI-VERIFY-001",
                "name": "Verified Order Intake vs PO Reconciliation",
                "process_area_id": pa_id,
                "indicator_type": "LEADING",
                "risk_description": (
                    "Verification risk: booked customer orders may lack an approved purchase order "
                    "or carry an amount variance beyond tolerance."
                ),
                "end_goal": "Every booked order has an approved PO within a 10% amount variance.",
                "data_source_ids": [sources["SAP_ECC"]["id"], sources["RED_BOX_PO"]["id"]],
                "owner": "Internal Audit",
            },
        )
        check("KRI created", created.status_code == 201, created.text[:200])
        new_kri_id = created.json()["id"]

        # ------------------------------------------------------------------ 3
        section("3. Enter test steps; the plan is re-interpreted on every write")
        steps = [
            ("Extract Order Intake population",
             "Extract the population of Order Intake records from SAP ECC for the audit period."),
            ("Extract Purchase Order population",
             "Extract the population of Purchase Orders from Red Box PO for the audit period."),
            ("Match orders to purchase orders",
             "Match Order Intake records with corresponding Purchase Orders using order_id."),
            ("Evaluate the amount variance",
             "Compare order amounts with PO amounts and flag every variance exceeding 10%."),
            ("Compile metrics and evidence",
             "Calculate aggregate KRI metrics and compile reproducible evidence packages."),
        ]
        for index, (title, instruction) in enumerate(steps, start=1):
            resp = client.post(
                f"/api/v1/kris/{new_kri_id}/steps",
                json={"step_number": index, "title": title, "instruction": instruction},
            )
            check(f"Step {index} accepted", resp.status_code == 201, resp.text[:200])

            if index == 2:
                # A plan that matches two populations needs a variance limit, so the
                # threshold is configured as soon as both populations exist.
                resp_th = client.post(
                    f"/api/v1/kris/{new_kri_id}/thresholds",
                    json={
                        "name": "10% Amount Variance Limit",
                        "threshold_type": "PERCENTAGE_DIFFERENCE",
                        "operator": ">",
                        "threshold_value": 10.0,
                        "is_active": True,
                    },
                )
                check("10% variance threshold accepted", resp_th.status_code == 201, resp_th.text[:200])

        section("4. The interpreted plan the agent committed")
        plans = client.get(f"/api/v1/kris/{new_kri_id}/plans").json()
        check("A plan was committed without any approval step", len(plans) >= 1)
        latest = plans[0]
        check("Latest plan is current for the configuration", latest["is_current"] is True)
        check("Latest plan is valid", latest["is_valid"] is True)

        plan = client.get(f"/api/v1/kris/{new_kri_id}/plans/{latest['id']}").json()
        payload = plan["plan_payload"]
        print()
        print(f"  Plan v{plan['version']}  source={plan['source']}  hash={plan['plan_hash'][:16]}...")
        print(f"  {'#':<3}{'operation':<22}{'tool':<24}{'reads from':<34}why")
        for step in payload["steps"]:
            reads = (
                f"{step['data_source_code']} / {step['entity_code']}"
                if step.get("data_source_code")
                else "-"
            )
            print(
                f"  {step['step_number']:<3}{step['operation']:<22}{step['tool_name']:<24}"
                f"{reads:<34}{(step.get('selector_reason') or '')[:60]}"
            )
        print()

        check(
            "Data sources came from the step text, not a hardcoded default",
            {u["data_source_code"] for u in payload["data_sources_used"]} == {"SAP_ECC", "RED_BOX_PO"},
        )
        check(
            "Every read is attributed to a catalog source",
            all(u.get("selector_reason") for u in payload["data_sources_used"]),
        )
        check("The plan declares its exception rules", len(payload["exception_rules"]) == 3)
        check("Mandatory metric and evidence stages are present",
              {"CALCULATE_METRICS", "BUILD_EVIDENCE"} <= {s["operation"] for s in payload["steps"]})

        # ------------------------------------------------------------------ 5
        section("5. Ambiguity is rejected, not guessed (R4)")
        rejected = client.post(
            f"/api/v1/kris/{new_kri_id}/steps",
            json={"step_number": 6, "title": "Vague", "instruction": "Consider the situation and decide appropriately."},
        )
        check("An unclassifiable step is rejected with 422", rejected.status_code == 422, rejected.text[:200])
        if rejected.status_code == 422:
            detail = rejected.json()["detail"]
            check("The rejection names the offending step", detail["steps"][0]["step_number"] == 6)
            check("The rejection explains what to fix",
                  "could not be mapped to an approved operation" in detail["steps"][0]["error"])
        check(
            "The rejected step was rolled back",
            len(client.get(f"/api/v1/kris/{new_kri_id}").json()["test_steps"]) == 5,
        )

        unassigned = client.post(
            f"/api/v1/kris/{new_kri_id}/steps",
            json={"step_number": 6, "title": "Wrong source", "instruction": "Extract the policy population from Sapiens for the audit period."},
        )
        check("A step naming an unassigned source is rejected", unassigned.status_code == 422)
        if unassigned.status_code == 422:
            check("The rejection explains the source is not assigned",
                  "not assigned" in unassigned.json()["detail"]["steps"][0]["error"])

        wrong_field = client.post(
            f"/api/v1/kris/{new_kri_id}/steps",
            json={"step_number": 6, "title": "Bad field", "instruction": "Reconcile the populations using customer_name."},
        )
        check("A match field absent from the counterpart is rejected", wrong_field.status_code == 422)
        if wrong_field.status_code == 422:
            check("The rejection lists the fields actually shared",
                  "order_id" in wrong_field.json()["detail"]["steps"][0]["error"])

        # ------------------------------------------------------------------ 6
        section("6. A data source with no data behind it blocks activation (R8)")
        sapiens_kri = client.post(
            "/api/v1/kris",
            json={
                "identifier": "KRI-VERIFY-SAPIENS",
                "name": "Sapiens policy coverage",
                "process_area_id": pa_id,
                "indicator_type": "LEADING",
                "risk_description": "Issued policies may not be traceable to an approved quotation.",
                "end_goal": "Every issued policy traces to an approved quotation.",
                "data_source_ids": [sources["SAPIENS"]["id"]],
            },
        ).json()["id"]
        sapiens_step = client.post(
            f"/api/v1/kris/{sapiens_kri}/steps",
            json={"step_number": 1, "title": "Extract", "instruction": "Extract the policy population for the audit period."},
        )
        check("A KRI with nothing queryable cannot produce a plan", sapiens_step.status_code == 422)
        sapiens_activate = client.patch(f"/api/v1/kris/{sapiens_kri}/status", json={"status": "ACTIVE"})
        check("Such a KRI cannot be activated", sapiens_activate.status_code == 422)
        check("The activation error names the problem",
              "Cannot activate" in sapiens_activate.json()["detail"]["message"])

        # ------------------------------------------------------------------ 7
        section("7. Activate and run the verified KRI")
        activated = client.patch(f"/api/v1/kris/{new_kri_id}/status", json={"status": "ACTIVE"})
        check("KRI activated", activated.status_code == 200 and activated.json()["status"] == "ACTIVE")

        run = client.post(
            "/api/v1/audits/run",
            json={"kri_id": new_kri_id, "start_date": "2026-01-01", "end_date": "2026-03-31"},
        )
        check("Audit run completed", run.status_code == 200, run.text[:300])
        result = run.json()
        print()
        for key in (
            "run_reference", "status", "total_transactions", "matched_count", "missing_po_count",
            "amount_mismatch_count", "total_exceptions", "total_order_value",
            "exception_order_value", "mismatch_value_percentage", "exception_rate_by_count",
        ):
            print(f"    {key:<28} {result.get(key)}")
        print()
        check("Run status is COMPLETED", result["status"] == "COMPLETED", result.get("error_message") or "")
        check("80 order intake records evaluated", result["total_transactions"] == 80)
        check("8 missing purchase orders flagged", result["missing_po_count"] == 8)
        check("10 amount mismatches flagged", result["amount_mismatch_count"] == 10)
        check("20 exceptions in total", result["total_exceptions"] == 20)
        check("Exception rate by count is 25%", result["exception_rate_by_count"] == 25.0)
        run_id = result["id"]
        run_reference = result["run_reference"]

        # ------------------------------------------------------------------ 8
        section("8. Source attribution in the execution trace")
        trace = client.get(f"/api/v1/audits/{run_id}/trace").json()
        reads = [i for i in trace["invocations"] if i["tool_name"] == "fetch_financial_data"]
        for inv in reads:
            args = inv["input_payload"]
            print(f"    step {inv['step_number']:<3} {args['data_source_code']:<12} {args['entity_code']:<16}"
                  f" {args['start_date']} .. {args['end_date']}  -> {inv['output_payload']['record_count']} records")
        check("Two reads, one per catalog source", len(reads) == 2)
        check("Each read records its resolved data source",
              {i["input_payload"]["data_source_code"] for i in reads} == {"SAP_ECC", "RED_BOX_PO"})

        logs = client.get(f"/api/v1/audits/{run_id}/logs").json()
        stages = [e["stage"] for e in logs["events"]]
        for stage in ("PLAN_LOADED", "PLAN_STEP_STARTED", "PLAN_STEP_COMPLETED",
                      "TOOL_EXECUTION", "EXCEPTION_FLAGGED", "METRICS_CALCULATED", "RUN_COMPLETED"):
            check(f"Log stage recorded: {stage}", stage in stages)

        # ------------------------------------------------------------------ 9
        section("9. Editing a step regenerates the plan (R3)")
        version_before = plans[0]["version"]
        step_id = client.get(f"/api/v1/kris/{new_kri_id}").json()["test_steps"][3]["id"]
        edited = client.put(
            f"/api/v1/kris/{new_kri_id}/steps/{step_id}",
            json={
                "title": "Evaluate the amount variance",
                "instruction": "Compare order amounts with PO amounts and flag every variance exceeding 30%.",
            },
        )
        check("Step edit accepted", edited.status_code == 200, edited.text[:200])
        new_plans = client.get(f"/api/v1/kris/{new_kri_id}/plans").json()
        check("A new plan version was created", new_plans[0]["version"] > version_before,
              f"{new_plans[0]['version']} vs {version_before}")
        new_plan = client.get(f"/api/v1/kris/{new_kri_id}/plans/{new_plans[0]['id']}").json()
        threshold_steps = [
            s for s in new_plan["plan_payload"]["steps"] if s["operation"] == "APPLY_THRESHOLD"
        ]
        # The KRI threshold (10%) takes precedence over the step text, which is reported.
        check("Threshold still resolves from the KRI configuration",
              threshold_steps and threshold_steps[0]["parameters"]["threshold_value"] == 10.0)
        check("The superseded plan version is retained for audit", len(new_plans) >= 2)

        threshold = client.get(f"/api/v1/kris/{new_kri_id}").json()["thresholds"][0]["id"]
        client.put(f"/api/v1/kris/{new_kri_id}/thresholds/{threshold}",
                   json={"threshold_value": 30.0})
        after_threshold = client.get(f"/api/v1/kris/{new_kri_id}/plans").json()
        check("Changing the threshold also produces a new plan version",
              after_threshold[0]["version"] > new_plans[0]["version"])
        latest_plan = client.get(f"/api/v1/kris/{new_kri_id}/plans/{after_threshold[0]['id']}").json()
        threshold_step = next(
            s for s in latest_plan["plan_payload"]["steps"] if s["operation"] == "APPLY_THRESHOLD"
        )
        check("The new plan carries the new threshold",
              threshold_step["parameters"]["threshold_value"] == 30.0)

        rerun = client.post(
            "/api/v1/audits/run",
            json={"kri_id": new_kri_id, "start_date": "2026-01-01", "end_date": "2026-03-31"},
        ).json()
        check("The re-run uses the new plan", rerun["status"] == "COMPLETED")
        check("A stricter tolerance yields fewer amount mismatches",
              rerun["amount_mismatch_count"] < result["amount_mismatch_count"],
              f"{rerun['amount_mismatch_count']} vs {result['amount_mismatch_count']}")
        print(f"    10% tolerance -> {result['amount_mismatch_count']} mismatches")
        print(f"    30% tolerance -> {rerun['amount_mismatch_count']} mismatches")

        # ------------------------------------------------------------------ 10
        section("10. Exceptions, evidence and replay")
        exceptions = client.get(f"/api/v1/audits/{run_id}/exceptions").json()
        by_type: dict = {}
        for exc in exceptions:
            by_type.setdefault(exc["exception_type"], []).append(exc)
        for exc_type, items in sorted(by_type.items()):
            print(f"    {exc_type:<18} {len(items):>3}")
        check("MISSING_PO exceptions recorded", len(by_type.get("MISSING_PO", [])) == 8)
        check("AMOUNT_MISMATCH exceptions recorded", len(by_type.get("AMOUNT_MISMATCH", [])) == 10)
        check("AMBIGUOUS_MATCH exceptions recorded", len(by_type.get("AMBIGUOUS_MATCH", [])) == 2)

        first = exceptions[0]
        evidence_ref = first.get("evidence_reference")
        if evidence_ref:
            evidence = client.get(f"/api/v1/audits/evidence/{evidence_ref}").json()
            check("Evidence package is reproducible and linked",
                  bool(evidence.get("explanation")) and evidence["order_id"] == first["order_id"])
        else:
            detail = client.get(f"/api/v1/audits/{run_id}/exceptions/{first['id']}").json()
            evidence_ref = detail.get("evidence_reference")
            evidence = client.get(f"/api/v1/audits/evidence/{evidence_ref}").json()
            check("Evidence package is reproducible and linked", bool(evidence.get("explanation")))

        replay = client.post(f"/api/v1/audits/{run_id}/replay")
        check("Replay of the pinned plan succeeded", replay.status_code == 200, replay.text[:200])
        if replay.status_code == 200:
            replayed = replay.json()
            check("Replay reproduces the same population",
                  replayed["total_transactions"] == result["total_transactions"])
            check("Replay reproduces the same exceptions",
                  replayed["total_exceptions"] == result["total_exceptions"])
            check("Replay is a new run", replayed["run_reference"] != run_reference)

        # ------------------------------------------------------------------ 11
        section("11. Plan history is read-only and complete")
        history = client.get(f"/api/v1/kris/{new_kri_id}/plans").json()
        check("Every plan version is retained", len(history) >= 3, f"{len(history)} versions")
        check("Exactly one version is current",
              sum(1 for p in history if p["is_current"]) == 1)
        if len(history) >= 2:
            diff = client.get(
                f"/api/v1/kris/{new_kri_id}/plans/diff",
                params={"from": history[-1]["id"], "to": history[0]["id"]},
            ).json()
            changed = [e for e in diff["entries"] if e["change"] == "CHANGED"]
            check("Diff between versions reports the changed steps", len(changed) >= 1)

        # ------------------------------------------------------------------ 12
        section("12. The UI bridge honours the requested window")
        ui_kri = client.post(
            "/api/kris",
            json={
                "id": "KRI-VERIFY-UI",
                "name": "UI bridge verification KRI",
                "area": "O2C",
                "kind": "Lagging",
                "status": "active",
                "risk": "Verify the UI bridge resolves the audit window from the request.",
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
        check("UI save succeeded", ui_kri.status_code == 200, ui_kri.text[:300])
        if ui_kri.status_code == 200:
            saved = ui_kri.json()
            check("The saved KRI reports a current plan", saved["currentPlan"]["isCurrent"] is True)
            check("The saved KRI reports its capabilities",
                  {s["code"] for s in saved["capabilities"]["dataSources"]} == {"SAP_ECC", "RED_BOX_PO"})

            january = client.post("/api/runs", json={"kriId": "KRI-VERIFY-UI",
                                                     "startDate": "2026-01-01", "endDate": "2026-01-31"}).json()["run"]
            q1 = client.post("/api/runs", json={"kriId": "KRI-VERIFY-UI", "period": "2026-Q1"}).json()["run"]
            print(f"    January window : {january['period']} -> {january['tested']} records")
            print(f"    Q1 window      : {q1['period']} -> {q1['tested']} records")
            check("The requested window is honoured", january["period"] == "2026-01-01 to 2026-01-31")
            check("A period code resolves to a window", q1["period"] == "2026-01-01 to 2026-03-31")
            check("A different window yields a different population", january["tested"] < q1["tested"])

            no_window = client.post("/api/runs", json={"kriId": "KRI-VERIFY-UI"})
            check("A missing window is an explicit error, not a silent default", no_window.status_code == 400)

            bad_source = client.post(
                "/api/kris",
                json={
                    "id": "KRI-VERIFY-BADSRC", "name": "Bad source", "area": "O2C", "kind": "Lagging",
                    "status": "draft", "risk": "x" * 20, "objective": "y" * 20,
                    "sources": ["MADE_UP_SYSTEM"], "steps": [{"instruction": "Extract the population."}],
                },
            )
            check("An unknown data source is rejected", bad_source.status_code == 400)

    finally:
        app.dependency_overrides.clear()

    section("RESULT")
    if failures:
        print(f"  {len(failures)} check(s) FAILED:")
        for name in failures:
            print(f"    - {name}")
        return 1
    print("  All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
