"""FastAPI Router for KRI Management, Steps, Plans, Thresholds, Schedules, and Validation."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.kri_repository import KRIRepository
from app.schemas.kri import (
    KRICreate,
    KRIUpdate,
    KRIStatusUpdate,
    KRIResponse,
    KRITestStepCreate,
    KRITestStepUpdate,
    KRITestStepResponse,
    TestStepBulkUpdate,
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
    CapabilityManifest,
    OperationDescriptor,
    StructuredPlan,
    PlanValidationResult,
    PlanInterpretationErrorResponse,
    ExecutionPlanSummary,
    ExecutionPlanDetail,
    PlanDiffResponse,
)
from app.schemas.common import StandardResponse
from app.services.data_source_registry import build_capability_manifest, entity_registry
from app.services.plan_coordinator import PlanCoordinator, PlanStaleError
from app.services.plan_service import PlanService

router = APIRouter(prefix="/kris", tags=["KRI Configuration"])


def _require_kri(db: Session, kri_id: int):
    kri = KRIRepository(db).get_kri_by_id(kri_id)
    if not kri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")
    return kri


def _regenerate_or_422(db: Session, kri_id: int):
    """Regenerate the plan after a configuration change.

    A change that cannot be interpreted is rejected with 422 and per-step guidance, so the
    stored steps and the stored plan can never disagree (R3, R4).
    """
    try:
        return PlanCoordinator(db).ensure_current_plan(kri_id)
    except PlanStaleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=PlanInterpretationErrorResponse(
                message=str(exc), steps=exc.step_errors, errors=[str(exc)]
            ).model_dump(),
        )


def _step_write(db: Session, kri_id: int, mutate, serialize):
    """Apply a test step mutation and re-interpret the plan atomically.

    If interpretation fails, the step set is rolled back before the 422 is raised, so a
    rejected edit leaves no partial state behind (R3).
    """
    repo = KRIRepository(db)
    snapshot = repo.snapshot_steps(kri_id)
    result = mutate(repo)
    try:
        _regenerate_or_422(db, kri_id)
    except HTTPException:
        repo.restore_steps(kri_id, snapshot)
        raise
    return serialize(result)


def _serialize_plan(plan_record: Any, current_hash: Optional[str] = None) -> ExecutionPlanSummary:
    payload = plan_record.plan_payload or {}
    return ExecutionPlanSummary(
        id=plan_record.id,
        kri_id=plan_record.kri_id,
        version=plan_record.version,
        source=plan_record.source or "RULES",
        is_valid=plan_record.is_valid,
        plan_hash=plan_record.plan_hash,
        steps_hash=plan_record.steps_hash,
        is_current=bool(current_hash and plan_record.steps_hash == current_hash),
        step_count=len(payload.get("steps", [])),
        validation_errors=plan_record.validation_errors or [],
        created_at=plan_record.created_at,
    )


# --- Catalog Endpoints ---
@router.get("/meta/process-areas", response_model=List[ProcessAreaResponse])
def list_process_areas(db: Session = Depends(get_db)):
    """Retrieve list of standard process areas (e.g. O2C, P2P, R2R)."""
    return KRIRepository(db).list_process_areas()


@router.get("/meta/data-sources", response_model=List[DataSourceResponse])
def list_data_sources(db: Session = Depends(get_db)):
    """Retrieve catalog of enterprise data sources with their entity bindings."""
    KRIRepository(db).sync_data_source_availability()
    sources = KRIRepository(db).list_data_sources()
    return [
        DataSourceResponse(
            id=ds.id,
            name=ds.name,
            code=ds.code,
            system_type=ds.system_type,
            description=ds.description,
            is_queryable=ds.is_queryable,
            availability_note=ds.availability_note,
            entity_codes=entity_registry.bound_entity_codes(ds.code),
            created_at=ds.created_at,
        )
        for ds in sources
    ]


@router.get("/meta/operations", response_model=List[OperationDescriptor])
def list_operations():
    """Approved operations, their tools and required parameters. Drives UI dropdowns."""
    return PlanService.describe_operations()


@router.get("/meta/data-source-capabilities", response_model=CapabilityManifest)
def get_catalog_capabilities(db: Session = Depends(get_db)):
    """Capability manifest for the whole catalog: which source exposes which entity/fields."""
    kri_repo = KRIRepository(db)
    kri_repo.sync_data_source_availability()
    sources = kri_repo.list_data_sources()
    data_sources = [
        {
            "code": ds.code,
            "name": ds.name,
            "system_type": ds.system_type,
            "description": ds.description,
            "is_queryable": ds.is_queryable,
            "aliases": [],
            "entities": [
                entity_registry.get_entity(code).to_manifest() for code in entity_registry.bound_entity_codes(ds.code)
            ],
        }
        for ds in sources
    ]
    return CapabilityManifest(
        kri_identifier="*catalog*",
        data_sources=[d for d in data_sources if d["entities"]],
        unavailable_data_sources=[d for d in data_sources if not d["entities"]],
    )


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
    return _require_kri(db, kri_id)


@router.put("/{kri_id}", response_model=KRIResponse)
def update_kri(kri_id: int, kri_in: KRIUpdate, db: Session = Depends(get_db)):
    """Update KRI core metadata. Changing data sources regenerates the execution plan."""
    kri = KRIRepository(db).update_kri(kri_id, kri_in)
    if not kri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"KRI {kri_id} not found.")
    if kri_in.data_source_ids is not None:
        _regenerate_or_422(db, kri_id)
        kri = KRIRepository(db).get_kri_by_id(kri_id)
    return kri


@router.put("/{kri_id}/data-sources", response_model=KRIResponse)
def set_kri_data_sources(kri_id: int, data_source_ids: List[int], db: Session = Depends(get_db)):
    """Replace the KRI's assigned data sources.

    The assignment participates in the plan's staleness hash, so this regenerates the plan
    (R3). Assigning a source with no queryable entity is accepted here but blocks activation
    with an explicit error (R8).
    """
    _require_kri(db, kri_id)
    kri = KRIRepository(db).set_data_sources(kri_id, data_source_ids)
    try:
        _regenerate_or_422(db, kri_id)
    except HTTPException:
        raise
    return kri


@router.patch("/{kri_id}/status", response_model=KRIResponse)
def update_kri_status(kri_id: int, status_in: KRIStatusUpdate, db: Session = Depends(get_db)):
    """Update KRI status. Activation requires a valid plan for the current configuration."""
    kri_repo = KRIRepository(db)
    kri = _require_kri(db, kri_id)

    if status_in.status.value == "ACTIVE":
        try:
            plan_record = PlanCoordinator(db).ensure_current_plan(kri_id, strict_stages=True)
        except PlanStaleError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=PlanInterpretationErrorResponse(
                    message=f"Cannot activate KRI '{kri.identifier}': {exc}",
                    steps=exc.step_errors,
                    errors=[str(exc)],
                ).model_dump(),
            )
        if not plan_record.is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot activate KRI. Plan validation errors: {plan_record.validation_errors}",
            )

    return kri_repo.update_status(kri_id, status_in.status.value)


@router.post("/{kri_id}/validate", response_model=PlanValidationResult)
def validate_kri(kri_id: int, db: Session = Depends(get_db)):
    """Interpret the current steps and validate the resulting plan without persisting it."""
    kri = _require_kri(db, kri_id)
    coordinator = PlanCoordinator(db)
    try:
        plan = coordinator.interpret(kri_id)
    except PlanStaleError as exc:
        return PlanValidationResult(
            is_valid=False,
            kri_id=kri_id,
            errors=[str(exc)],
            steps_hash=coordinator.current_steps_hash(kri_id),
        )
    manifest = build_capability_manifest(kri, db=db)
    return PlanService.validate_plan(
        kri, plan, manifest=manifest, steps_hash=coordinator.current_steps_hash(kri_id)
    )


# --- Execution Plan Endpoints (read-only history; no approval step) ---
@router.post("/{kri_id}/generate-plan", response_model=StructuredPlan)
def generate_execution_plan(kri_id: int, db: Session = Depends(get_db)):
    """Interpret the test steps and persist the result as a new plan version."""
    _require_kri(db, kri_id)
    plan_record = _regenerate_or_422(db, kri_id)
    return StructuredPlan(**(plan_record.plan_payload or {}))


@router.post("/{kri_id}/plan/regenerate", response_model=StructuredPlan)
def regenerate_execution_plan(kri_id: int, db: Session = Depends(get_db)):
    """Force a new plan version even when the steps are unchanged."""
    _require_kri(db, kri_id)
    try:
        plan_record = PlanCoordinator(db).regenerate(kri_id)
    except PlanStaleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=PlanInterpretationErrorResponse(
                message=str(exc), steps=exc.step_errors, errors=[str(exc)]
            ).model_dump(),
        )
    return StructuredPlan(**(plan_record.plan_payload or {}))


@router.get("/{kri_id}/plans", response_model=List[ExecutionPlanSummary])
def list_execution_plans(kri_id: int, db: Session = Depends(get_db)):
    """Read-only plan version history (R5)."""
    _require_kri(db, kri_id)
    coordinator = PlanCoordinator(db)
    current_hash = coordinator.current_steps_hash(kri_id)
    return [_serialize_plan(plan, current_hash) for plan in coordinator.list_plans(kri_id)]


@router.get("/{kri_id}/plans/latest", response_model=Optional[ExecutionPlanSummary])
def get_latest_execution_plan(kri_id: int, db: Session = Depends(get_db)):
    """Latest plan version, flagged with whether it matches the current configuration."""
    _require_kri(db, kri_id)
    coordinator = PlanCoordinator(db)
    plans = coordinator.list_plans(kri_id)
    if not plans:
        return None
    return _serialize_plan(plans[0], coordinator.current_steps_hash(kri_id))


@router.get("/{kri_id}/plans/diff", response_model=PlanDiffResponse)
def diff_execution_plans(
    kri_id: int,
    from_plan_id: int = Query(..., alias="from"),
    to_plan_id: int = Query(..., alias="to"),
    db: Session = Depends(get_db),
):
    """Read-only structural diff between two plan versions, for audit purposes."""
    _require_kri(db, kri_id)
    try:
        return PlanCoordinator(db).diff(from_plan_id, to_plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/{kri_id}/plans/{plan_id}", response_model=ExecutionPlanDetail)
def get_execution_plan(kri_id: int, plan_id: int, db: Session = Depends(get_db)):
    """Retrieve one plan version including per-step rationale and source attribution."""
    _require_kri(db, kri_id)
    coordinator = PlanCoordinator(db)
    plan = coordinator.get_plan(plan_id)
    if plan is None or plan.kri_id != kri_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Plan {plan_id} not found.")
    summary = _serialize_plan(plan, coordinator.current_steps_hash(kri_id))
    return ExecutionPlanDetail(
        **summary.model_dump(),
        plan_payload=plan.plan_payload or {},
        interpreter_meta=plan.interpreter_meta,
        created_by=plan.created_by,
    )


@router.get("/{kri_id}/capabilities", response_model=CapabilityManifest)
def get_kri_capabilities(kri_id: int, db: Session = Depends(get_db), sample_rows: int = Query(0, ge=0, le=5)):
    """Capability manifest for this KRI: exactly what its plans are allowed to reference."""
    kri = _require_kri(db, kri_id)
    return CapabilityManifest(**build_capability_manifest(kri, db=db, sample_rows=sample_rows))


# --- Test Step Endpoints (every write regenerates the plan, and rolls back on failure) ---
@router.post("/{kri_id}/steps", response_model=KRITestStepResponse, status_code=status.HTTP_201_CREATED)
def add_test_step(kri_id: int, step_in: KRITestStepCreate, db: Session = Depends(get_db)):
    """Add a test step, then re-interpret the plan so it stays in sync (R3)."""
    _require_kri(db, kri_id)
    return _step_write(db, kri_id, lambda repo: repo.add_test_step(kri_id, step_in), lambda s: s)


@router.put("/{kri_id}/steps", response_model=List[KRITestStepResponse])
def replace_test_steps(kri_id: int, bulk: TestStepBulkUpdate, db: Session = Depends(get_db)):
    """Atomically replace the whole ordered step set and re-interpret the plan."""

    def mutate(repo):
        if bulk.data_source_ids is not None:
            repo.set_data_sources(kri_id, bulk.data_source_ids)
        return repo.replace_test_steps(kri_id, bulk.steps)

    _require_kri(db, kri_id)
    return _step_write(db, kri_id, mutate, lambda steps: steps)


@router.put("/{kri_id}/steps/{step_id}", response_model=KRITestStepResponse)
def update_test_step(kri_id: int, step_id: int, step_in: KRITestStepUpdate, db: Session = Depends(get_db)):
    """Update a test step, then re-interpret the plan so it stays in sync (R3)."""
    _require_kri(db, kri_id)

    def mutate(repo):
        step = repo.update_test_step(step_id, step_in)
        if not step or step.kri_id != kri_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Test step {step_id} not found."
            )
        return step

    return _step_write(db, kri_id, mutate, lambda s: s)


@router.delete("/{kri_id}/steps/{step_id}", response_model=StandardResponse)
def delete_test_step(kri_id: int, step_id: int, db: Session = Depends(get_db)):
    """Delete a test step, then re-interpret the plan."""
    _require_kri(db, kri_id)

    def mutate(repo):
        if not repo.delete_test_step(step_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Test step {step_id} not found."
            )
        return True

    _step_write(db, kri_id, mutate, lambda _: None)
    return StandardResponse(message=f"Test step {step_id} deleted successfully.")


@router.patch("/{kri_id}/steps/reorder", response_model=List[KRITestStepResponse])
def reorder_test_steps(kri_id: int, req: StepReorderRequest, db: Session = Depends(get_db)):
    """Reorder test steps, then re-interpret the plan."""
    _require_kri(db, kri_id)
    return _step_write(
        db,
        kri_id,
        lambda repo: repo.reorder_test_steps(kri_id, req.reorder_list),
        lambda steps: steps,
    )


# --- Threshold Endpoints ---
@router.post("/{kri_id}/thresholds", response_model=KRIThresholdResponse, status_code=status.HTTP_201_CREATED)
def add_threshold(kri_id: int, th_in: KRIThresholdCreate, db: Session = Depends(get_db)):
    """Add a variance comparison threshold to a KRI, then re-interpret the plan."""
    _require_kri(db, kri_id)
    threshold = KRIRepository(db).add_threshold(kri_id, th_in)
    _regenerate_or_422(db, kri_id)
    return threshold


@router.put("/{kri_id}/thresholds/{threshold_id}", response_model=KRIThresholdResponse)
def update_threshold(kri_id: int, threshold_id: int, th_in: KRIThresholdUpdate, db: Session = Depends(get_db)):
    """Update a variance threshold, then re-interpret the plan."""
    _require_kri(db, kri_id)
    threshold = KRIRepository(db).update_threshold(threshold_id, th_in)
    if not threshold or threshold.kri_id != kri_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Threshold {threshold_id} not found.")
    _regenerate_or_422(db, kri_id)
    return threshold


@router.delete("/{kri_id}/thresholds/{threshold_id}", response_model=StandardResponse)
def delete_threshold(kri_id: int, threshold_id: int, db: Session = Depends(get_db)):
    """Delete a threshold configuration, then re-interpret the plan."""
    _require_kri(db, kri_id)
    if not KRIRepository(db).delete_threshold(threshold_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Threshold {threshold_id} not found.")
    _regenerate_or_422(db, kri_id)
    return StandardResponse(message=f"Threshold {threshold_id} deleted successfully.")


# --- Schedule and Reviewer Endpoints ---
@router.post("/{kri_id}/schedule", response_model=KRIScheduleCreate)
def configure_schedule(kri_id: int, sch_in: KRIScheduleCreate, db: Session = Depends(get_db)):
    """Configure or update audit execution schedule."""
    _require_kri(db, kri_id)
    return KRIRepository(db).create_or_update_schedule(kri_id, sch_in)


@router.post("/{kri_id}/reviewers", response_model=ReviewerResponse, status_code=status.HTTP_201_CREATED)
def add_reviewer(kri_id: int, rev_in: ReviewerCreate, db: Session = Depends(get_db)):
    """Assign a reviewer to a KRI."""
    _require_kri(db, kri_id)
    return KRIRepository(db).add_reviewer(kri_id, rev_in)

