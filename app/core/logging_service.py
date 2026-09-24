"""Run Logger & Audit Execution Event Tracking Service.

Persists chronological, human-readable execution logs to:
1. File: `logs/runs/{run_reference}.log`
2. File: `logs/runs/{run_reference}.json`
3. Database: `audit_run_logs` table
4. Python standard logging / console output.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.audit import AuditRunLog

logger = logging.getLogger("kri_audit")


class RunLogger:
    """Manages multi-target logging for an individual audit run."""

    def __init__(self, run_reference: str, audit_run_id: int, db: Optional[Session] = None):
        self.run_reference = run_reference
        self.audit_run_id = audit_run_id
        self.db = db
        self.events: List[Dict[str, Any]] = []
        self.start_time = time.time()

        # Ensure logs directory exists
        self.logs_dir = Path(settings.logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.log_file_path = self.logs_dir / f"{run_reference}.log"
        self.json_file_path = self.logs_dir / f"{run_reference}.json"

        # Initialize log file with header
        header = (
            f"================================================================================\n"
            f"CONTINUOUS INTERNAL AUDIT KRI ENGINE - EXECUTION TRACE LOG\n"
            f"Run Reference : {self.run_reference}\n"
            f"Audit Run ID  : {self.audit_run_id}\n"
            f"Timestamp     : {datetime.utcnow().isoformat()}Z\n"
            f"Environment   : {settings.environment}\n"
            f"================================================================================\n\n"
        )
        with open(self.log_file_path, "w", encoding="utf-8") as f:
            f.write(header)

    def _append_to_file(self, text: str) -> None:
        """Append text entry to the human-readable log file."""
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception as e:
            logger.error("Failed writing to log file %s: %s", self.log_file_path, e)

    def _save_json(self) -> None:
        """Persist structured events JSON."""
        try:
            with open(self.json_file_path, "w", encoding="utf-8") as f:
                json.dump(self.events, f, indent=2, default=str)
        except Exception as e:
            logger.error("Failed writing to json log file %s: %s", self.json_file_path, e)

    def log_event(
        self,
        stage: str,
        message: str,
        step_number: int = 0,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record an event to file, memory, database, and console."""
        timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        event = {
            "timestamp": timestamp_str,
            "step_number": step_number,
            "stage": stage,
            "message": message,
            "payload": payload or {},
        }
        self.events.append(event)

        # Formatted log line
        log_line = f"[{timestamp_str}] [{stage:<20}] (Step {step_number:02d}) {message}"
        if payload:
            formatted_payload = json.dumps(payload, indent=4, default=str)
            # Indent payload
            indented = "\n".join("    " + line for line in formatted_payload.splitlines())
            log_line += f"\n{indented}"

        self._append_to_file(log_line)
        self._save_json()

        logger.info("[%s] %s: %s", self.run_reference, stage, message)

        # Database persistence
        if self.db:
            try:
                db_log = AuditRunLog(
                    audit_run_id=self.audit_run_id,
                    step_number=step_number,
                    stage=stage,
                    message=message,
                    payload=payload,
                    created_at=datetime.utcnow(),
                )
                self.db.add(db_log)
                self.db.commit()
            except Exception as e:
                logger.error("Error saving AuditRunLog to DB: %s", e)
                try:
                    self.db.rollback()
                except Exception:
                    pass

    def log_run_init(
        self,
        kri_identifier: str,
        kri_name: str,
        start_date: Any,
        end_date: Any,
        customer_filter: Optional[str],
        provider_mode: str,
    ) -> None:
        msg = f"Initializing audit run for {kri_identifier} ({kri_name})"
        payload = {
            "kri_identifier": kri_identifier,
            "kri_name": kri_name,
            "audit_window": f"{start_date} to {end_date}",
            "customer_filter": customer_filter or "ALL_CUSTOMERS",
            "orchestration_mode": provider_mode,
        }
        self.log_event("RUN_INITIALIZATION", msg, step_number=0, payload=payload)

    def log_plan_loaded(self, plan_dict: Dict[str, Any]) -> None:
        steps = plan_dict.get("steps", [])
        msg = f"Structured execution plan loaded with {len(steps)} planned operations."
        self.log_event("PLAN_LOADED", msg, step_number=0, payload={"planned_steps": steps})

    def log_tool_call(
        self,
        step_number: int,
        tool_name: str,
        input_payload: Dict[str, Any],
        output_payload: Dict[str, Any],
        status: str,
        execution_time_ms: float,
        error_message: Optional[str] = None,
    ) -> None:
        msg = f"Tool '{tool_name}' executed with status {status} in {execution_time_ms}ms"
        payload = {
            "tool_name": tool_name,
            "status": status,
            "execution_time_ms": execution_time_ms,
            "input_arguments": input_payload,
            "output_result": output_payload,
            "error_message": error_message,
        }
        self.log_event("TOOL_EXECUTION", msg, step_number=step_number, payload=payload)

    def log_exception_detected(self, exc_data: Dict[str, Any]) -> None:
        order_id = exc_data.get("order_id")
        exc_type = exc_data.get("exception_type")
        severity = exc_data.get("severity")
        diff_pct = exc_data.get("difference_percentage")
        msg = f"Flagged Exception [{exc_type}] for Order {order_id} (Severity: {severity}, Variance: {diff_pct}%)"
        self.log_event("EXCEPTION_FLAGGED", msg, step_number=0, payload=exc_data)

    def log_metrics(self, metrics: Dict[str, Any]) -> None:
        msg = (
            f"Calculated Aggregate Metrics: Total Transactions={metrics.get('total_transactions', 0)}, "
            f"Matched={metrics.get('matched_count', 0)}, Exceptions={metrics.get('total_exceptions', 0)} "
            f"(Missing POs: {metrics.get('missing_po_count', 0)}, Amount Mismatches: {metrics.get('amount_mismatch_count', 0)})"
        )
        self.log_event("METRICS_CALCULATED", msg, step_number=0, payload=metrics)

    def log_evidence_built(self, count: int) -> None:
        msg = f"Built {count} reproducible evidence records with cryptographic reproducibility proofs."
        self.log_event("EVIDENCE_BUILT", msg, step_number=0, payload={"evidence_count": count})

    def log_run_complete(
        self,
        status: str,
        total_exceptions: int,
        metrics: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        duration = round(time.time() - self.start_time, 3)
        msg = f"Audit Run finished with status {status} in {duration}s. Total exceptions flagged: {total_exceptions}."
        payload = {
            "status": status,
            "duration_seconds": duration,
            "total_exceptions": total_exceptions,
            "metrics": metrics,
            "error_message": error_message,
        }
        self.log_event("RUN_COMPLETED", msg, step_number=0, payload=payload)

        # Footer
        footer = (
            f"\n================================================================================\n"
            f"AUDIT RUN FINISHED : {status} (Duration: {duration}s, Exceptions: {total_exceptions})\n"
            f"Log File Location  : {self.log_file_path}\n"
            f"================================================================================\n"
        )
        self._append_to_file(footer)

    def get_formatted_log_text(self) -> str:
        """Read and return complete content of the log file."""
        if self.log_file_path.exists():
            with open(self.log_file_path, "r", encoding="utf-8") as f:
                return f.read()
        return "Log file not found."
