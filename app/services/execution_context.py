"""Execution Context for Audit Runs.

Maintains scoped, in-memory and database-backed state for an audit run.
Prevents passing full datasets repeatedly to the LLM by storing dataset references.
"""

from typing import Dict, Any, List, Optional
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

        # In-memory intermediate state store
        self.datasets: Dict[str, List[Dict[str, Any]]] = {}
        self.dataset_metadata: Dict[str, Dict[str, Any]] = {}
        self.comparisons: Dict[str, Dict[str, Any]] = {}
        self.candidate_exceptions: List[Dict[str, Any]] = []
        self.generated_evidence: Dict[str, Dict[str, Any]] = {}
        self.calculated_metrics: Optional[Dict[str, Any]] = None
        self.tool_invocations_count: int = 0

    def store_dataset(
        self,
        dataset_reference: str,
        entity_name: str,
        source_system: str,
        records: List[Dict[str, Any]],
    ) -> None:
        """Store dataset in memory and persist in database."""
        self.datasets[dataset_reference] = records
        self.dataset_metadata[dataset_reference] = {
            "entity_name": entity_name,
            "source_system": source_system,
            "record_count": len(records),
        }
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
            return db_ds.dataset_payload
        return None

    def store_comparison(self, comparison_reference: str, comparison_result: Dict[str, Any]) -> None:
        """Store comparison results."""
        self.comparisons[comparison_reference] = comparison_result

    def get_comparison(self, comparison_reference: str) -> Optional[Dict[str, Any]]:
        """Retrieve comparison results."""
        return self.comparisons.get(comparison_reference)

    def add_candidate_exception(self, exception_data: Dict[str, Any]) -> None:
        """Add flagged candidate exception."""
        self.candidate_exceptions.append(exception_data)

    def get_active_threshold_value(self) -> float:
        """Get the active KRI threshold percentage or fallback to 10.0%."""
        if self.kri.thresholds:
            for th in self.kri.thresholds:
                if th.is_active:
                    return th.threshold_value
        return 10.0
