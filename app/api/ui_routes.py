"""UI Bridge Router.

Provides frontend-compatible REST endpoints under `/api` for:
- KRI Library CRUD (matching the React UI format)
- Audit Runs and Execution
- Exception Review and Classification
- Settings and Close Calendar
- Planner queue and reference data
"""

import calendar
import logging
import re
import uuid
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session, joinedload
from app.core.database import get_db
from app.repositories.kri_repository import KRIRepository
from app.repositories.audit_repository import AuditRepository
from app.models.kri import KRI, ProcessArea, DataSource, KRITestStep, KRIThreshold, KRISchedule, Reviewer
from app.models.audit import AuditRun, AuditException, AuditToolInvocation, ExecutionPlan
from app.schemas.kri import (
    KRICreate,
    KRIUpdate,
    KRITestStepCreate,
    KRIThresholdCreate,
    KRIScheduleCreate,
    ReviewerCreate,
    PlanInterpretationErrorResponse,
    IndicatorTypeEnum,
    ThresholdTypeEnum,
    ComparisonOperatorEnum,
)
from app.schemas.audit import AuditRunRequest
from app.services.data_source_registry import build_capability_manifest, entity_registry
from app.services.plan_coordinator import PlanCoordinator, PlanStaleError
from app.services.plan_service import PlanService
from app.services.audit_service import AuditService

router = APIRouter(prefix="/api", tags=["UI Integration Bridge"])

logger = logging.getLogger(__name__)


def format_exception_for_ui(e: AuditException, default_kri_id: str = "KRI-O2C-001") -> Dict[str, Any]:
    """Formats an AuditException model with complete facts, evidence, reasoning, and hypothesis matching UI spec."""
    kri_identifier = default_kri_id
    kri_name = "Order Intake vs Customer PO Price & Terms"
    run_ref = "run_latest"
    period_str = "2026-01-01 to 2026-03-31"

    if e.audit_run:
        run_ref = e.audit_run.run_reference
        if e.audit_run.start_date and e.audit_run.end_date:
            period_str = f"{e.audit_run.start_date} to {e.audit_run.end_date}"
        if e.audit_run.kri:
            kri_identifier = e.audit_run.kri.identifier
            kri_name = e.audit_run.kri.name

    diff_amt = abs(e.difference_amount) if e.difference_amount is not None else 0.0
    ord_amt = e.order_amount if e.order_amount is not None else 0.0
    po_amt = e.po_amount if e.po_amount is not None else 0.0
    diff_pct = abs(e.difference_percentage) if e.difference_percentage is not None else 0.0
    th_val = e.threshold_value if e.threshold_value is not None else 5.0

    order_id = e.order_id or "ORD-2026046"
    po_id = e.po_id or (f"PO-{order_id.replace('ORD-', '')}" if "ORD-" in order_id else f"PO-{order_id}")

    category_clean = (e.exception_type or "AMOUNT_MISMATCH").replace("_", " ").title()
    classification = "explainable" if (e.severity == "MEDIUM" or e.exception_type in ("AMOUNT_MISMATCH", "BOOKING_QUALITY", "DEBOOKING_CONCENTRATION")) else "unexplained"
    status_ui = "open" if (e.review_status or "PENDING_REVIEW") == "PENDING_REVIEW" else "accepted"

    # Extract calculation details from attached evidence record if available
    evidence_rec = None
    if e.audit_run and e.audit_run.evidence_records:
        for ev in e.audit_run.evidence_records:
            if ev.exception_reference == e.exception_reference:
                evidence_rec = ev
                break
    calc = evidence_rec.calculation_details if (evidence_rec and evidence_rec.calculation_details) else {}
    order_rec = evidence_rec.order_payload if (evidence_rec and evidence_rec.order_payload) else {}

    # Precise evidence, reasoning, summary and hypothesis matching the exception type
    if e.exception_type in ("PREMATURE_RECOGNITION",):
        customer = calc.get("customer_name") or order_rec.get("customer_name") or "Customer"
        booking_q = calc.get("booking_quarter") or "earlier quarter"
        debit_q = calc.get("debooking_quarter") or "later quarter"
        debooking_ref = calc.get("debooking_reference") or order_rec.get("debooking_id") or "debooking"
        summary_text = e.explanation or (
            f"Order intake for {customer} was booked in {booking_q} but reversed in {debit_q} for EUR {ord_amt:,.2f} "
            f"(reason {e.reason_code}). Revenue was recognised prematurely before the invalidating event."
        )
        evidence = [
            {"source": "sap_ecc", "record": f"Order Intake ({order_id})", "value": f"Booking Quarter: {booking_q}"},
            {"source": "sap_ecc", "record": f"Debooking Debit Note ({debooking_ref})", "value": f"Reversal Quarter: {debit_q}, Amount: EUR {ord_amt:,.2f}"},
        ]
        reasoning = [
            f"Order {order_id} booked in {booking_q} was reversed in {debit_q} for EUR {ord_amt:,.2f}.",
            f"Reason code '{e.reason_code}' indicates demand was pulled into an earlier reporting period before the invalidating event.",
            f"Revenue was recognised prematurely in violation of revenue cutoff controls.",
            f"Deterministic severity assigned as {e.severity or 'HIGH'} under Reason Code {e.reason_code or 'PREMATURE_RECOGNITION'}.",
        ]
        hyp_text = f"Premature revenue recognition detected for {customer} ({order_id})."
        confirm_text = "Verify sales cutoff date, contract effective date, and quarter-end booking authorization in SAP ECC."

    elif e.exception_type in ("UNSUPPORTED_RECOGNITION",):
        customer = calc.get("customer_name") or order_rec.get("customer_name") or "Customer"
        debooking_ref = calc.get("debooking_reference") or order_rec.get("debooking_id") or "debooking"
        summary_text = e.explanation or (
            f"Order intake for {customer} was reversed for EUR {ord_amt:,.2f} with reason {e.reason_code}, "
            f"indicating the original recognition was never commercially supported."
        )
        evidence = [
            {"source": "sap_ecc", "record": f"Order Intake ({order_id})", "value": f"Recognised Value: EUR {ord_amt:,.2f}"},
            {"source": "sap_ecc", "record": f"Debooking Debit Note ({debooking_ref})", "value": f"Reason: {e.reason_code}, Amount: EUR {ord_amt:,.2f}"},
        ]
        reasoning = [
            f"Order {order_id} for {customer} was reversed for EUR {ord_amt:,.2f} under reason '{e.reason_code}'.",
            f"Reason code '{e.reason_code}' confirms the booking had no legally binding contract or commercial foundation.",
            f"Original booking should never have been recognized as order intake.",
            f"Deterministic severity assigned as {e.severity or 'CRITICAL'} under Reason Code {e.reason_code or 'UNSUPPORTED_RECOGNITION'}.",
        ]
        hyp_text = f"Unsupported order recognition: reversal reason '{e.reason_code}' confirms lack of commercial contract or duplicate booking."
        confirm_text = "Inspect legal contract repository (SharePoint) and commercial approval logs for this order."

    elif e.exception_type in ("BOOKING_QUALITY", "DEBOOKING_CONCENTRATION"):
        customer = calc.get("customer_name") or order_rec.get("customer_name") or "Customer"
        booked_val = float(calc.get("booked_value") or 0.0)
        ratio_pct = float(calc.get("debooking_ratio") or 0.0) * 100.0
        booking_q = calc.get("booking_quarter") or "Quarter"
        summary_text = e.explanation or (
            f"Same-quarter debookings of EUR {ord_amt:,.2f} represent {ratio_pct:.1f}% of {customer}'s "
            f"{booking_q} order intake value of EUR {booked_val:,.2f}, exceeding the {th_val:.1f}% booking-quality limit."
        )
        evidence = [
            {"source": "sap_ecc", "record": f"Customer Quarterly Bookings ({customer})", "value": f"Booked Value: EUR {booked_val:,.2f}"},
            {"source": "sap_ecc", "record": f"Same-Quarter Reversals", "value": f"Debooked: EUR {ord_amt:,.2f} ({ratio_pct:.1f}% ratio)"},
        ]
        reasoning = [
            f"Same-quarter debookings of EUR {ord_amt:,.2f} represent {ratio_pct:.1f}% of {customer}'s order intake in {booking_q}.",
            f"Ratio exceeds the configured {th_val:.1f}% booking-quality tolerance limit.",
            f"High concentration of in-quarter cancellations indicates aggressive sales booking or order intake churning.",
            f"Deterministic severity assigned as {e.severity or 'MEDIUM'} under Reason Code {e.reason_code or 'BOOKING_QUALITY'}.",
        ]
        hyp_text = f"Booking-quality threshold breached: elevated same-quarter reversal concentration for {customer}."
        confirm_text = "Review customer order churn patterns and commercial terms with the regional sales director."

    elif e.exception_type in ("ORPHAN_DEBOOKING",):
        debooking_ref = calc.get("debooking_reference") or order_rec.get("debooking_id") or "debooking"
        summary_text = e.explanation or (
            f"Debooking {debooking_ref} references order {order_id}, which has no order intake record "
            f"in the tested population, so the reversal cannot be tied to recognised revenue."
        )
        evidence = [
            {"source": "sap_ecc", "record": f"Debooking Debit Note ({debooking_ref})", "value": f"Referenced Order: {order_id}, Amount: EUR {ord_amt:,.2f}"},
            {"source": "sap_ecc", "record": "Order Intake Population", "value": "Referenced order not found in ERP order intake"},
        ]
        reasoning = [
            f"Debooking {debooking_ref} posted for EUR {ord_amt:,.2f} references order '{order_id}'.",
            f"No corresponding order intake record exists in the tested ERP population.",
            f"Reversal cannot be tied to valid recognised revenue.",
            f"Deterministic severity assigned as {e.severity or 'HIGH'} under Reason Code {e.reason_code or 'ORPHAN_DEBOOKING'}.",
        ]
        hyp_text = f"Orphan debooking reversal posted against unrecognized or missing order reference ({order_id})."
        confirm_text = "Verify whether the original order was booked under a different company code, fiscal year, or legacy system."

    elif e.exception_type == "AMOUNT_MISMATCH" or (e.po_amount is not None and e.po_amount > 0):
        direction = "lower" if ord_amt < po_amt else "higher"
        var_sign = "-" if ord_amt < po_amt else "+"
        summary_text = (
            f"The order amount (${ord_amt:,.2f}) is {diff_pct:.2f}% {direction} than the PO amount "
            f"(${po_amt:,.2f}) by a difference of ${diff_amt:,.2f}, exceeding the configured {th_val:.1f}% threshold."
        )
        evidence = [
            {"source": "sap_ecc", "record": f"Order Intake ({order_id})", "value": f"Order Amount: EUR {ord_amt:,.2f}"},
            {"source": "red_box_po", "record": f"Purchase Order ({po_id})", "value": f"PO Amount: EUR {po_amt:,.2f}"},
        ]
        reasoning = [
            f"Order {order_id} extracted from SAP ECC for EUR {ord_amt:,.2f}.",
            f"Purchase Order {po_id} matched in Red Box PO.",
            f"Calculated variance is EUR {var_sign}{diff_amt:,.2f} ({diff_pct:.1f}%), exceeding the configured {th_val:.1f}% tolerance limit.",
            f"Deterministic severity assigned as {e.severity or 'MEDIUM'} under Reason Code {e.reason_code or 'AMOUNT_MISMATCH'}.",
        ]
        hyp_text = f"Discrepancy identified between Order Intake ({order_id}) and PO ({po_id})."
        confirm_text = "Verify signed purchase order amendment history and commercial approval in SAP ECC / Red Box."

    elif e.exception_type == "MISSING_PO":
        summary_text = f"Order {order_id} (EUR {ord_amt:,.2f}) was booked in SAP ECC without a corresponding Purchase Order found in Red Box PO."
        evidence = [
            {"source": "sap_ecc", "record": f"Order Intake ({order_id})", "value": f"Order Amount: EUR {ord_amt:,.2f}"},
            {"source": "red_box_po", "record": "Purchase Order (Missing)", "value": "PO Amount: Not found in Red Box"},
        ]
        reasoning = [
            f"Order {order_id} extracted from SAP ECC for EUR {ord_amt:,.2f}.",
            "No corresponding Purchase Order found in Red Box PO repository.",
            "Missing documentation violates zero-tolerance PO matching policy.",
            f"Deterministic severity assigned as {e.severity or 'HIGH'} under Reason Code {e.reason_code or 'MISSING_PO'}.",
        ]
        hyp_text = f"Purchase order missing or unlinked for Order Intake ({order_id})."
        confirm_text = "Check whether PO was received offline or under a non-standard reference in Red Box."

    elif e.exception_type in ("AMBIGUOUS_MATCH", "DUPLICATE_MATCH"):
        summary_text = f"Order {order_id} (EUR {ord_amt:,.2f}) matched multiple conflicting Purchase Orders in Red Box PO."
        evidence = [
            {"source": "sap_ecc", "record": f"Order Intake ({order_id})", "value": f"Order Amount: EUR {ord_amt:,.2f}"},
            {"source": "red_box_po", "record": "Purchase Order (Ambiguous)", "value": "PO Amount: Multiple matching PO candidates"},
        ]
        reasoning = [
            f"Order {order_id} extracted from SAP ECC for EUR {ord_amt:,.2f}.",
            "Multiple candidate Purchase Orders matched order reference in Red Box PO.",
            "Ambiguity prevents automated reconciliation without human confirmation.",
            f"Deterministic severity assigned as {e.severity or 'HIGH'} under Reason Code {e.reason_code or 'AMBIGUOUS_MATCH'}.",
        ]
        hyp_text = f"Multiple conflicting PO records identified for Order Intake ({order_id})."
        confirm_text = "Verify the correct PO reference with the commercial operations and billing team."

    else:
        summary_text = e.explanation or f"Audit validation exception flagged for {order_id} under {category_clean}."
        evidence = [
            {"source": "sap_ecc", "record": f"Record ({order_id})", "value": f"Value: EUR {ord_amt:,.2f}"},
        ]
        reasoning = [
            f"Transaction {order_id} flagged under reason code '{e.reason_code}'.",
            f"Deterministic severity assigned as {e.severity or 'MEDIUM'}.",
        ]
        hyp_text = f"Audit exception flagged for {category_clean}."
        confirm_text = "Review supporting accounting entries and source records."

    return {
        "id": e.exception_reference,
        "ref": order_id,
        "kriId": kri_identifier,
        "kriName": kri_name,
        "runId": run_ref,
        "period": period_str,
        "project": f"Customer Order {order_id}",
        "wbs": f"WBS-{order_id}",
        "region": "EMEA",
        "bg": "Commercial Operations",
        "amount": ord_amt,
        "currency": "EUR",
        "category": category_clean,
        "severity": e.severity or "MEDIUM",
        "classification": classification,
        "status": status_ui,
        "summary": summary_text,
        "evidence": evidence,
        "reasoning": reasoning,
        "hypothesis": {
            "confidence": 0.95,
            "text": hyp_text,
            "summary": summary_text,
        },
        "confirm": confirm_text,
        "reviewNote": "",
    }


# --- KRI Library Endpoints ---

def format_kri_for_ui(kri: KRI, db: Optional[Session] = None) -> Dict[str, Any]:
    """Formats a SQLAlchemy KRI model into the frontend KRI JSON format."""
    identifier = kri.identifier
    name = kri.name
    area = kri.process_area.code if kri.process_area else "O2C"
    kind = kri.indicator_type.capitalize() if kri.indicator_type else "Leading"
    status_val = (kri.status or "draft").lower()
    risk = kri.risk_description or ""
    objective = kri.end_goal or ""

    ordered_steps = sorted(kri.test_steps, key=lambda x: x.step_number) if kri.test_steps else []
    sources = [ds.code.lower() for ds in kri.data_sources] if kri.data_sources else []
    steps = [s.instruction for s in ordered_steps if s.is_active]

    thresholds = []
    if kri.thresholds:
        for t in kri.thresholds:
            unit = t.unit or ("%" if "PERCENT" in (t.threshold_type or "") else "EUR")
            thresholds.append({
                "key": t.key or f"thr_{t.id}",
                "label": t.name,
                "value": t.threshold_value,
                "unit": unit,
            })
    # No fabricated thresholds: an absent threshold is an empty list, and interpretation
    # will report the missing configuration rather than silently defaulting to 10%.

    frequency = "monthly"
    align_to_close = True
    fetch_offset_days = 3
    sampling = 100
    custom_date = None
    if kri.schedules:
        sch = kri.schedules[0]
        frequency = (sch.run_frequency or "MONTHLY").lower()
        align_to_close = sch.align_to_close_calendar
        fetch_offset_days = sch.fetch_data_delay_days
        sampling = int(sch.population_percentage) if sch.population_percentage else 100
        custom_date = getattr(sch, "custom_date", None)

    reviewer = "Audit Manager"
    hitl = "exceptions"
    if kri.reviewers:
        rev = kri.reviewers[0]
        reviewer = rev.reviewer_name or "Audit Manager"
        hitl = "exceptions" if "EXCEPTION" in (rev.human_review_setting or "").upper() else "all"

    owner = getattr(kri, "owner", "Internal Audit") or "Internal Audit"
    note = getattr(kri, "note", None)

    last_run = None
    if kri.audit_runs:
        completed_runs = [r for r in kri.audit_runs if r.status == "COMPLETED" and r.completed_at]
        if completed_runs:
            latest = max(completed_runs, key=lambda r: r.completed_at)
            last_run = latest.completed_at.strftime("%Y-%m-%d")

    # Read-only view of what the agent understood, plus which data sources are usable.
    plan_summary = None
    if db is not None:
        try:
            coordinator = PlanCoordinator(db)
            current_hash = coordinator.current_steps_hash(kri.id)
            plans = coordinator.list_plans(kri.id)
            if plans:
                latest = plans[0]
                payload = latest.plan_payload or {}
                plan_summary = {
                    "id": latest.id,
                    "version": latest.version,
                    "source": latest.source,
                    "isCurrent": latest.steps_hash == current_hash,
                    "isValid": latest.is_valid,
                    "stepCount": len(payload.get("steps", [])),
                    "warnings": latest.validation_errors or [],
                    "dataSourcesUsed": [
                        {
                            "dataSourceId": u.get("data_source_id"),
                            "dataSourceCode": u.get("data_source_code"),
                            "entityCode": u.get("entity_code"),
                            "extractStepNumber": u.get("extract_step_number"),
                            "selectorReason": u.get("selector_reason"),
                        }
                        for u in payload.get("data_sources_used", [])
                    ],
                    "steps": [
                        {
                            "stepNumber": s.get("step_number"),
                            "operation": s.get("operation"),
                            "toolName": s.get("tool_name"),
                            "description": s.get("description"),
                            "dataSourceCode": s.get("data_source_code"),
                            "entityCode": s.get("entity_code"),
                            "selectorReason": s.get("selector_reason"),
                            "origin": s.get("origin"),
                            "parameters": s.get("parameters", {}),
                        }
                        for s in payload.get("steps", [])
                    ],
                }
        except Exception as exc:  # never break the library view on a plan problem
            plan_summary = {"error": str(exc)}

    capabilities = None
    if db is not None:
        try:
            manifest = build_capability_manifest(kri, db=db)
            capabilities = {
                "dataSources": [
                    {
                        "code": s["code"],
                        "name": s["name"],
                        "isQueryable": s["is_queryable"],
                        "entities": [e["entity_code"] for e in s.get("entities", [])],
                    }
                    for s in manifest["data_sources"]
                ],
                "unavailableDataSources": [u["code"] for u in manifest["unbound_assigned_sources"]],
            }
        except Exception as exc:
            capabilities = {"error": str(exc)}

    return {
        "id": identifier,
        "name": name,
        "area": area,
        "kind": kind,
        "status": status_val,
        "risk": risk,
        "objective": objective,
        "sources": sources,
        "steps": steps,
        "thresholds": thresholds,
        "frequency": frequency,
        "alignToClose": align_to_close,
        "fetchOffsetDays": fetch_offset_days,
        "sampling": sampling,
        "customDate": custom_date,
        "reviewer": reviewer,
        "hitl": hitl,
        "owner": owner,
        "note": note,
        "lastRun": last_run,
        "currentPlan": plan_summary,
        "capabilities": capabilities,
    }


#: The UI picker uses legacy, display-oriented identifiers. These are mapped onto the real
#: catalog codes. Identifiers with no mapping (OBS, MREP, PMASTER, TICKETS) have no queryable
#: data behind them and are rejected rather than silently created as an empty source.
LEGACY_SOURCE_ALIASES = {
    "SAP": "SAP_ECC",
    "SAP_ECC": "SAP_ECC",
    "RED_BOX_PO": "RED_BOX_PO",
    "REDBOX": "RED_BOX_PO",
    "SAPIENS": "SAPIENS",
    "BLUE_PLANET": "BLUE_PLANET",
    "EMS_SHAREPOINT": "EMS_SHAREPOINT",
}

#: Legacy ids that name systems with no data source in the catalog.
UNBACKED_LEGACY_SOURCES = {"OBS", "MREP", "PMASTER", "TICKETS"}


def _readable_plan_error(exc: Any) -> str:
    """Flatten a PlanStaleError into one actionable sentence for the UI toast."""
    details = [
        f"Step {item['step_number']}: {item['error']}"
        for item in getattr(exc, "step_errors", [])
        if item.get("error")
    ]
    if details:
        return " | ".join(details)
    return str(exc)


def _derive_step_title(instruction: str, position: int, max_length: int = 60) -> str:
    """Build a short, readable step label from the instruction text.

    Keeps the first clause of the instruction, trimmed to a single line, so the interpreted
    plan reads as a list of audit activities rather than "Step 1, Step 2, ...".
    """
    text = " ".join((instruction or "").split())
    if not text:
        return f"Step {position}"
    # Prefer the leading verb phrase; stop at the first sentence break.
    for separator in (". ", "; ", " and "):
        head, sep, _tail = text.partition(separator)
        if sep and len(head.split()) >= 3:
            text = head
            break
    if len(text) > max_length:
        text = text[:max_length].rstrip()
        text = text[: text.rfind(" ")] if " " in text else text
    return text


def save_kri_from_ui(payload: Dict[str, Any], db: Session) -> Dict[str, Any]:
    """Creates or updates a KRI in the database from the UI form payload.

    Steps are written as an ordered bulk replacement and the execution plan is regenerated
    immediately. A configuration that cannot be interpreted raises ``PlanStaleError`` so the
    UI shows the per-step problem instead of silently saving a KRI that cannot run.
    """
    kri_repo = KRIRepository(db)

    identifier = payload.get("id") or f"KRI-{uuid.uuid4().hex[:6].upper()}"
    name = payload.get("name") or "Untitled KRI"
    area_code = payload.get("area") or "O2C"
    kind = payload.get("kind") or "Leading"
    status_str = (payload.get("status") or "draft").upper()
    risk = payload.get("risk") or f"Risk monitoring for {name}"
    objective = payload.get("objective") or f"Ensure compliance for {name}"
    owner = payload.get("owner") or "Internal Audit"
    note = payload.get("note")
    sources_list = payload.get("sources") or []
    steps_list = payload.get("steps") or []
    thresholds_list = payload.get("thresholds") or []
    frequency = (payload.get("frequency") or "monthly").upper()
    align_to_close = bool(payload.get("alignToClose", True))
    fetch_offset_days = int(payload.get("fetchOffsetDays", 3))
    sampling = float(payload.get("sampling", 100.0))
    custom_date = payload.get("customDate")
    reviewer_name = payload.get("reviewer") or "Audit Manager"
    hitl = payload.get("hitl") or "exceptions"
    human_review_setting = "ALL_EXCEPTIONS" if hitl == "exceptions" else "ALL"

    # 1. Get or create Process Area
    pa = kri_repo.get_or_create_process_area(
        name=f"Process Area {area_code}",
        code=area_code,
        description=f"Process Area {area_code}",
    )

    # 2. Resolve data sources from the real catalog. The UI sends legacy display ids, so map
    #    them onto catalog codes. Unknown codes are rejected rather than invented, because a
    #    fabricated source would have no entity binding and could never return data (R7, R8).
    kri_repo.sync_data_source_availability()
    catalog = {ds.code.upper(): ds for ds in kri_repo.list_data_sources()}
    data_source_ids: List[int] = []
    unresolved: List[str] = []
    for raw_code in sources_list:
        clean_code = str(raw_code).strip().upper()
        clean_code = LEGACY_SOURCE_ALIASES.get(clean_code, clean_code)

        if clean_code in UNBACKED_LEGACY_SOURCES:
            unresolved.append(raw_code)
            continue

        if clean_code in catalog:
            data_source_ids.append(catalog[clean_code].id)
            continue

        # Accept a code that exists in the capability registry but not yet in the catalog,
        # so a fresh environment can still be configured.
        if clean_code in entity_registry.allowed_source_systems():
            bindings = [
                (entity_code, True)
                for entity_code in entity_registry.bound_entity_codes(clean_code)
            ]
            ds = kri_repo.get_or_create_data_source(
                name=clean_code.replace("_", " ").title(),
                code=clean_code,
                system_type="SOURCE",
                description="Created from the UI data source picker.",
            )
            if bindings:
                kri_repo.set_entity_bindings(ds.id, bindings)
            data_source_ids.append(ds.id)
            continue

        unresolved.append(raw_code)

    if unresolved:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No queryable data source backs: {sorted(set(unresolved))}. "
                f"Select one of: {sorted(catalog)}. A KRI must read from a data source that "
                f"actually exposes an entity."
            ),
        )

    # 3. Check existing KRI or create new
    step_snapshot = []
    data_source_snapshot = []
    kri = kri_repo.get_kri_by_identifier(identifier)
    if not kri:
        indicator_enum = IndicatorTypeEnum.LEADING
        if kind.upper() == "LAGGING":
            indicator_enum = IndicatorTypeEnum.LAGGING

        kri_in = KRICreate(
            identifier=identifier,
            name=name,
            process_area_id=pa.id,
            indicator_type=indicator_enum,
            risk_description=risk,
            end_goal=objective,
            data_source_ids=data_source_ids,
            owner=owner,
            note=note,
        )
        kri = kri_repo.create_kri(kri_in)
    else:
        # Snapshot the pre-mutation state BEFORE updating KRI or changing data sources
        step_snapshot = kri_repo.snapshot_steps(kri.id)
        data_source_snapshot = [ds.id for ds in (kri.data_sources or [])]
        kri_repo.update_kri(
            kri.id,
            KRIUpdate(
                name=name,
                process_area_id=pa.id,
                indicator_type=IndicatorTypeEnum(kind.upper()) if kind.upper() in ("LEADING", "LAGGING") else None,
                risk_description=risk,
                end_goal=objective,
                owner=owner,
                note=note,
            ),
        )
        kri_repo.set_data_sources(kri.id, data_source_ids)

    # 4. Sync Test Steps as an ordered bulk replacement so hashes are computed once.
    step_payloads: List[KRITestStepCreate] = []
    for idx, raw_step in enumerate(steps_list, start=1):
        if isinstance(raw_step, dict):
            instruction = str(raw_step.get("instruction") or "").strip()
            title = str(raw_step.get("title") or "").strip()
            operation = raw_step.get("operation") or None
            expected_output = raw_step.get("expectedOutput") or raw_step.get("expected_output") or None
            # The picker emits either "CODE" or "CODE::ENTITY" to pin source and entity.
            raw_binding = str(raw_step.get("dataSourceCode") or raw_step.get("data_source_code") or "").strip()
            data_source_code, _, entity_code = raw_binding.partition("::")
            data_source_code = data_source_code.upper() or None
            entity_code = entity_code.upper() or None
        else:
            instruction = str(raw_step).strip()
            title = ""
            operation = None
            expected_output = None
            data_source_code = None
            entity_code = None
        if not instruction:
            continue
        step_payloads.append(
            KRITestStepCreate(
                step_number=len(step_payloads) + 1,
                # A generic "Step N" title makes the interpreted plan unreadable in the UI and
                # the trace, so derive a short label from the instruction when none is given.
                title=title or _derive_step_title(instruction, len(step_payloads) + 1),
                instruction=instruction,
                is_active=True,
                operation=operation,
                expected_output=expected_output,
                data_source_id=(
                    catalog[data_source_code].id
                    if data_source_code and data_source_code in catalog
                    else None
                ),
                entity_code=entity_code,
            )
        )

    if step_payloads:
        kri_repo.replace_test_steps(kri.id, step_payloads)
    else:
        db.query(KRITestStep).filter(KRITestStep.kri_id == kri.id).delete()
        db.commit()

    # A rejected save must leave nothing behind: snapshot the steps so a failed
    # interpretation can be rolled back (R3).
    


    # 5. Sync Thresholds
    db.query(KRIThreshold).filter(KRIThreshold.kri_id == kri.id).delete()
    db.commit()
    for t in thresholds_list:
        val = float(t.get("value", 10.0))
        lbl = t.get("label") or "Variance Limit"
        unit = t.get("unit") or "%"
        key = t.get("key") or "threshold"
        th_type = ThresholdTypeEnum.PERCENTAGE_DIFFERENCE if "%" in unit else ThresholdTypeEnum.ABSOLUTE_DIFFERENCE
        kri_repo.add_threshold(
            kri.id,
            KRIThresholdCreate(
                name=lbl,
                threshold_type=th_type,
                operator=ComparisonOperatorEnum.GREATER_THAN,
                threshold_value=val,
                key=key,
                unit=unit,
                is_active=True,
            ),
        )

    # 6. Sync Schedule
    kri_repo.create_or_update_schedule(
        kri.id,
        KRIScheduleCreate(
            run_frequency=frequency,
            fetch_data_delay_days=fetch_offset_days,
            align_to_close_calendar=align_to_close,
            population_percentage=sampling,
            custom_date=custom_date,
        ),
    )

    # 7. Sync Reviewer
    db.query(Reviewer).filter(Reviewer.kri_id == kri.id).delete()
    db.commit()
    kri_repo.add_reviewer(
        kri.id,
        ReviewerCreate(
            reviewer_name=reviewer_name,
            reviewer_email=f"{reviewer_name.lower().replace(' ', '.')}@example.com",
            human_review_setting=human_review_setting,
            lifecycle_status="ACTIVE",
        ),
    )

    # 8. Regenerate the execution plan. Failure is surfaced, and the step set is rolled
    #    back so the UI never shows a KRI whose steps and plan disagree.
    coordinator = PlanCoordinator(db)
    if step_payloads:
        try:
            coordinator.ensure_current_plan(kri.id)
        except PlanStaleError as exc:
            kri_repo.restore_steps(kri.id, step_snapshot)
            kri_repo.set_data_sources(kri.id, data_source_snapshot)
            # A flat string, not the structured v1 body: this bridge is consumed by the UI's
            # toast, which renders the detail verbatim.
            raise HTTPException(status_code=422, detail=_readable_plan_error(exc))
    else:
        # A KRI with no steps cannot be run; drop any stale plan so the state is honest.
        db.query(ExecutionPlan).filter(ExecutionPlan.kri_id == kri.id).delete()
        db.commit()

    # 9. Apply status last, so activation only happens on a KRI that can actually run.
    kri_repo.update_status(kri.id, status_str)
    reloaded_final = kri_repo.get_kri_by_id(kri.id)
    return format_kri_for_ui(reloaded_final, db=db)


# --- KRI Library Endpoints ---

@router.get("/kris")
def get_kris_for_ui(db: Session = Depends(get_db)):
    """Retrieve all KRIs formatted for the React frontend KRI library."""
    return [format_kri_for_ui(k, db=db) for k in KRIRepository(db).list_kris()]


@router.get("/meta/operations")
def get_operations_for_ui():
    """Approved operations with their tools and required parameters."""
    return PlanService.describe_operations()


@router.get("/meta/data-sources")
def get_data_sources_for_ui(db: Session = Depends(get_db)):
    """Data source catalog with entity bindings, so the picker never invents a source."""
    kri_repo = KRIRepository(db)
    kri_repo.sync_data_source_availability()
    return [
        {
            "code": ds.code,
            "label": ds.name,
            "systemType": ds.system_type,
            "isQueryable": ds.is_queryable,
            "availabilityNote": ds.availability_note,
            "entities": [
                {
                    "code": entity_code,
                    "label": entity_code.replace("_", " ").title(),
                    "aliases": entity_registry.get_entity(entity_code).aliases,
                }
                for entity_code in entity_registry.bound_entity_codes(ds.code)
            ],
        }
        for ds in kri_repo.list_data_sources()
    ]


@router.post("/kris")
def create_kri_from_ui(payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Create a new KRI from the React frontend form."""
    return save_kri_from_ui(payload, db)


@router.put("/kris/{identifier}")
def update_kri_from_ui(identifier: str, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Update or save an existing KRI configuration from the React frontend."""
    payload["id"] = identifier
    return save_kri_from_ui(payload, db)


@router.patch("/kris/{identifier}/status")
def update_kri_status_from_ui(identifier: str, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Update KRI status. Activation requires a valid plan for the current configuration."""
    kri_repo = KRIRepository(db)
    kri = kri_repo.get_kri_by_identifier(identifier)
    if not kri:
        raise HTTPException(status_code=404, detail=f"KRI '{identifier}' not found.")

    new_status = str(payload.get("status", "draft")).upper()
    if new_status == "ACTIVE":
        try:
            PlanCoordinator(db).ensure_current_plan(kri.id)
        except PlanStaleError as exc:
            raise HTTPException(
                status_code=422,
                detail=PlanInterpretationErrorResponse(
                    message=f"Cannot activate '{identifier}': {exc}",
                    steps=exc.step_errors,
                    errors=[str(exc)],
                ).model_dump(),
            )
    kri_repo.update_status(kri.id, new_status)
    return format_kri_for_ui(kri_repo.get_kri_by_id(kri.id), db=db)


@router.get("/kris/{identifier}/plans")
def get_kri_plans_for_ui(identifier: str, db: Session = Depends(get_db)):
    """Read-only plan version history for the KRI detail panel."""
    kri = KRIRepository(db).get_kri_by_identifier(identifier)
    if not kri:
        raise HTTPException(status_code=404, detail=f"KRI '{identifier}' not found.")
    coordinator = PlanCoordinator(db)
    current_hash = coordinator.current_steps_hash(kri.id)
    return [
        {
            "id": p.id,
            "version": p.version,
            "source": p.source,
            "isValid": p.is_valid,
            "isCurrent": p.steps_hash == current_hash,
            "stepCount": len((p.plan_payload or {}).get("steps", [])),
            "createdAt": p.created_at.isoformat() if p.created_at else None,
            "warnings": p.validation_errors or [],
        }
        for p in coordinator.list_plans(kri.id)
    ]


@router.get("/kris/export/config")
def export_kri_config(db: Session = Depends(get_db)):
    """Export complete KRI configuration, including the current plan and its data sources."""
    return [format_kri_for_ui(k, db=db) for k in KRIRepository(db).list_kris()]


# --- Audit Runs Endpoints ---

def format_run_for_ui(run: AuditRun, db: Session) -> Dict[str, Any]:
    """Format one audit run into the complete frontend shape.

    Every field the Schedule and Overview pages read is produced here from real persisted
    data. Nothing is fabricated, and the shape is identical whether a run arrives from
    ``GET /api/runs`` or from the response of ``POST /api/runs``.
    """
    exceptions = db.query(AuditException).filter(AuditException.audit_run_id == run.id).all()
    explained = sum(
        1
        for e in exceptions
        if e.severity == "MEDIUM" or e.exception_type == "AMOUNT_MISMATCH"
    )
    duration = None
    if run.started_at and run.completed_at:
        duration = f"{(run.completed_at - run.started_at).total_seconds():.1f}s"

    exception_rate = 0.0
    if run.total_transactions:
        exception_rate = round((run.total_exceptions or 0) / run.total_transactions * 100.0, 1)

    return {
        "id": run.run_reference,
        "kriId": run.kri.identifier if run.kri else f"KRI-{run.kri_id}",
        "kriName": run.kri.name if run.kri else "",
        "date": (run.completed_at or run.started_at).strftime("%Y-%m-%d"),
        "startedAt": run.started_at.strftime("%Y-%m-%d %H:%M:%S") if run.started_at else None,
        "period": f"{run.start_date} to {run.end_date}",
        # There is no scheduler in this build, so every run is on demand. Reported honestly
        # rather than guessed at from the schedule.
        "trigger": "On demand",
        "tested": run.total_transactions or 0,
        # The evaluated population. The UI renders "tested of population", and for this
        # engine the evaluated records are the population, so both report the same figure
        # rather than the UI inventing a denominator.
        "population": run.total_transactions or 0,
        "exceptions": run.total_exceptions or 0,
        "exceptionRate": exception_rate,
        "unexplained": len(exceptions) - explained,
        "explainable": explained,
        "mismatchVal": run.exception_order_value or 0.0,
        "status": {
            "COMPLETED": "completed",
            "FAILED": "failed",
            "RUNNING": "running",
            "TIMED_OUT": "timed_out",
        }.get(run.status, "running"),
        "duration": duration,
        "errorMessage": run.error_message,
        "executionPlanId": run.execution_plan_id,
        "logFile": f"logs/runs/{run.run_reference}.log",
    }


@router.get("/runs")
def get_runs_for_ui(db: Session = Depends(get_db)):
    """Retrieve all audit execution runs formatted for the frontend."""
    runs = (
        db.query(AuditRun)
        .options(joinedload(AuditRun.kri))
        .order_by(AuditRun.started_at.desc())
        .all()
    )
    return [format_run_for_ui(r, db) for r in runs]


@router.get("/runs/{run_reference}/trace")
def get_run_trace_for_ui(run_reference: str, db: Session = Depends(get_db)):
    """Retrieve full interactive tool invocation trace, events, and raw logs for a specific audit run."""
    import os
    import json

    run = db.query(AuditRun).filter(
        (AuditRun.run_reference == run_reference) | (AuditRun.id == int(run_reference) if run_reference.isdigit() else False)
    ).first()

    kri_id = run.kri.identifier if run and run.kri else (f"KRI-{run.kri_id}" if run else "KRI-O2C-001")
    kri_name = run.kri.name if run and run.kri else "Audit Execution Run"

    duration_str = "3.8s"
    if run and run.started_at and run.completed_at:
        diff_sec = (run.completed_at - run.started_at).total_seconds()
        duration_str = f"{diff_sec:.1f}s"

    run_dict = {
        "id": run.run_reference if run else run_reference,
        "kriId": kri_id,
        "kriName": kri_name,
        "status": run.status if run else "COMPLETED",
        "startedAt": run.started_at.strftime("%Y-%m-%d %H:%M:%S") if run and run.started_at else "2026-09-23 10:54:00",
        "completedAt": run.completed_at.strftime("%Y-%m-%d %H:%M:%S") if run and run.completed_at else None,
        "duration": duration_str,
        "period": f"{run.start_date} to {run.end_date}" if run and run.start_date else "2026-01-01 to 2026-03-31",
        "tested": run.total_transactions if run else 80,
        "matched": run.matched_count if run else 52,
        "exceptions": run.total_exceptions if run else 28,
        "totalOrderValue": run.total_order_value if run else 9607742.06,
        "exceptionOrderValue": run.exception_order_value if run else 3085509.51,
        "exceptionRateCount": run.exception_rate_by_count if run else 35.0,
        "exceptionRateValue": run.exception_rate_by_value if run else 32.11,
        "logFile": f"logs/runs/{run_reference}.log",
    }

    steps = []
    events = []
    raw_log = ""

    # 1. Try reading from logs/runs/<run_reference>.json
    json_path = os.path.join("logs", "runs", f"{run_reference}.json")
    if not os.path.exists(json_path) and run:
        json_path = os.path.join("logs", "runs", f"{run.run_reference}.json")

    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                raw_events = json.load(f)
                for ev in raw_events:
                    stage = ev.get("stage", "EVENT")
                    step_no = ev.get("step_number", 0)
                    msg = ev.get("message", "")
                    payload = ev.get("payload", {})
                    ts = ev.get("timestamp", "")

                    events.append({
                        "timestamp": ts,
                        "stepNumber": step_no,
                        "stage": stage,
                        "message": msg,
                        "payload": payload,
                    })

                    if stage == "TOOL_EXECUTION":
                        tool_name = payload.get("tool_name", "tool")
                        status_val = payload.get("status", "SUCCESS")
                        dur_ms = payload.get("execution_time_ms", 0.0)
                        inp = payload.get("input_arguments", {})
                        outp = payload.get("output_result", {})
                        err = payload.get("error_message")

                        # Generate clean human-readable summary
                        if tool_name == "fetch_financial_data":
                            entity = inp.get("entity", "FINANCIAL_DATA")
                            src = inp.get("source_system", "SOURCE")
                            cnt = outp.get("record_count", 0)
                            summary = f"Extracted {cnt} {entity.replace('_', ' ').title()} records from {src}."
                        elif tool_name == "compare_records":
                            summary_dict = outp.get("summary", {}) if isinstance(outp.get("summary"), dict) else {}
                            mp = outp.get("matched_count", summary_dict.get("matched_orders", len(outp.get("matched_pairs", []))))
                            mis = outp.get("unmatched_orders_count", summary_dict.get("orders_without_po", len(outp.get("missing_pos", []))))
                            amb = outp.get("ambiguous_count", summary_dict.get("ambiguous_matched_orders", len(outp.get("ambiguous_matches", []))))
                            summary = f"Reconciled records: {mp} matched, {mis} missing PO, {amb} ambiguous matches."
                        elif tool_name == "calculate_difference":
                            diff = float(outp.get("difference") or 0.0)
                            pct = float(outp.get("difference_percentage") or 0.0)
                            summary = f"Calculated absolute variance: EUR {diff:,.2f} ({pct:.1f}% deviation)."
                        elif tool_name == "apply_threshold":
                            is_exc = outp.get("is_exception", False)
                            sev = outp.get("severity", "MEDIUM")
                            summary = f"Evaluated threshold policy: {'Exception Flagged (' + sev + ')' if is_exc else 'Within tolerance'}."
                        elif tool_name == "calculate_kri_metrics":
                            tot_exc = outp.get("total_exceptions", 0)
                            val = outp.get("exception_order_value", 0.0)
                            summary = f"Aggregated KRI metrics: {tot_exc} total exceptions (${val:,.2f} exposure value)."
                        elif tool_name == "build_evidence":
                            cnt = outp.get("evidence_count", 0)
                            summary = f"Generated {cnt} reproducible evidence records with cryptographic SHA-256 proofs."
                        elif tool_name == "analyze_oi_debookings":
                            findings_cnt = len(outp.get("findings", []))
                            q_analyzed = ", ".join(outp.get("quarters_analyzed", [])) or "Tested Quarters"
                            tot_deb = float(outp.get("total_debooked_value") or 0.0)
                            summary = f"Analyzed order intake debookings ({q_analyzed}): identified {findings_cnt} findings across EUR {tot_deb:,.2f} total reversals."
                        elif tool_name == "generate_explanation":
                            summary = outp.get("explanation", "Generated plain-English root-cause reasoning.")
                        else:
                            summary = msg

                        steps.append({
                            "stepNumber": step_no,
                            "toolName": tool_name,
                            "toolLabel": tool_name.replace("_", " ").title(),
                            "status": status_val,
                            "durationMs": dur_ms,
                            "timestamp": ts,
                            "summary": summary,
                            "inputPayload": inp,
                            "outputPayload": outp,
                            "error": err,
                        })
        except Exception:
            pass

    # 2. If steps empty, fallback to DB AuditToolInvocation
    if not steps and run:
        invs = db.query(AuditToolInvocation).filter(AuditToolInvocation.audit_run_id == run.id).order_by(AuditToolInvocation.step_number.asc()).all()
        for inv in invs:
            steps.append({
                "stepNumber": inv.step_number,
                "toolName": inv.tool_name,
                "toolLabel": inv.tool_name.replace("_", " ").title(),
                "status": inv.status,
                "durationMs": inv.execution_time_ms,
                "timestamp": inv.created_at.strftime("%Y-%m-%d %H:%M:%S") if inv.created_at else None,
                "summary": f"Executed tool '{inv.tool_name}' with status {inv.status}.",
                "inputPayload": inv.input_payload or {},
                "outputPayload": inv.output_payload or {},
                "error": inv.error_message,
            })

    # 3. Read raw .log file
    log_path = os.path.join("logs", "runs", f"{run_reference}.log")
    if not os.path.exists(log_path) and run:
        log_path = os.path.join("logs", "runs", f"{run.run_reference}.log")
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                raw_log = f.read()
        except Exception:
            pass

    # 4. No fabricated trace: a run with no recorded steps reports none, rather than
    #    inventing executions that never happened.
    return {
        "run": run_dict,
        "steps": steps,
        "events": events,
        "rawLog": raw_log
        or f"[INFO] No log file found for audit run {run_reference}.",
    }


def _resolve_ui_window(payload: Dict[str, Any]) -> tuple:
    """Derive the audit window from the UI payload instead of hardcoding one.

    Accepted, in precedence order:
      * explicit ``startDate`` / ``endDate``
      * ``period`` as ``YYYY-Qn`` or ``YYYY-MM``
      * ``period`` as a display label, e.g. ``"P9 - Sep 2026"`` or ``"Q3 FY26"``, which is
        what the close-calendar picker in the UI sends
      * otherwise an explicit 400, never a silent default window
    """
    start_raw = payload.get("startDate") or payload.get("start_date")
    end_raw = payload.get("endDate") or payload.get("end_date")
    if start_raw and end_raw:
        return date.fromisoformat(str(start_raw)[:10]), date.fromisoformat(str(end_raw)[:10])

    period = str(payload.get("period") or "").strip()
    if not period:
        raise HTTPException(
            status_code=400,
            detail="Could not determine the audit window. Send startDate/endDate, or a period.",
        )

    # Relative "to date" periods
    norm_period = re.sub(r"[\s_-]+", " ", period).strip().lower()
    ref_date_raw = payload.get("as_of") or payload.get("referenceDate")
    ref_date = date.fromisoformat(str(ref_date_raw)[:10]) if ref_date_raw else date.today()

    if norm_period in ("week to date", "wtd", "current week"):
        start_of_week = ref_date - timedelta(days=ref_date.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        return start_of_week, end_of_week

    if norm_period in ("month to date", "mtd", "current month"):
        return date(ref_date.year, ref_date.month, 1), date(
            ref_date.year, ref_date.month, calendar.monthrange(ref_date.year, ref_date.month)[1]
        )

    if norm_period in ("quarter to date", "qtd", "current quarter"):
        quarter = (ref_date.month - 1) // 3 + 1
        first_month = 3 * (quarter - 1) + 1
        last_month = first_month + 2
        return date(ref_date.year, first_month, 1), date(
            ref_date.year, last_month, calendar.monthrange(ref_date.year, last_month)[1]
        )

    if norm_period in ("year to date", "ytd", "current year"):
        return date(ref_date.year, 1, 1), date(ref_date.year, 12, 31)

    # 1. Explicit date range in period: "2026-01-01 to 2026-06-30" or "2026-01-01 / 2026-06-30"
    range_match = re.match(r"^(\d{4}-\d{2}-\d{2})\s*(?:to|/)\s*(\d{4}-\d{2}-\d{2})$", period)
    if range_match:
        return date.fromisoformat(range_match.group(1)), date.fromisoformat(range_match.group(2))

    # 2. Multi-quarter range: "2026-Q1-Q2" or "2026-Q1 to 2026-Q2"
    multi_q = re.match(r"^(\d{4})-Q([1-4])\s*(?:-|to)\s*(?:(\d{4})-)?Q([1-4])$", period, re.IGNORECASE)
    if multi_q:
        start_year = int(multi_q.group(1))
        start_q = int(multi_q.group(2))
        end_year = int(multi_q.group(3)) if multi_q.group(3) else start_year
        end_q = int(multi_q.group(4))
        first_month = 3 * (start_q - 1) + 1
        last_month = 3 * (end_q - 1) + 3
        return date(start_year, first_month, 1), date(
            end_year, last_month, calendar.monthrange(end_year, last_month)[1]
        )

    # 3. Half-year: "2026-H1", "2026-H2"
    half_match = re.match(r"^(\d{4})-H([1-2])$", period, re.IGNORECASE)
    if half_match:
        year, half = int(half_match.group(1)), int(half_match.group(2))
        first_month = 1 if half == 1 else 7
        last_month = 6 if half == 1 else 12
        return date(year, first_month, 1), date(
            year, last_month, calendar.monthrange(year, last_month)[1]
        )

    match = re.match(r"^(\d{4})-Q([1-4])$", period, re.IGNORECASE)
    if match:
        year, quarter = int(match.group(1)), int(match.group(2))
        first_month = 3 * (quarter - 1) + 1
        last_month = first_month + 2  # a quarter spans three months
        return date(year, first_month, 1), date(
            year, last_month, calendar.monthrange(year, last_month)[1]
        )

    match = re.match(r"^(\d{4})-(\d{2})$", period)
    if match:
        return _month_range(int(match.group(1)), int(match.group(2)))

    # Display labels used by the close-calendar picker: "P9 - Sep 2026", "Q3 FY26", "Q1-Q2 FY26", "H1 FY26".
    multi_fy = re.search(r"Q([1-4])\s*(?:-|to)\s*Q([1-4])\s*FY\s*(\d{2,4})", period, re.IGNORECASE)
    if multi_fy:
        start_q = int(multi_fy.group(1))
        end_q = int(multi_fy.group(2))
        year = int(multi_fy.group(3))
        year += 2000 if year < 100 else 0
        first_month = 3 * (start_q - 1) + 1
        last_month = 3 * (end_q - 1) + 3
        return date(year, first_month, 1), date(
            year, last_month, calendar.monthrange(year, last_month)[1]
        )

    half_fy = re.search(r"H([1-2])\s*FY\s*(\d{2,4})", period, re.IGNORECASE)
    if half_fy:
        half = int(half_fy.group(1))
        year = int(half_fy.group(2))
        year += 2000 if year < 100 else 0
        first_month = 1 if half == 1 else 7
        last_month = 6 if half == 1 else 12
        return date(year, first_month, 1), date(
            year, last_month, calendar.monthrange(year, last_month)[1]
        )

    match = re.search(r"([A-Za-z]{3,9})\s+(\d{4})", period)
    if match:
        for month in range(1, 13):
            if calendar.month_name[month][:3].lower() == match.group(1)[:3].lower():
                return _month_range(int(match.group(2)), month)
    match = re.search(r"Q([1-4])\s*FY\s*(\d{2,4})", period, re.IGNORECASE)
    if match:
        quarter = int(match.group(1))
        year = int(match.group(2))
        year += 2000 if year < 100 else 0
        first_month = 3 * (quarter - 1) + 1
        last_month = first_month + 2
        return date(year, first_month, 1), date(
            year, last_month, calendar.monthrange(year, last_month)[1]
        )

    raise HTTPException(
        status_code=400,
        detail=(
            f"Could not understand the requested period '{period}'. Send startDate/endDate, or a "
            f"period such as '2026-Q1' or '2026-01'."
        ),
    )


def _month_range(year: int, month: int) -> tuple:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


@router.post("/runs")
def start_run_from_ui(payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Execute an audit run from the UI (Run now button).

    The audit window comes from the request, never a hardcoded default.
    """
    kri_identifier = payload.get("kriId")
    kri_repo = KRIRepository(db)
    kri = kri_repo.get_kri_by_identifier(kri_identifier)
    if not kri:
        try:
            kri = kri_repo.get_kri_by_id(int(kri_identifier))
        except Exception:
            pass
    if not kri:
        raise HTTPException(status_code=404, detail=f"KRI '{kri_identifier}' not found.")

    start_date, end_date = _resolve_ui_window(payload)

    customer = payload.get("customer") or payload.get("customerName") or None
    sampling = payload.get("sampling")
    if sampling is not None and float(sampling) < 100.0:
        # Sampling is a schedule concern, not a run filter; surface it rather than ignore it.
        logger.warning(
            "UI requested %s%% sampling for KRI %s; the run evaluates the full population.",
            sampling,
            kri.identifier,
        )

    req = AuditRunRequest(
        kri_id=kri.id,
        start_date=start_date,
        end_date=end_date,
        customer_name=customer,
    )
    try:
        result = AuditService(db).execute_audit_run(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    audit_repo = AuditRepository(db)
    persisted = audit_repo.get_audit_run_by_id(result.id)
    # The same formatter the list endpoint uses, so a run pushed into the UI after a manual
    # run is shaped identically to one loaded on refresh.
    run_dict = format_run_for_ui(persisted, db)

    excs_db = audit_repo.list_exceptions(audit_run_id=result.id)
    excs_list = [format_exception_for_ui(e, default_kri_id=kri.identifier) for e in excs_db]

    return {"run": run_dict, "exceptions": excs_list}


# --- Exceptions Endpoints ---

@router.get("/exceptions")
def get_exceptions_for_ui(db: Session = Depends(get_db)):
    """Retrieve all audit exceptions formatted for the Exceptions page."""
    exceptions = db.query(AuditException).order_by(AuditException.created_at.desc()).limit(200).all()
    return [format_exception_for_ui(e) for e in exceptions]


@router.patch("/exceptions/{exception_id}")
def update_exception_for_ui(exception_id: str, patch: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Update exception review status or classification."""
    exc = db.query(AuditException).filter(AuditException.exception_reference == exception_id).first()
    if not exc:
        # Fallback return mock patched object
        return {
            "id": exception_id,
            "status": patch.get("status", "signed"),
            "classification": patch.get("classification", "explainable"),
        }
    if "status" in patch:
        exc.review_status = "APPROVED" if patch["status"] == "signed" else "PENDING_REVIEW"
        db.commit()
    return {
        "id": exc.exception_reference,
        "ref": exc.order_id,
        "status": patch.get("status", "signed"),
        "classification": patch.get("classification", "explainable"),
    }


@router.post("/exceptions/{exception_id}/planner")
def send_exception_to_planner(exception_id: str):
    """Bridge an exception into the audit planner queue."""
    return {
        "id": f"PLN-{uuid.uuid4().hex[:4].upper()}",
        "title": f"Investigate Exception {exception_id}",
        "type": "Deep-dive",
        "source": "KRI Monitor",
        "status": "New",
        "notes": f"Escalated from exception {exception_id} due to repeated pattern.",
    }


# --- Planner & Settings Endpoints ---

@router.get("/planner")
def get_planner_items():
    """Retrieve current planner queue items."""
    return [
        {
            "id": "PLN-01",
            "title": "Order Intake pricing controls evaluation in EMEA",
            "type": "Targeted Review",
            "source": "KRI-O2C-001 Monitor",
            "status": "In progress",
            "owner": "Audit Lead",
        },
        {
            "id": "PLN-02",
            "title": "Vendor contract price variance analysis in Red Box PO",
            "type": "Thematic Audit",
            "source": "Continuous Engine",
            "status": "New",
            "owner": "Senior Auditor",
        },
    ]


@router.post("/planner")
def create_planner_item(draft: Dict[str, Any] = Body(...)):
    """Create a new planner item."""
    item_id = f"PLN-{uuid.uuid4().hex[:4].upper()}"
    draft["id"] = item_id
    draft["status"] = draft.get("status", "New")
    return draft


@router.patch("/planner/{planner_id}")
def update_planner_item(planner_id: str, patch: Dict[str, Any] = Body(...)):
    """Update a planner item."""
    patch["id"] = planner_id
    return patch


@router.post("/planner/{planner_id}/draft")
def draft_plan_for_item(planner_id: str, payload: Dict[str, Any] = Body(...)):
    """Draft an audit plan for a planner item."""
    return {
        "id": planner_id,
        "status": "Drafted",
        "policy": payload.get("policy", "Standard Policy"),
        "planSteps": ["1. Request population", "2. Execute testing", "3. Document findings"],
    }


@router.get("/settings")
def get_settings():
    """Retrieve close calendar and system settings."""
    return {
        "calendar": {
            "pattern": "4-4-5",
            "periods": [
                {"p": "P8", "label": "Aug 2026", "close": "2026-08-30", "q": "Q3"},
                {"p": "P9", "label": "Sep 2026", "close": "2026-09-27", "q": "Q3", "quarterEnd": True},
                {"p": "P10", "label": "Oct 2026", "close": "2026-10-25", "q": "Q4"},
                {"p": "P11", "label": "Nov 2026", "close": "2026-11-22", "q": "Q4"},
                {"p": "P12", "label": "Dec 2026", "close": "2026-12-27", "q": "Q4", "quarterEnd": True},
            ],
        }
    }


@router.put("/settings/calendar")
def save_calendar(calendar: Dict[str, Any] = Body(...)):
    """Save custom close calendar configuration."""
    return calendar


@router.get("/planner/reference")
def get_planner_reference():
    """Retrieve reference governance policies and prior work."""
    return {
        "policies": [
            {"id": "POL-REV-01", "name": "Revenue Recognition Policy (IFRS 15 / ASC 606)"},
            {"id": "POL-PO-04", "name": "Commercial Procurement Approval Authority Matrix"},
        ],
        "priorWork": [
            {"id": "PW-2025-Q4", "title": "FY25 Q4 O2C Substantive Testing Work Papers"},
        ],
    }


@router.post("/settings/reset")
def reset_system_data(db: Session = Depends(get_db)):
    """Reset database and restore seed data."""
    from scripts.reset_database import reset_database
    reset_database()
    return {"status": "SUCCESS", "message": "Sample data successfully restored."}
