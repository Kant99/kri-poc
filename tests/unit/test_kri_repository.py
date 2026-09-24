"""Unit Tests for KRI Repository Operations."""

import pytest
from app.repositories.kri_repository import KRIRepository
from app.schemas.kri import (
    KRICreate,
    KRIUpdate,
    KRITestStepCreate,
    StepReorderItem,
    KRIThresholdCreate,
    IndicatorTypeEnum,
    ThresholdTypeEnum,
    ComparisonOperatorEnum,
)


def test_kri_crud(db_session):
    """Test creating, reading, updating, and status transitioning for a KRI."""
    repo = KRIRepository(db_session)
    pa = repo.get_or_create_process_area("Procure to Pay", "P2P")

    # Create KRI
    kri = repo.create_kri(
        KRICreate(
            identifier="KRI-P2P-001",
            name="PO Approval vs Invoicing Variance",
            process_area_id=pa.id,
            indicator_type=IndicatorTypeEnum.LAGGING,
            risk_description="Risk of invoice payments exceeding approved PO amounts.",
            end_goal="Flag all invoices exceeding PO by > 5%.",
        )
    )
    assert kri.id is not None
    assert kri.identifier == "KRI-P2P-001"
    assert kri.status == "DRAFT"

    # Update KRI
    updated = repo.update_kri(kri.id, KRIUpdate(name="Updated PO Approval Variance"))
    assert updated.name == "Updated PO Approval Variance"

    # Status update
    activated = repo.update_status(kri.id, "ACTIVE")
    assert activated.status == "ACTIVE"
    assert activated.validated_at is not None


def test_test_step_reorder(db_session):
    """Test adding test steps and reordering them."""
    repo = KRIRepository(db_session)
    pa = repo.get_or_create_process_area("O2C", "O2C")
    kri = repo.create_kri(
        KRICreate(
            identifier="KRI-STEP-TEST",
            name="Step Test KRI",
            process_area_id=pa.id,
            risk_description="Step testing description.",
            end_goal="Test step reorder logic.",
        )
    )

    s1 = repo.add_test_step(kri.id, KRITestStepCreate(step_number=1, title="Step 1", instruction="Do step 1"))
    s2 = repo.add_test_step(kri.id, KRITestStepCreate(step_number=2, title="Step 2", instruction="Do step 2"))

    # Reorder steps
    reordered = repo.reorder_test_steps(
        kri.id,
        [
            StepReorderItem(step_id=s1.id, new_step_number=2),
            StepReorderItem(step_id=s2.id, new_step_number=1),
        ],
    )
    assert reordered[0].id == s2.id
    assert reordered[0].step_number == 1
    assert reordered[1].id == s1.id
    assert reordered[1].step_number == 2
