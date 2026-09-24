"""FastAPI Router for KRI Management, Steps, Thresholds, Schedules, and Validation."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.repositories.kri_repository import KRIRepository
from app.repositories.audit_repository import AuditRepository
from app.schemas.kri import (
    KRICreate,
    KRIUpdate,
    KRIStatusUpdate,
    KRIResponse,
    KRITestStepCreate,
    KRITestStepUpdate,
    KRITestStepResponse,
    StepReorderRequest,
    KRIThresholdCreate,
    KRIThresholdUpdate,
    KRIThresholdResponse,
    KRIScheduleCreate,
    KRIScheduleResponse,
    ReviewerCreate,
    ReviewerResponse,
    ProcessAreaResponse,
    DataSourceResponse,
    StructuredPlan,
    PlanValidationResult,
)
from app.schemas.common import StandardResponse
from app.services.plan_service import PlanService

router = APIRouter(prefix="/kris", tags=["KRI Configuration"])


# --- Catalog Endpoints ---
@router.get("/meta/process-areas", response_model=List[ProcessAreaResponse])
def list_process_areas(db: Session = Depends(get_db)):
    """Retrieve list of standard process areas (e.g. O2C, P2P, R2R)."""
    return KRIRepository(db).list_process_areas()


@router.get("/meta/data-sources", response_model=List[DataSourceResponse])
def list_data_sources(db: Session = Depends(get_db)):
    """Retrieve catalog of enterprise data sources."""
    return KRIRepository(db).list_data_sources()


# --- KRI Core CRUD ---
@router.post("", response_model=KRIResponse, status_code=status.HTTP_201_CREATED)
def create_kri(kri_in: KRICreate, db: Session = Depends(get_db)):
    """Create a new Key Risk Indicator (KRI) configuration."""
    repo = KRIRepository(db)
    if repo.get_kri_by_identifier(kri_in.identifier):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"KRI with identifier '{kri_in.identifier}' already exists.",
        )
    return repo.create_kri(kri_in)


@router.get("", response_model=List[KRIResponse])
def list_kris(
    status: Optional[str] = Query(None, description="Filter by status (e.g. ACTIVE, DRAFT)"),
    process_area_id: Optional[int] = Query(None, description="Filter by process area ID"),
    db: Session = Depends(get_db),
):
    """List all configured KRIs with filtering."""
    return KRIRepository(db).list_kris(status=status, process_area_id=process_area_id)


@router.get("/{kri_id}", response_model=KRIResponse)
def get_kri(kri_id: int, db: Session = Depends(get_db)):
    """Retrieve full KRI configuration including steps, thresholds, and schedules."""
    kri = KRIRepository(db).get_kri_by_id(kri_id)
    if not kri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")
    return kri


@router.put("/{kri_id}", response_model=KRIResponse)
def update_kri(kri_id: int, kri_in: KRIUpdate, db: Session = Depends(get_db)):
    """Update KRI core metadata."""
    kri = KRIRepository(db).update_kri(kri_id, kri_in)
    if not kri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")
    return kri


@router.patch("/{kri_id}/status", response_model=KRIResponse)
def update_kri_status(kri_id: int, status_in: KRIStatusUpdate, db: Session = Depends(get_db)):
    """Update KRI status (DRAFT, IN_ASSESSMENT, ACTIVE, INACTIVE, ARCHIVED)."""
    kri_repo = KRIRepository(db)
    kri = kri_repo.get_kri_by_id(kri_id)
    if not kri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")

    if status_in.status.value == "ACTIVE":
        # Validate plan before activation
        plan = PlanService.generate_plan_from_steps(kri)
        val = PlanService.validate_plan(kri, plan)
        if not val.is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot activate KRI. Validation errors: {val.errors}",
            )

    return kri_repo.update_status(kri_id, status_in.status.value)


@router.post("/{kri_id}/validate", response_model=PlanValidationResult)
def validate_kri(kri_id: int, db: Session = Depends(get_db)):
    """Validate KRI test steps, tools, dependencies, and configuration completeness."""
    kri = KRIRepository(db).get_kri_by_id(kri_id)
    if not kri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")
    plan = PlanService.generate_plan_from_steps(kri)
    return PlanService.validate_plan(kri, plan)


@router.post("/{kri_id}/generate-plan", response_model=StructuredPlan)
def generate_execution_plan(kri_id: int, db: Session = Depends(get_db)):
    """Translate natural language test steps into a structured, validated execution plan."""
    kri = KRIRepository(db).get_kri_by_id(kri_id)
    if not kri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")
    plan = PlanService.generate_plan_from_steps(kri)
    val = PlanService.validate_plan(kri, plan)
    AuditRepository(db).create_execution_plan(
        kri_id=kri.id,
        plan_payload=plan.model_dump(mode="json"),
        is_valid=val.is_valid,
        validation_errors=val.errors,
    )
    return plan


# --- Test Step Endpoints ---
@router.post("/{kri_id}/steps", response_model=KRITestStepResponse, status_code=status.HTTP_201_CREATED)
def add_test_step(kri_id: int, step_in: KRITestStepCreate, db: Session = Depends(get_db)):
    """Add a natural language test step to a KRI."""
    repo = KRIRepository(db)
    if not repo.get_kri_by_id(kri_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")
    return repo.add_test_step(kri_id, step_in)


@router.put("/{kri_id}/steps/{step_id}", response_model=KRITestStepResponse)
def update_test_step(kri_id: int, step_id: int, step_in: KRITestStepUpdate, db: Session = Depends(get_db)):
    """Update a natural language test step."""
    step = KRIRepository(db).update_test_step(step_id, step_in)
    if not step:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Test step {step_id} not found.")
    return step


@router.delete("/{kri_id}/steps/{step_id}", response_model=StandardResponse)
def delete_test_step(kri_id: int, step_id: int, db: Session = Depends(get_db)):
    """Delete a test step."""
    success = KRIRepository(db).delete_test_step(step_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Test step {step_id} not found.")
    return StandardResponse(message=f"Test step {step_id} deleted successfully.")


@router.patch("/{kri_id}/steps/reorder", response_model=List[KRITestStepResponse])
def reorder_test_steps(kri_id: int, req: StepReorderRequest, db: Session = Depends(get_db)):
    """Reorder test steps by providing updated step numbers."""
    return KRIRepository(db).reorder_test_steps(kri_id, req.reorder_list)


# --- Threshold Endpoints ---
@router.post("/{kri_id}/thresholds", response_model=KRIThresholdResponse, status_code=status.HTTP_201_CREATED)
def add_threshold(kri_id: int, th_in: KRIThresholdCreate, db: Session = Depends(get_db)):
    """Add a variance comparison threshold to a KRI."""
    repo = KRIRepository(db)
    if not repo.get_kri_by_id(kri_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")
    return repo.add_threshold(kri_id, th_in)


@router.put("/{kri_id}/thresholds/{threshold_id}", response_model=KRIThresholdResponse)
def update_threshold(kri_id: int, threshold_id: int, th_in: KRIThresholdUpdate, db: Session = Depends(get_db)):
    """Update a variance threshold."""
    th = KRIRepository(db).update_threshold(threshold_id, th_in)
    if not th:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Threshold {threshold_id} not found.")
    return th


@router.delete("/{kri_id}/thresholds/{threshold_id}", response_model=StandardResponse)
def delete_threshold(kri_id: int, threshold_id: int, db: Session = Depends(get_db)):
    """Delete a threshold configuration."""
    success = KRIRepository(db).delete_threshold(threshold_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Threshold {threshold_id} not found.")
    return StandardResponse(message=f"Threshold {threshold_id} deleted successfully.")


# --- Schedule and Reviewer Endpoints ---
@router.post("/{kri_id}/schedule", response_model=KRIScheduleResponse)
def configure_schedule(kri_id: int, sch_in: KRIScheduleCreate, db: Session = Depends(get_db)):
    """Configure or update audit execution schedule."""
    repo = KRIRepository(db)
    if not repo.get_kri_by_id(kri_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")
    return repo.create_or_update_schedule(kri_id, sch_in)


@router.post("/{kri_id}/reviewers", response_model=ReviewerResponse, status_code=status.HTTP_201_CREATED)
def add_reviewer(kri_id: int, rev_in: ReviewerCreate, db: Session = Depends(get_db)):
    """Assign a reviewer to a KRI."""
    repo = KRIRepository(db)
    if not repo.get_kri_by_id(kri_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")
    return repo.add_reviewer(kri_id, rev_in)
