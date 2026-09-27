"""Unit Tests for Plan Generation, Hashing, Versioning and Validation."""

import pytest

from app.repositories.kri_repository import KRIRepository
from app.schemas.kri import KRITestStepCreate, KRITestStepUpdate, PlannedStepItem, StructuredPlan
from app.services.plan_coordinator import PlanCoordinator
from app.services.plan_service import PlanService


def test_plan_generation_from_steps(db_session, sample_active_kri):
    """Test translating natural language test steps into a structured plan."""
    plan = PlanCoordinator(db_session).interpret(sample_active_kri.id)

    assert plan.kri_id == sample_active_kri.id
    assert len(plan.steps) >= 5

    operations = [s.operation for s in plan.steps]
    tool_names = [s.tool_name for s in plan.steps]

    assert "EXTRACT_POPULATION" in operations
    assert "MATCH_RECORDS" in operations
    assert "CALCULATE_METRICS" in operations
    assert "BUILD_EVIDENCE" in operations
    assert "fetch_financial_data" in tool_names
    assert "compare_records" in tool_names
    assert "calculate_kri_metrics" in tool_names


def test_plan_validation(db_session, sample_active_kri):
    """A freshly interpreted plan validates cleanly."""
    kri = KRIRepository(db_session).get_kri_by_id(sample_active_kri.id)
    plan = PlanCoordinator(db_session).interpret(kri.id)
    result = PlanService.validate_plan(kri, plan, steps_hash=PlanService.compute_steps_hash(kri))

    assert result.is_valid
    assert result.errors == []


def test_plan_validation_missing_extraction(db_session, sample_active_kri):
    """Validation fails when the mandatory extraction stage is removed."""
    kri = KRIRepository(db_session).get_kri_by_id(sample_active_kri.id)
    plan = StructuredPlan(
        kri_id=kri.id,
        steps=[
            PlannedStepItem(
                step_number=1,
                operation="MATCH_RECORDS",
                tool_name="compare_records",
                parameters={
                    "left_alias": "pop_order_intake",
                    "right_alias": "pop_purchase_order",
                    "comparison_alias": "comparison",
                    "match_configuration": {"left_field": "order_id", "right_field": "order_id"},
                },
            )
        ],
    )
    result = PlanService.validate_plan(kri, plan)
    assert not result.is_valid
    assert any("EXTRACT_POPULATION" in e for e in result.errors)


def test_steps_hash_changes_when_a_step_is_edited(db_session, sample_active_kri):
    """Editing a step invalidates the plan, which is then regenerated (R3)."""
    repo = KRIRepository(db_session)
    coordinator = PlanCoordinator(db_session)

    before = PlanService.compute_steps_hash(repo.get_kri_by_id(sample_active_kri.id))
    assert coordinator.ensure_current_plan(sample_active_kri.id).steps_hash == before

    step = repo.get_kri_by_id(sample_active_kri.id).test_steps[0]
    repo.update_test_step(
        step.id,
        KRITestStepUpdate(instruction="Extract the population of Order Intake records from SAP ECC, bookings only."),
    )

    after = PlanService.compute_steps_hash(repo.get_kri_by_id(sample_active_kri.id))
    assert after != before

    plan_record = coordinator.ensure_current_plan(sample_active_kri.id)
    assert plan_record.steps_hash == after
    assert plan_record.version == 2


def test_steps_hash_changes_when_a_data_source_is_unassigned(db_session, sample_active_kri):
    """The data source assignment is part of the hash, so it invalidates the plan too.

    Unassigning Red Box PO leaves a step that references it, so regeneration fails loudly
    rather than producing a plan that reads the wrong source.
    """
    from app.services.plan_coordinator import PlanStaleError

    repo = KRIRepository(db_session)
    coordinator = PlanCoordinator(db_session)
    before = PlanService.compute_steps_hash(repo.get_kri_by_id(sample_active_kri.id))

    remaining = [ds.id for ds in repo.get_kri_by_id(sample_active_kri.id).data_sources if ds.code == "SAP_ECC"]
    repo.set_data_sources(sample_active_kri.id, remaining)

    assert PlanService.compute_steps_hash(repo.get_kri_by_id(sample_active_kri.id)) != before
    with pytest.raises(PlanStaleError) as exc:
        coordinator.ensure_current_plan(sample_active_kri.id)
    per_step = " ".join(e["error"] for e in exc.value.step_errors)
    assert "not assigned" in per_step


def test_threshold_change_invalidates_the_plan(db_session, sample_active_kri):
    """A threshold participates in the hash, so changing it re-interprets the plan."""
    repo = KRIRepository(db_session)
    coordinator = PlanCoordinator(db_session)
    assert coordinator.ensure_current_plan(sample_active_kri.id).version == 1

    threshold = repo.get_kri_by_id(sample_active_kri.id).thresholds[0]
    threshold.threshold_value = 25.0
    db_session.commit()

    plan_record = coordinator.ensure_current_plan(sample_active_kri.id)
    assert plan_record.version == 2
    threshold_step = next(
        s for s in plan_record.plan_payload["steps"] if s["operation"] == "APPLY_THRESHOLD"
    )
    assert threshold_step["parameters"]["threshold_value"] == 25.0


def test_ensure_current_plan_is_idempotent(db_session, sample_active_kri):
    """Re-deriving an unchanged configuration must not create version churn."""
    coordinator = PlanCoordinator(db_session)
    first = coordinator.ensure_current_plan(sample_active_kri.id)
    second = coordinator.ensure_current_plan(sample_active_kri.id)
    assert first.id == second.id
    assert first.version == second.version
    assert len(coordinator.list_plans(sample_active_kri.id)) == 1


def test_regenerate_forces_a_new_version(db_session, sample_active_kri):
    coordinator = PlanCoordinator(db_session)
    first = coordinator.ensure_current_plan(sample_active_kri.id)
    forced = coordinator.regenerate(sample_active_kri.id)
    assert forced.version == first.version + 1
    assert forced.plan_hash == first.plan_hash


def test_plan_diff_between_versions(db_session, sample_active_kri):
    repo = KRIRepository(db_session)
    coordinator = PlanCoordinator(db_session)
    v1 = coordinator.ensure_current_plan(sample_active_kri.id)

    # Change a threshold: a configuration change the interpreter can still satisfy.
    threshold = repo.get_kri_by_id(sample_active_kri.id).thresholds[0]
    threshold.threshold_value = 30.0
    db_session.commit()

    v2 = coordinator.ensure_current_plan(sample_active_kri.id)
    assert v2.version > v1.version

    diff = coordinator.diff(v1.id, v2.id)
    assert diff["from_version"] == v1.version
    assert diff["to_version"] == v2.version
    changed = [e for e in diff["entries"] if e["change"] == "CHANGED"]
    assert changed
    threshold_entries = [
        e for e in changed if e["after"] and e["after"]["operation"] == "APPLY_THRESHOLD"
    ]
    assert threshold_entries
    assert threshold_entries[0]["before"]["parameters"]["threshold_value"] == 10.0
    assert threshold_entries[0]["after"]["parameters"]["threshold_value"] == 30.0


def test_activation_requires_a_runnable_plan(db_session, sample_active_kri):
    """A KRI whose steps cannot be interpreted cannot be activated (R4 + R8)."""
    from app.services.plan_coordinator import PlanStaleError

    KRIRepository(db_session).replace_test_steps(
        sample_active_kri.id,
        [
            KRITestStepCreate(
                step_number=1,
                title="Vague",
                instruction="Consider the situation and decide appropriately.",
            )
        ],
    )
    with pytest.raises(PlanStaleError):
        PlanCoordinator(db_session).ensure_current_plan(sample_active_kri.id)
