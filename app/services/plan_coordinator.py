"""Plan Coordinator.

Owns the lifecycle of execution plans: interpretation, validation, versioned persistence and
staleness detection. Every path that can change a plan's meaning (step edits, data source
assignment changes, threshold changes) funnels through :meth:`ensure_current_plan`, so a plan
is *always* in sync with the configuration it was derived from (R3).

There is no approval step. Writing unambiguous steps is the user's job; if they cannot be
interpreted, the write is rejected with a per-step message (R2, R4).
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.kri import KRI
from app.models.audit import ExecutionPlan
from app.repositories.audit_repository import AuditRepository
from app.repositories.kri_repository import KRIRepository
from app.services.data_source_registry import build_capability_manifest
from app.services.plan_interpreter import InterpretationError, PlanInterpreter
from app.services.plan_service import PlanService

logger = logging.getLogger(__name__)


class PlanStaleError(RuntimeError):
    """The stored plan no longer matches the KRI configuration and could not be refreshed."""

    def __init__(self, message: str, step_errors: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.step_errors = step_errors or []


class PlanCoordinator:
    """Interpret -> validate -> persist, with hash-bound staleness detection."""

    def __init__(self, db: Session, llm_provider: Optional[Any] = None):
        self.db = db
        self.kri_repo = KRIRepository(db)
        self.audit_repo = AuditRepository(db)

    # -- interpretation -------------------------------------------------------

    def interpret(self, kri_id: int, sample_rows: int = 0):
        """Interpret a KRI's steps into a ``StructuredPlan`` without persisting it."""
        kri = self._load_kri(kri_id)
        interpreter = PlanInterpreter(db=self.db)
        return interpreter.interpret(kri, sample_rows=sample_rows)

    # -- persistence ----------------------------------------------------------

    def ensure_current_plan(
        self,
        kri_id: int,
        force_new_version: bool = False,
        sample_rows: int = 0,
        strict_stages: bool = False,
    ) -> ExecutionPlan:
        """Return the plan matching the KRI's current configuration, regenerating if needed.

        ``strict_stages`` is ``False`` by default so an author can add steps one at a time;
        activation and run-time resolution pass ``True``.
        """
        kri = self._load_kri(kri_id)
        steps_hash = PlanService.compute_steps_hash(kri)

        if not force_new_version:
            existing = self.audit_repo.get_plan_for_steps_hash(kri_id, steps_hash)
            if existing is not None and existing.is_valid:
                return existing

        return self._regenerate(kri, steps_hash, sample_rows=sample_rows, strict_stages=strict_stages)

    def _regenerate(
        self,
        kri: KRI,
        steps_hash: Optional[str] = None,
        sample_rows: int = 0,
        strict_stages: bool = False,
    ) -> ExecutionPlan:
        steps_hash = steps_hash or PlanService.compute_steps_hash(kri)
        interpreter = PlanInterpreter(db=self.db)
        try:
            plan = interpreter.interpret(kri, sample_rows=sample_rows)
        except InterpretationError as exc:
            logger.warning("Plan interpretation failed for KRI %s: %s", kri.identifier, exc)
            raise PlanStaleError(str(exc), step_errors=exc.step_errors) from exc

        manifest = build_capability_manifest(kri, db=self.db, sample_rows=sample_rows)
        validation = PlanService.validate_plan(
            kri, plan, manifest=manifest, steps_hash=steps_hash, strict_stages=strict_stages
        )
        if not validation.is_valid:
            raise PlanStaleError(
                f"Interpreted plan for KRI '{kri.identifier}' is not runnable: {validation.errors}",
                step_errors=[
                    {"error": err, "candidates": []} for err in validation.errors
                ],
            )

        plan.data_sources_used = PlanService.summarize_data_sources(plan)
        return self.audit_repo.create_execution_plan(
            kri_id=kri.id,
            plan_payload=plan.model_dump(mode="json"),
            is_valid=True,
            validation_errors=validation.warnings,
            source=_plan_source(plan),
            steps_hash=steps_hash,
            plan_hash=PlanService.hash_payload(plan.model_dump(mode="json")),
            interpreter_meta=plan.interpreter_meta,
        )

    def regenerate(self, kri_id: int, sample_rows: int = 0) -> ExecutionPlan:
        """Force a new plan version even when the inputs are unchanged."""
        kri = self._load_kri(kri_id)
        return self._regenerate(kri, sample_rows=sample_rows)
    def refresh_after_change(self, kri_id: int) -> Optional[ExecutionPlan]:
        """Regenerate after a step / data source / threshold change.

        Returns the new plan, or ``None`` when the change could not be interpreted. The
        caller is responsible for surfacing the error; the previously stored plan is left
        untouched so history remains intact.
        """
        try:
            return self.ensure_current_plan(kri_id)
        except PlanStaleError as exc:
            logger.warning("Plan refresh after change failed for KRI %s: %s", kri_id, exc)
            return None

    # -- queries --------------------------------------------------------------

    def is_plan_current(self, kri_id: int) -> bool:
        kri = self._load_kri(kri_id)
        steps_hash = PlanService.compute_steps_hash(kri)
        return self.audit_repo.get_plan_for_steps_hash(kri_id, steps_hash) is not None

    def current_steps_hash(self, kri_id: int) -> str:
        return PlanService.compute_steps_hash(self._load_kri(kri_id))

    def list_plans(self, kri_id: int) -> List[ExecutionPlan]:
        return self.audit_repo.list_plans_for_kri(kri_id)

    def get_plan(self, plan_id: int) -> Optional[ExecutionPlan]:
        return self.audit_repo.get_plan_by_id(plan_id)

    def diff(self, from_plan_id: int, to_plan_id: int) -> Dict[str, Any]:
        source = self.audit_repo.get_plan_by_id(from_plan_id)
        target = self.audit_repo.get_plan_by_id(to_plan_id)
        if source is None or target is None:
            raise ValueError("Both plan versions must exist to compute a diff.")

        before = {s["step_number"]: s for s in (source.plan_payload or {}).get("steps", [])}
        after = {s["step_number"]: s for s in (target.plan_payload or {}).get("steps", [])}

        entries: List[Dict[str, Any]] = []
        for number in sorted(set(before) | set(after)):
            b, a = before.get(number), after.get(number)
            if b is None:
                change = "ADDED"
            elif a is None:
                change = "REMOVED"
            elif _canonical(b) != _canonical(a):
                change = "CHANGED"
            else:
                change = "UNCHANGED"
            entries.append({"step_number": number, "change": change, "before": b, "after": a})

        return {
            "from_version": source.version,
            "to_version": target.version,
            "from_plan_id": source.id,
            "to_plan_id": target.id,
            "entries": entries,
        }

    # -- helpers --------------------------------------------------------------

    def _load_kri(self, kri_id: int) -> KRI:
        kri = self.kri_repo.get_kri_by_id(kri_id)
        if kri is None:
            raise ValueError(f"KRI with ID {kri_id} not found.")
        return kri


def _plan_source(plan: Any) -> str:
    origins = {step.origin for step in plan.steps}
    if origins == {"LLM"}:
        return "LLM"
    if "LLM" in origins:
        return "MIXED"
    return "RULES"


def _canonical(step: Dict[str, Any]) -> Tuple:
    """Comparable projection of a planned step, ignoring provenance and prose."""
    return (
        step.get("operation"),
        step.get("tool_name"),
        _canonical_json(step.get("parameters") or {}),
        step.get("data_source_code"),
        step.get("entity_code"),
    )


def _canonical_json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str)
