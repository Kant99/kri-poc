"""Unit Tests for Plan Generation and Validation Service."""

import pytest
from app.services.plan_service import PlanService
from app.models.kri import KRI, KRITestStep, KRIThreshold


def test_plan_generation_from_steps(sample_active_kri):
    """Test translating natural language test steps into a structured plan."""
    plan = PlanService.generate_plan_from_steps(sample_active_kri)
    assert plan.kri_id == sample_active_kri.id
    assert len(plan.steps) >= 5

    tool_names = [s.tool_name for s in plan.steps]
    assert "fetch_financial_data" in tool_names
    assert "compare_records" in tool_names
    assert "calculate_kri_metrics" in tool_names


def test_plan_validation(sample_active_kri):
    """Test plan validation checks on valid plan."""
    plan = PlanService.generate_plan_from_steps(sample_active_kri)
    val = PlanService.validate_plan(sample_active_kri, plan)
    assert val.is_valid
    assert len(val.errors) == 0


def test_plan_validation_missing_extraction(sample_active_kri):
    """Test plan validation failure when extraction stage is omitted."""
    plan = PlanService.generate_plan_from_steps(sample_active_kri)
    # Remove extraction steps
    plan.steps = [s for s in plan.steps if s.operation != "EXTRACT_POPULATION" and s.tool_name != "fetch_financial_data"]
    val = PlanService.validate_plan(sample_active_kri, plan)
    assert not val.is_valid
    assert any("EXTRACT_POPULATION" in e for e in val.errors)
