"""Execution Context for Audit Runs.

Maintains scoped, in-memory and database-backed state for an audit run.
Prevents passing full datasets repeatedly to the LLM by storing dataset references.

Dataset bindings are **source-scoped**: a reference is keyed by
``(data_source_code, entity_code)`` and surfaced under a stable plan alias, so two data
sources exposing the same entity produce two distinct populations instead of silently
overwriting each other. Comparisons are persisted alongside datasets so a run can be
replayed from its stored intermediate state.
"""

from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from app.repositories.financial_repository import FinancialRepository
from app.repositories.audit_repository import AuditRepository
from app.models.kri import KRI


class AuditExecutionContext:
    """Scoped execution context for an individual audit run."""

    def __init__(
        self,
        audit_run_id: int,
        run_reference: str,
        kri: KRI,
        db: Session,
        financial_repo: FinancialRepository,
        audit_repo: AuditRepository,
        start_date: Any,
        end_date: Any,
        customer_filter: Optional[str] = None,
        run_logger: Optional[Any] = None,
        plan: Optional[Dict[str, Any]] = None,
    ):
        self.audit_run_id = audit_run_id
        self.run_reference = run_reference
        self.kri = kri
        self.db = db
        self.financial_repo = financial_repo
        self.audit_repo = audit_repo
        self.start_date = start_date
        self.end_date = end_date
        self.customer_filter = customer_filter
        self.run_logger = run_logger
        self.plan = plan or {}

        # In-memory intermediate state store
        self.datasets: Dict[str, List[Dict[str, Any]]] = {}
        self.dataset_metadata: Dict[str, Dict[str, Any]] = {}
        self.comparisons: Dict[str, Dict[str, Any]] = {}
        self.candidate_exceptions: List[Dict[str, Any]] = []
        self.generated_evidence: Dict[str, Dict[str, Any]] = {}
        self.calculated_metrics: Optional[Dict[str, Any]] = None
        self.tool_invocations_count: int = 0
        # Free-text output of PREPARE steps, kept for the run summary.
        self.prepared_notes: List[Dict[str, Any]] = []

        # Plan bindings: plan-declared alias -> concrete run-time reference
        self.dataset_refs: Dict[str, str] = {}
        self.comparison_refs: Dict[str, str] = {}
        self.metric_ref: Optional[str] = None
        # (data_source_code, entity_code) -> plan alias
        self._alias_index: Dict[Tuple[str, str], str] = {}

    # -- plan bindings -------------------------------------------------------

    def alias_for(self, data_source_code: str, entity_code: str) -> str:
        """Return the stable plan alias bound to a (source, entity) pair, registering it once."""
        key = ((data_source_code or "").upper(), (entity_code or "").upper())
        if key not in self._alias_index:
            self._alias_index[key] = f"{key[1].lower()}_{key[0].lower()}"
        return self._alias_index[key]

    def bind_dataset(self, alias: str, dataset_reference: str) -> None:
        self.dataset_refs[alias] = dataset_reference

    def resolve_dataset(self, alias: str) -> Optional[str]:
        return self.dataset_refs.get(alias)

    def bind_comparison(self, alias: str, comparison_reference: str) -> None:
        self.comparison_refs[alias] = comparison_reference

    def resolve_comparison(self, alias: str) -> Optional[str]:
        return self.comparison_refs.get(alias)

    # -- datasets ------------------------------------------------------------

    def store_dataset(
        self,
        dataset_reference: str,
        entity_name: str,
        source_system: str,
        records: List[Dict[str, Any]],
        alias: Optional[str] = None,
    ) -> None:
        """Store dataset in memory and persist in database."""
        self.datasets[dataset_reference] = records
        self.dataset_metadata[dataset_reference] = {
            "entity_name": entity_name,
            "source_system": source_system,
            "record_count": len(records),
            "alias": alias,
        }
        if alias:
            self._alias_index.setdefault(
                ((source_system or "").upper(), (entity_name or "").upper()), alias
            )
        # Persist to execution_datasets table
        self.audit_repo.store_dataset(
            audit_run_id=self.audit_run_id,
            dataset_reference=dataset_reference,
            entity_name=entity_name,
            source_system=source_system,
            record_count=len(records),
            dataset_payload=records,
        )

    def get_dataset(self, dataset_reference: str) -> Optional[List[Dict[str, Any]]]:
        """Retrieve dataset by reference ID."""
        if dataset_reference in self.datasets:
            return self.datasets[dataset_reference]

        # Check in DB if not in memory
        db_ds = self.audit_repo.get_dataset(self.audit_run_id, dataset_reference)
        if db_ds:
            self.datasets[dataset_reference] = db_ds.dataset_payload
            self.dataset_metadata.setdefault(
                dataset_reference,
                {
                    "entity_name": db_ds.entity_name,
                    "source_system": db_ds.source_system,
                    "record_count": db_ds.record_count,
                    "alias": None,
                },
            )
            return db_ds.dataset_payload
        return None

    def get_population_records(self, dataset_reference: Optional[str] = None) -> List[Dict[str, Any]]:
        """Resolve the population dataset explicitly, never by substring guessing."""
        if dataset_reference:
            records = self.get_dataset(dataset_reference)
            if records is not None:
                return records
            return []

        # Fallback for direct tool calls with no plan binding: first dataset bound as
        # population_role, else the largest dataset.
        candidates = [
            (ref, meta)
            for ref, meta in self.dataset_metadata.items()
            if (meta.get("entity_name") or "").upper() == "ORDER_INTAKE"
        ]
        if not candidates:
            candidates = list(self.dataset_metadata.items())
        if not candidates:
            return []
        ref = max(candidates, key=lambda item: item[1].get("record_count", 0))[0]
        return self.get_dataset(ref) or []

    # -- comparisons ---------------------------------------------------------

    def store_comparison(self, comparison_reference: str, comparison_result: Dict[str, Any]) -> None:
        """Store comparison results in memory and persist for replay determinism."""
        self.comparisons[comparison_reference] = comparison_result
        self.audit_repo.store_dataset(
            audit_run_id=self.audit_run_id,
            dataset_reference=comparison_reference,
            entity_name="COMPARISON",
            source_system="DERIVED",
            record_count=len(comparison_result.get("matched_pairs", []))
            + len(comparison_result.get("missing_pos", []))
            + len(comparison_result.get("ambiguous_matches", [])),
            dataset_payload=comparison_result,
        )

    def get_comparison(self, comparison_reference: str) -> Optional[Dict[str, Any]]:
        """Retrieve comparison results."""
        if comparison_reference in self.comparisons:
            return self.comparisons[comparison_reference]
        db_ds = self.audit_repo.get_dataset(self.audit_run_id, comparison_reference)
        if db_ds and db_ds.entity_name == "COMPARISON":
            self.comparisons[comparison_reference] = db_ds.dataset_payload
            return db_ds.dataset_payload
        return None

    # -- exceptions ----------------------------------------------------------

    def add_candidate_exception(self, exception_data: Dict[str, Any]) -> None:
        """Add flagged candidate exception."""
        self.candidate_exceptions.append(exception_data)

    def get_active_threshold_value(self, default: Any = ...) -> float:
        """Resolve the threshold the KRI should be evaluated against.

        Preference order: the plan's own resolved value, then the KRI's active threshold row.
        Silently defaulting to 10% is exactly the hidden assumption rule R4 forbids, so with
        no ``default`` supplied an unresolvable threshold raises. Callers that can proceed
        without one (evidence packaging, advisory reporting) pass ``default=None``.
        """
        resolved = self.plan.get("threshold") if self.plan else None
        if resolved is not None:
            return float(resolved)
        for th in self.kri.thresholds or []:
            if th.is_active:
                return float(th.threshold_value)
        if default is not ...:
            return default
        raise ValueError(
            f"KRI '{self.kri.identifier}' has no active threshold configured and the execution "
            f"plan does not resolve one, so a variance test cannot be evaluated. Add a threshold "
            f"to the KRI configuration."
        )
