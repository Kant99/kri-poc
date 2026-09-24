"""Tool 6: build_evidence.

Builds structured, reproducible audit evidence for each flagged exception.
Retains source record provenance, numerical calculation proofs, and threshold configurations.
"""

import uuid
import hashlib
import json
from typing import List
from app.schemas.tools import BuildEvidenceInput, BuildEvidenceOutput, EvidenceDetail
from app.services.execution_context import AuditExecutionContext


def build_evidence_handler(
    input_data: BuildEvidenceInput,
    context: AuditExecutionContext,
) -> BuildEvidenceOutput:
    """Construct and persist evidence records for all candidate exceptions."""
    evidence_list: List[EvidenceDetail] = []

    for exc in context.candidate_exceptions:
        exc_ref = exc.get("exception_reference") or f"exc_{uuid.uuid4().hex[:8]}"
        exc["exception_reference"] = exc_ref
        if input_data.exception_references and exc_ref not in input_data.exception_references:
            continue

        order_id = exc.get("order_id")
        order_record = exc.get("order_record") or {}
        po_record = exc.get("po_record")

        calc_details = exc.get("calculation_details", {
            "order_amount": exc.get("order_amount"),
            "po_amount": exc.get("po_amount"),
            "difference_amount": exc.get("difference_amount"),
            "difference_percentage": exc.get("difference_percentage"),
        })

        th_details = exc.get("threshold_details", {
            "threshold_type": "PERCENTAGE_DIFFERENCE",
            "operator": ">",
            "threshold_value": exc.get("threshold_value", context.get_active_threshold_value()),
        })

        explanation = exc.get("explanation", "Audit exception identified during KRI evaluation.")
        source_system = order_record.get("source_system", "SAP_ECC")

        # Generate cryptographic hash for evidence reproducibility proof
        hash_payload = json.dumps({
            "order_id": order_id,
            "order_amount": exc.get("order_amount"),
            "po_amount": exc.get("po_amount"),
            "difference_percentage": exc.get("difference_percentage"),
            "threshold_value": th_details.get("threshold_value"),
        }, sort_keys=True)
        reproducibility_hash = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()

        # Persist in DB
        db_ev = context.audit_repo.store_evidence(
            audit_run_id=context.audit_run_id,
            exception_reference=exc_ref,
            order_id=order_id,
            source_system=source_system,
            order_payload=order_record,
            po_payload=po_record,
            calculation_details=calc_details,
            threshold_details=th_details,
            explanation=explanation,
        )

        detail = EvidenceDetail(
            evidence_reference=db_ev.evidence_reference,
            exception_reference=exc_ref,
            order_id=order_id,
            source_system=source_system,
            order_record=order_record,
            po_record=po_record,
            calculation_details=calc_details,
            threshold_details=th_details,
            explanation=explanation,
            reproducibility_hash=reproducibility_hash,
        )
        evidence_list.append(detail)
        context.generated_evidence[exc_ref] = detail.model_dump()

    return BuildEvidenceOutput(
        status="SUCCESS",
        audit_run_reference=context.run_reference,
        evidence_count=len(evidence_list),
        evidence_records=evidence_list,
    )
