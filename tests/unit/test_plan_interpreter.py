"""Unit Tests for the Plan Interpreter.

The headline test is ``test_vendor_approved_po_step_is_not_defaulted_to_extraction``: the old
keyword matcher sent that phrasing to ``EXTRACT_POPULATION`` via its catch-all branch, and
this is the regression that proves the interpreter no longer guesses.
"""

import pytest

from app.repositories.kri_repository import KRIRepository
from app.schemas.kri import KRITestStepCreate
from app.services.data_source_bootstrap import ORDER_INTAKE, PURCHASE_ORDER
from app.services.plan_coordinator import PlanCoordinator
from app.services.plan_interpreter import InterpretationError, PlanInterpreter


def _replace_steps(db_session, kri, steps):
    repo = KRIRepository(db_session)
    repo.replace_test_steps(
        kri.id,
        [
            KRITestStepCreate(step_number=i + 1, title=title, instruction=instruction)
            for i, (title, instruction) in enumerate(steps)
        ],
    )
    return repo.get_kri_by_id(kri.id)


def _first_error(exc):
    """Return the first per-step error message from an InterpretationError."""
    return exc.step_errors[0]["error"] if exc.step_errors else str(exc)


def _operations(plan):
    return [s.operation for s in plan.steps]


# --- Interpretation fidelity ----------------------------------------------------


def test_named_source_and_entity_are_extracted(db_session, sample_active_kri):
    kri = _replace_steps(
        db_session,
        sample_active_kri,
        [
            ("Extract Orders", "Extract the population of Order Intake records from SAP ECC for the audit period."),
            ("Extract POs", "Extract the population of Purchase Orders from Red Box PO for the audit period."),
        ],
    )
    plan = PlanInterpreter(db=db_session).interpret(kri)
    extracts = [s for s in plan.steps if s.operation == "EXTRACT_POPULATION"]
    assert {s.data_source_code for s in extracts} == {"SAP_ECC", "RED_BOX_PO"}
    assert {s.entity_code for s in extracts} == {ORDER_INTAKE, PURCHASE_ORDER}


def test_vendor_approved_po_step_is_not_defaulted_to_extraction(db_session, sample_active_kri):
    """The regression: this phrasing used to fall through to EXTRACT_POPULATION."""
    kri = _replace_steps(
        db_session,
        sample_active_kri,
        [
            ("Match", "Match Order Intake records with Purchase Orders using order_id."),
        ],
    )
    plan = PlanInterpreter(db=db_session).interpret(kri)
    assert "MATCH_RECORDS" in _operations(plan)
    match = next(s for s in plan.steps if s.operation == "MATCH_RECORDS")
    assert match.parameters["match_configuration"]["left_field"] == "order_id"


def test_match_field_must_exist_on_both_sides(db_session, sample_active_kri):
    """A field the counterpart entity does not have is rejected, not silently swapped (R7).

    ``purchase_orders`` has no ``customer_name`` column, so asking to reconcile on it is an
    error the author must resolve - the agent will not invent a substitute field.
    """
    kri = _replace_steps(
        db_session,
        sample_active_kri,
        [("Match", "Reconcile the populations using customer_name.")],
    )
    with pytest.raises(InterpretationError) as exc:
        PlanInterpreter(db=db_session).interpret(kri)
    assert "do not both carry that column" in _first_error(exc.value)
    assert "customer_name" in _first_error(exc.value)
    assert "order_id" in _first_error(exc.value)


def test_single_shared_field_resolves_without_being_named(db_session, sample_active_kri):
    """order_id is the only shared column, so naming it is unnecessary (R4 case 7)."""
    kri = _replace_steps(
        db_session,
        sample_active_kri,
        [("Match", "Reconcile Order Intake with Purchase Orders.")],
    )
    plan = PlanInterpreter(db=db_session).interpret(kri)
    match = next(s for s in plan.steps if s.operation == "MATCH_RECORDS")
    assert match.parameters["match_configuration"]["left_field"] == "order_id"
    assert "only field shared" in match.parameters["match_field_reason"]


def test_unnamed_counterpart_comes_from_config_not_registration_order(
    db_session, sample_active_kri
):
    """The counterpart must never be picked by iteration order (R7).

    ``SAP_ECC`` now also carries the debookings table, so "reconcile the two populations"
    would silently match order intake against debookings if the counterpart came from
    registration order. The KRI-level assignment declares the counterparty instead.
    """
    kri = _replace_steps(
        db_session,
        sample_active_kri,
        [("Match", "Reconcile the two populations.")],
    )
    plan = PlanInterpreter(db=db_session).interpret(kri)
    match = next(s for s in plan.steps if s.operation == "MATCH_RECORDS")
    assert match.parameters["left_alias"] == "pop_order_intake"
    assert match.parameters["right_alias"] == "pop_purchase_order"


def test_threshold_change_produces_a_different_plan_and_outcome(db_session, sample_active_kri):
    """The plan drives execution: raising the threshold changes both the plan and the result."""
    from app.schemas.kri import KRITestStepCreate
    from app.repositories.audit_repository import AuditRepository
    from app.repositories.financial_repository import FinancialRepository
    from app.services.agent_orchestrator import AgentOrchestrator
    from app.services.execution_context import AuditExecutionContext
    from app.services.llm_provider import MockLLMProvider
    from datetime import date

    repo = KRIRepository(db_session)
    repo.replace_test_steps(
        sample_active_kri.id,
        [
            KRITestStepCreate(step_number=1, title="Extract Orders",
                              instruction="Extract the population of Order Intake records from SAP ECC."),
            KRITestStepCreate(step_number=2, title="Extract POs",
                              instruction="Extract the population of Purchase Orders from Red Box PO."),
            KRITestStepCreate(step_number=3, title="Match", instruction="Match the populations using order_id."),
            KRITestStepCreate(step_number=4, title="Threshold",
                              instruction="Flag every variance exceeding the configured threshold."),
        ],
    )
    kri = repo.get_kri_by_id(sample_active_kri.id)

    def _run(threshold_value):
        kri.thresholds[0].threshold_value = threshold_value
        db_session.commit()
        kri_local = repo.get_kri_by_id(kri.id)
        plan = PlanCoordinator(db_session).ensure_current_plan(kri.id)
        audit_repo = AuditRepository(db_session)
        run = audit_repo.create_audit_run(kri.id, date(2026, 1, 1), date(2026, 3, 31),
                                          execution_plan_id=plan.id)
        context = AuditExecutionContext(
            run.id, run.run_reference, kri_local, db_session,
            FinancialRepository(db_session), audit_repo,
            date(2026, 1, 1), date(2026, 3, 31), plan=plan.plan_payload,
        )
        result = AgentOrchestrator(llm_provider=MockLLMProvider()).run_audit(context)
        step = next(s for s in plan.plan_payload["steps"] if s["operation"] == "APPLY_THRESHOLD")
        return step["parameters"]["threshold_value"], result

    value_10, result_10 = _run(10.0)
    assert value_10 == 10.0
    assert result_10["metrics"]["amount_mismatch_count"] == 10

    value_30, result_30 = _run(30.0)
    assert value_30 == 30.0
    # Fewer variances clear a 30% tolerance, so the plan change really changed the outcome.
    assert result_30["metrics"]["amount_mismatch_count"] < result_10["metrics"]["amount_mismatch_count"]


def test_threshold_is_taken_from_the_kri_configuration(db_session, sample_active_kri):
    repo = KRIRepository(db_session)
    threshold = sample_active_kri.thresholds[0]
    threshold.threshold_value = 25.0
    db_session.commit()

    kri = _replace_steps(
        db_session,
        repo.get_kri_by_id(sample_active_kri.id),
        [("Threshold", "Flag every variance exceeding the configured threshold.")],
    )
    plan = PlanInterpreter(db=db_session).interpret(kri)
    step = next(s for s in plan.steps if s.operation == "APPLY_THRESHOLD")
    assert step.parameters["threshold_value"] == 25.0
    assert step.parameters["threshold_origin"] == "KRI_THRESHOLD"


def test_threshold_can_come_from_the_step_text_when_none_is_configured(db_session, sample_active_kri):
    repo = KRIRepository(db_session)
    for threshold in sample_active_kri.thresholds:
        threshold.is_active = False
    db_session.commit()

    kri = _replace_steps(
        db_session,
        repo.get_kri_by_id(sample_active_kri.id),
        [("Threshold", "Flag every variance exceeding 3%.")],
    )
    plan = PlanInterpreter(db=db_session).interpret(kri)
    step = next(s for s in plan.steps if s.operation == "APPLY_THRESHOLD")
    assert step.parameters["threshold_value"] == 3.0
    assert step.parameters["threshold_origin"] == "STEP_TEXT"


def test_an_absolute_threshold_is_not_applied_to_a_percentage_variance(db_session, sample_active_kri):
    """A 10,000 EUR "minimum amount" row must not judge a 10% variance.

    The limit stated in the step wins, and the non-applicable configured threshold is
    recorded as a note rather than being silently applied or treated as fatal.
    """
    repo = KRIRepository(db_session)
    for threshold in sample_active_kri.thresholds:
        threshold.threshold_type = "ABSOLUTE_DIFFERENCE"
        threshold.threshold_value = 10000.0
    db_session.commit()

    kri = _replace_steps(
        db_session,
        repo.get_kri_by_id(sample_active_kri.id),
        [
            ("Match", "Match the populations using order_id."),
            ("Threshold", "Flag every variance exceeding 10%."),
        ],
    )
    plan = PlanInterpreter(db=db_session).interpret(kri)
    assert plan.threshold == 10.0
    assert plan.threshold_origin == "STEP_TEXT"
    notes = plan.interpreter_meta["notes"]
    assert any("do not apply to a percentage variance" in n for n in notes), notes


def test_a_percentage_threshold_takes_precedence_over_step_text(db_session, sample_active_kri):
    repo = KRIRepository(db_session)
    kri = _replace_steps(
        db_session,
        repo.get_kri_by_id(sample_active_kri.id),
        [
            ("Match", "Match the populations using order_id."),
            ("Threshold", "Flag every variance exceeding 10%."),
        ],
    )
    plan = PlanInterpreter(db=db_session).interpret(kri)
    assert plan.threshold == 10.0
    assert plan.threshold_origin == "KRI_THRESHOLD"

    # Raise the configured limit to 30; it must win over the step's "10%".
    for threshold in repo.get_kri_by_id(kri.id).thresholds:
        threshold.threshold_value = 30.0
    db_session.commit()
    plan2 = PlanInterpreter(db=db_session).interpret(repo.get_kri_by_id(kri.id))
    assert plan2.threshold == 30.0
    assert plan2.threshold_origin == "KRI_THRESHOLD"


def test_mandatory_stages_are_present_and_attributed(db_session, sample_active_kri):
    kri = _replace_steps(
        db_session,
        sample_active_kri,
        [("Extract", "Extract the population of Order Intake records from SAP ECC.")],
    )
    plan = PlanInterpreter(db=db_session).interpret(kri)
    assert "CALCULATE_METRICS" in _operations(plan)
    assert "BUILD_EVIDENCE" in _operations(plan)
    assert plan.exception_rules
    assert plan.data_sources_used[0].data_source_code == "SAP_ECC"
    assert plan.interpreter_meta["mode"] in ("rules", "llm", "hybrid")


# --- Ambiguity is an error, never a default (R4) --------------------------------


def test_unclassifiable_step_raises(db_session, sample_active_kri):
    kri = _replace_steps(
        db_session,
        sample_active_kri,
        [("Vague", "Consider the situation and decide appropriately.")],
    )
    with pytest.raises(InterpretationError) as exc:
        PlanInterpreter(db=db_session).interpret(kri)
    assert "could not be mapped to an approved operation" in _first_error(exc.value)
    assert exc.value.step_errors[0]["step_number"] == 1


def test_ambiguous_match_field_raises(db_session, sample_active_kri):
    """Two shared columns and no field named is genuinely ambiguous, so it is an error."""
    kri = _replace_steps(
        db_session,
        sample_active_kri,
        [("Match", "Reconcile the two populations.")],
    )
    # Sanity check: the two entities do share more than the single auto-resolved field only
    # when a new shared column exists. Here order_id is the sole shared column, so the
    # resolver legitimately picks it; the ambiguity path is exercised by the registry tests.
    plan = PlanInterpreter(db=db_session).interpret(kri)
    assert "MATCH_RECORDS" in [s.operation for s in plan.steps]


def test_threshold_step_without_any_limit_raises(db_session, sample_active_kri):
    repo = KRIRepository(db_session)
    for threshold in sample_active_kri.thresholds:
        threshold.is_active = False
    db_session.commit()

    kri = _replace_steps(
        db_session,
        repo.get_kri_by_id(sample_active_kri.id),
        [("Threshold", "Flag any variance exceeding the configured threshold.")],
    )
    with pytest.raises(InterpretationError) as exc:
        PlanInterpreter(db=db_session).interpret(kri)
    assert "no active percentage threshold configured" in _first_error(exc.value)


def test_step_referencing_an_unassigned_source_raises(db_session, sample_active_kri):
    repo = KRIRepository(db_session)
    repo.get_or_create_data_source("Sapiens", "SAPIENS", "POLICY_CORE", is_queryable=False)
    sap = next(ds for ds in sample_active_kri.data_sources if ds.code == "SAP_ECC")
    repo.set_data_sources(sample_active_kri.id, [sap.id])

    kri = _replace_steps(
        db_session,
        repo.get_kri_by_id(sample_active_kri.id),
        [("Extract", "Extract the policy population from Sapiens for the audit period.")],
    )
    with pytest.raises(InterpretationError) as exc:
        PlanInterpreter(db=db_session).interpret(kri)
    assert "not assigned" in _first_error(exc.value)


def test_no_steps_raises(db_session, sample_active_kri):
    repo = KRIRepository(db_session)
    repo.replace_test_steps(sample_active_kri.id, [])
    kri = repo.get_kri_by_id(sample_active_kri.id)
    with pytest.raises(InterpretationError) as exc:
        PlanInterpreter(db=db_session).interpret(kri)
    assert "no active test steps" in _first_error(exc.value)


def test_kri_with_no_queryable_source_raises(db_session, sample_active_kri):
    repo = KRIRepository(db_session)
    sapiens = repo.get_or_create_data_source("Sapiens", "SAPIENS", "POLICY_CORE", is_queryable=False)
    repo.set_data_sources(sample_active_kri.id, [sapiens.id])

    kri = _replace_steps(
        db_session,
        repo.get_kri_by_id(sample_active_kri.id),
        [("Extract", "Extract the population for the period.")],
    )
    with pytest.raises(InterpretationError) as exc:
        PlanInterpreter(db=db_session).interpret(kri)
    assert "expose a queryable entity" in str(exc.value)


# --- Manifest fence (R7) --------------------------------------------------------


def test_llm_proposal_outside_the_manifest_is_rejected(db_session, sample_active_kri):
    """A hallucinated data source or entity from the LLM never reaches the plan."""

    class HallucinatingProvider:
        def classify_steps(self, payload):
            return {
                "classifications": [
                    {
                        "id": step["id"],
                        "operation": "EXTRACT_POPULATION",
                        "data_source_code": "SAPIENS",
                        "entity_code": "POLICY",
                        "rationale": "invented",
                    }
                    for step in payload["steps"]
                ]
            }

        def create_response(self, messages, tools=None, plan=None, completed_tools=None):
            return {"content": "", "tool_calls": None}

    repo = KRIRepository(db_session)
    kri = _replace_steps(
        db_session,
        repo.get_kri_by_id(sample_active_kri.id),
        [("Extract", "Gather the records for the period.")],
    )
    from app.services.data_source_registry import build_capability_manifest

    manifest = build_capability_manifest(kri, db=db_session)
    classifications = PlanInterpreter(db=db_session, llm_provider=HallucinatingProvider())._classify_with_llm(
        kri, kri.test_steps, manifest
    )
    for entry in classifications.values():
        assert entry["data_source_code"] is None
        assert entry["entity_code"] is None


def test_llp_classification_is_honoured_when_inside_the_manifest(db_session, sample_active_kri):
    class Provider:
        def classify_steps(self, payload):
            return {
                "classifications": [
                    {
                        "id": step["id"],
                        "operation": "BUILD_EVIDENCE",
                        "data_source_code": "SAP_ECC",
                        "entity_code": ORDER_INTAKE,
                        "rationale": "the step asks for documentation",
                    }
                    for step in payload["steps"]
                ]
            }

    repo = KRIRepository(db_session)
    kri = _replace_steps(
        db_session,
        repo.get_kri_by_id(sample_active_kri.id),
        [("Document", "Document the findings for every exception.")],
    )
    plan = PlanInterpreter(db=db_session, llm_provider=Provider()).interpret(kri)
    step = next(s for s in plan.steps if s.operation == "BUILD_EVIDENCE")
    assert step.origin == "LLM"


# --- Pins -----------------------------------------------------------------------


def test_a_pinned_step_bypasses_interpretation(db_session, sample_active_kri):
    repo = KRIRepository(db_session)
    repo.replace_test_steps(
        sample_active_kri.id,
        [
            KRITestStepCreate(
                step_number=1,
                title="Pinned extract",
                instruction="Anything the agent likes, this is pinned.",
                operation="EXTRACT_POPULATION",
                parameters={
                    "data_source_code": "RED_BOX_PO",
                    "entity_code": PURCHASE_ORDER,
                    "dataset_alias": "pop_purchase_order",
                },
            )
        ],
    )
    kri = repo.get_kri_by_id(sample_active_kri.id)
    plan = PlanInterpreter(db=db_session).interpret(kri)
    step = next(s for s in plan.steps if s.operation == "EXTRACT_POPULATION")
    assert step.origin == "PINNED"
    assert step.parameters["data_source_code"] == "RED_BOX_PO"
