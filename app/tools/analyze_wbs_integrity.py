"""Tool 9: analyze_wbs_integrity.

Analyzes Work Breakdown Structure (WBS) usage accuracy and multi-opportunity integrity.
Reconciles five core commercial and financial streams:
  1. WBS Master Hierarchy (SAP ECC / PS)
  2. Commercial Opportunities & Contracts (SCRM)
  3. Order Intake Bookings (SAP ECC)
  4. Customer Purchase Orders (Red Box PO / SAP ECC)
  5. Recognized Revenue (YRA Report from SAP SD/FI)
  6. Actual Costs Incurred (YCA Report from SAP CO)

Detects 6 distinct Leading Risk conditions:
  1. MULTI_OPPORTUNITY_COMMINGLING (Critical): Single WBS mapped to > 1 commercial opportunity.
  2. MULTI_CUSTOMER_COMMINGLING (Critical): Single WBS containing transactions for multiple distinct customers.
  3. UNCOVERED_WBS (High): Active Order Intake or YRA revenue booked to a WBS without an approved Customer PO.
  4. REVENUE_OVER_RECOGNITION (High): YRA recognized revenue exceeds booked Order Intake beyond permitted tolerance.
  5. ORPHAN_COST_PARKING (High): Incurred YCA costs accumulating on a WBS with no active Order Intake or Opportunity.
  6. CROSS_BOOKING_MISMATCH (Medium): Discrepancy where an Opportunity or PO is split/mapped across disconnected WBS elements.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from app.schemas.tools import (
    AnalyzeWBSIntegrityInput,
    AnalyzeWBSIntegrityOutput,
    WBSRule,
    WBSIntegritySummary,
)
from app.services.execution_context import AuditExecutionContext


def default_wbs_rules() -> List[WBSRule]:
    """Default rule set for WBS usage integrity."""
    return [
        WBSRule(code="MULTI_OPPORTUNITY_COMMINGLING", severity="CRITICAL"),
        WBSRule(code="MULTI_CUSTOMER_COMMINGLING", severity="CRITICAL"),
        WBSRule(code="UNCOVERED_WBS", severity="HIGH"),
        WBSRule(code="REVENUE_OVER_RECOGNITION", severity="HIGH"),
        WBSRule(code="ORPHAN_COST_PARKING", severity="HIGH"),
        WBSRule(code="CROSS_BOOKING_MISMATCH", severity="MEDIUM"),
    ]


def _find_dataset(
    context: Optional[AuditExecutionContext],
    explicit_ref: Optional[str],
    entity_code: str,
) -> List[Dict[str, Any]]:
    """Resolve dataset by explicit reference or by scanning metadata for entity_code."""
    if context is None:
        return []

    if explicit_ref and explicit_ref in context.datasets:
        return context.datasets[explicit_ref]

    # Search by alias or entity_code in metadata
    for ref, meta in context.dataset_metadata.items():
        if (meta.get("entity_name") or "").upper() == entity_code.upper():
            return context.datasets.get(ref, [])

    return []


def analyze_wbs_integrity_handler(
    input_data: AnalyzeWBSIntegrityInput,
    context: Optional[AuditExecutionContext] = None,
) -> AnalyzeWBSIntegrityOutput:
    """Execute deterministic multi-stream WBS integrity and accuracy analysis."""
    # 1. Load the 6 datasets
    wbs_records = _find_dataset(context, input_data.wbs_dataset_reference, "WBS_MASTER")
    opp_records = _find_dataset(context, input_data.opportunities_dataset_reference, "SCRM_OPPORTUNITY")
    oi_records = _find_dataset(context, input_data.order_intake_dataset_reference, "ORDER_INTAKE")
    po_records = _find_dataset(context, input_data.customer_pos_dataset_reference, "PURCHASE_ORDER")
    yra_records = _find_dataset(context, input_data.yra_revenue_dataset_reference, "YRA_REVENUE")
    yca_records = _find_dataset(context, input_data.yca_cost_dataset_reference, "YCA_COST")

    rules = {r.code: r for r in (input_data.rules or default_wbs_rules())}
    tol_pct = input_data.revenue_tolerance_percentage
    tol_amt = input_data.revenue_tolerance_amount
    max_opps = input_data.max_opportunities_per_wbs

    # Lookup dictionaries
    wbs_master_by_code: Dict[str, Dict[str, Any]] = {
        r.get("wbs_code"): r for r in wbs_records if r.get("wbs_code")
    }
    opp_master_by_id: Dict[str, Dict[str, Any]] = {
        r.get("opportunity_id"): r for r in opp_records if r.get("opportunity_id")
    }

    # Aggregate by WBS element
    all_wbs_codes: Set[str] = set(wbs_master_by_code.keys())
    for r in oi_records:
        if r.get("wbs_element"):
            all_wbs_codes.add(r["wbs_element"])
    for r in po_records:
        if r.get("wbs_element"):
            all_wbs_codes.add(r["wbs_element"])
    for r in yra_records:
        if r.get("wbs_element"):
            all_wbs_codes.add(r["wbs_element"])
    for r in yca_records:
        if r.get("wbs_element"):
            all_wbs_codes.add(r["wbs_element"])

    # Aggregation structures per WBS
    wbs_opp_ids: Dict[str, Set[str]] = defaultdict(set)
    wbs_customers: Dict[str, Set[str]] = defaultdict(set)
    wbs_oi_amount: Dict[str, float] = defaultdict(float)
    wbs_po_amount: Dict[str, float] = defaultdict(float)
    wbs_yra_amount: Dict[str, float] = defaultdict(float)
    wbs_yca_amount: Dict[str, float] = defaultdict(float)
    wbs_po_numbers: Dict[str, Set[str]] = defaultdict(set)
    wbs_oi_ids: Dict[str, Set[str]] = defaultdict(set)

    # Opportunity cross-mapping: opp_id -> Set[wbs_code]
    opp_to_wbs_map: Dict[str, Set[str]] = defaultdict(set)

    # Populate from Order Intake
    for oi in oi_records:
        wbs = oi.get("wbs_element")
        if not wbs:
            continue
        amt = float(oi.get("order_amount") or 0.0)
        wbs_oi_amount[wbs] += amt
        if oi.get("order_id"):
            wbs_oi_ids[wbs].add(str(oi["order_id"]))
        if oi.get("opportunity_id"):
            wbs_opp_ids[wbs].add(str(oi["opportunity_id"]))
            opp_to_wbs_map[str(oi["opportunity_id"])].add(wbs)
        if oi.get("customer_name"):
            wbs_customers[wbs].add(str(oi["customer_name"]).strip())
        if oi.get("po_reference"):
            wbs_po_numbers[wbs].add(str(oi["po_reference"]).strip())

    # Populate from Customer Purchase Orders
    for po in po_records:
        wbs = po.get("wbs_element")
        if not wbs:
            continue
        amt = float(po.get("po_amount") or 0.0)
        wbs_po_amount[wbs] += amt
        if po.get("po_id"):
            wbs_po_numbers[wbs].add(str(po["po_id"]))
        if po.get("opportunity_id"):
            wbs_opp_ids[wbs].add(str(po["opportunity_id"]))
            opp_to_wbs_map[str(po["opportunity_id"])].add(wbs)
        if po.get("vendor_id") and not po.get("customer_name"):
            # in customer POs, vendor/customer might be in customer_name or vendor_id
            pass
        if po.get("customer_name"):
            wbs_customers[wbs].add(str(po["customer_name"]).strip())

    # Populate from YRA (Revenue)
    for yra in yra_records:
        wbs = yra.get("wbs_element")
        if not wbs:
            continue
        amt = float(yra.get("revenue_amount") or 0.0)
        wbs_yra_amount[wbs] += amt
        if yra.get("opportunity_id"):
            wbs_opp_ids[wbs].add(str(yra["opportunity_id"]))
            opp_to_wbs_map[str(yra["opportunity_id"])].add(wbs)
        if yra.get("customer_name"):
            wbs_customers[wbs].add(str(yra["customer_name"]).strip())

    # Populate from YCA (Cost)
    for yca in yca_records:
        wbs = yca.get("wbs_element")
        if not wbs:
            continue
        amt = float(yca.get("cost_amount") or 0.0)
        wbs_yca_amount[wbs] += amt

    # Populate from WBS Master
    for code, master in wbs_master_by_code.items():
        if master.get("customer_name"):
            wbs_customers[code].add(str(master["customer_name"]).strip())

    # Compile findings and summaries
    findings: List[Dict[str, Any]] = []
    summaries: List[WBSIntegritySummary] = []
    counts_by_code: Dict[str, int] = defaultdict(int)

    commingled_wbs_count = 0
    multi_customer_wbs_count = 0
    uncovered_po_wbs_count = 0
    revenue_over_recognized_count = 0
    orphan_cost_wbs_count = 0
    total_exposure = 0.0

    for wbs in sorted(all_wbs_codes):
        master = wbs_master_by_code.get(wbs, {})
        project_name = master.get("project_name") or f"Project for {wbs}"
        opps = sorted(wbs_opp_ids.get(wbs, set()))
        custs = sorted(wbs_customers.get(wbs, set()))

        oi_amt = round(wbs_oi_amount.get(wbs, 0.0), 2)
        po_amt = round(wbs_po_amount.get(wbs, 0.0), 2)
        yra_amt = round(wbs_yra_amount.get(wbs, 0.0), 2)
        yca_amt = round(wbs_yca_amount.get(wbs, 0.0), 2)

        wbs_findings: List[str] = []
        wbs_exposure = 0.0

        is_commingled = False
        is_multi_cust = False
        is_uncovered = False
        is_rev_exceeded = False
        is_orphan = False

        # Check 1: Multi-Opportunity Commingling (> max_opps)
        if len(opps) > max_opps:
            rule = rules.get("MULTI_OPPORTUNITY_COMMINGLING")
            if rule and rule.create_exception:
                is_commingled = True
                commingled_wbs_count += 1
                counts_by_code["MULTI_OPPORTUNITY_COMMINGLING"] += 1
                msg = (
                    f"WBS '{wbs}' has {len(opps)} distinct commercial opportunities recorded "
                    f"({', '.join(opps)}), exceeding the policy limit of {max_opps}. "
                    f"Order Intake=${oi_amt:,.2f}, YRA Revenue=${yra_amt:,.2f}."
                )
                wbs_findings.append(msg)
                exposure = max(oi_amt, yra_amt)
                wbs_exposure += exposure
                findings.append(
                    {
                        "code": "MULTI_OPPORTUNITY_COMMINGLING",
                        "severity": rule.severity,
                        "wbs_element": wbs,
                        "project_name": project_name,
                        "opportunity_count": len(opps),
                        "opportunity_ids": opps,
                        "customer_names": custs,
                        "order_intake_amount": oi_amt,
                        "yra_revenue_amount": yra_amt,
                        "exposure_amount": exposure,
                        "explanation": msg,
                    }
                )

        # Check 2: Multi-Customer Commingling
        if len(custs) > 1:
            rule = rules.get("MULTI_CUSTOMER_COMMINGLING")
            if rule and rule.create_exception:
                is_multi_cust = True
                multi_customer_wbs_count += 1
                counts_by_code["MULTI_CUSTOMER_COMMINGLING"] += 1
                msg = (
                    f"WBS '{wbs}' commingles {len(custs)} distinct customers ({', '.join(custs)}) "
                    f"under a single WBS element, posing critical cross-customer financial risk."
                )
                wbs_findings.append(msg)
                exposure = max(oi_amt, yra_amt)
                wbs_exposure += exposure
                findings.append(
                    {
                        "code": "MULTI_CUSTOMER_COMMINGLING",
                        "severity": rule.severity,
                        "wbs_element": wbs,
                        "project_name": project_name,
                        "customer_names": custs,
                        "order_intake_amount": oi_amt,
                        "yra_revenue_amount": yra_amt,
                        "exposure_amount": exposure,
                        "explanation": msg,
                    }
                )

        # Check 3: Uncovered WBS (Active OI or Revenue with No Customer PO)
        if (oi_amt > 0.0 or yra_amt > 0.0) and po_amt <= 0.0 and not wbs_po_numbers.get(wbs):
            rule = rules.get("UNCOVERED_WBS")
            if rule and rule.create_exception:
                is_uncovered = True
                uncovered_po_wbs_count += 1
                counts_by_code["UNCOVERED_WBS"] += 1
                msg = (
                    f"WBS '{wbs}' has active commercial recognition (OI=${oi_amt:,.2f}, "
                    f"YRA Revenue=${yra_amt:,.2f}) but has no approved Customer Purchase Order recorded."
                )
                wbs_findings.append(msg)
                exposure = max(oi_amt, yra_amt)
                wbs_exposure += exposure
                findings.append(
                    {
                        "code": "UNCOVERED_WBS",
                        "severity": rule.severity,
                        "wbs_element": wbs,
                        "project_name": project_name,
                        "order_intake_amount": oi_amt,
                        "customer_po_amount": po_amt,
                        "yra_revenue_amount": yra_amt,
                        "exposure_amount": exposure,
                        "explanation": msg,
                    }
                )

        # Check 4: Revenue Over-Recognition with Tolerance (YRA > OI + tolerance)
        permitted_ceiling = oi_amt * (1.0 + tol_pct) + tol_amt
        if yra_amt > permitted_ceiling and yra_amt > 0.0:
            rule = rules.get("REVENUE_OVER_RECOGNITION")
            if rule and rule.create_exception:
                is_rev_exceeded = True
                revenue_over_recognized_count += 1
                counts_by_code["REVENUE_OVER_RECOGNITION"] += 1
                excess = round(yra_amt - oi_amt, 2)
                pct_diff = round(((yra_amt - oi_amt) / oi_amt * 100.0), 1) if oi_amt > 0 else 100.0
                msg = (
                    f"WBS '{wbs}' recognized YRA Revenue of ${yra_amt:,.2f}, which exceeds booked "
                    f"Order Intake of ${oi_amt:,.2f} by ${excess:,.2f} ({pct_diff}%), "
                    f"surpassing the permitted tolerance buffer ({tol_pct * 100:.1f}% + ${tol_amt:,.0f})."
                )
                wbs_findings.append(msg)
                wbs_exposure += excess
                findings.append(
                    {
                        "code": "REVENUE_OVER_RECOGNITION",
                        "severity": rule.severity,
                        "wbs_element": wbs,
                        "project_name": project_name,
                        "order_intake_amount": oi_amt,
                        "yra_revenue_amount": yra_amt,
                        "excess_amount": excess,
                        "percentage_difference": pct_diff,
                        "exposure_amount": excess,
                        "explanation": msg,
                    }
                )

        # Check 5: Orphan Cost Parking (YCA Cost Incurred with Zero OI & Zero Opportunities)
        if yca_amt > 0.0 and oi_amt <= 0.0 and len(opps) == 0:
            rule = rules.get("ORPHAN_COST_PARKING")
            if rule and rule.create_exception:
                is_orphan = True
                orphan_cost_wbs_count += 1
                counts_by_code["ORPHAN_COST_PARKING"] += 1
                msg = (
                    f"WBS '{wbs}' has incurred actual project costs (YCA) of ${yca_amt:,.2f} "
                    f"with zero booked Order Intake and no commercial opportunity linked (cost parking risk)."
                )
                wbs_findings.append(msg)
                wbs_exposure += yca_amt
                findings.append(
                    {
                        "code": "ORPHAN_COST_PARKING",
                        "severity": rule.severity,
                        "wbs_element": wbs,
                        "project_name": project_name,
                        "yca_cost_amount": yca_amt,
                        "exposure_amount": yca_amt,
                        "explanation": msg,
                    }
                )

        total_exposure += wbs_exposure
        summaries.append(
            WBSIntegritySummary(
                wbs_element=wbs,
                project_name=project_name,
                opportunity_ids=opps,
                customer_names=custs,
                order_intake_amount=oi_amt,
                customer_po_amount=po_amt,
                yra_revenue_amount=yra_amt,
                yca_cost_amount=yca_amt,
                is_commingled=is_commingled,
                is_multi_customer=is_multi_cust,
                is_uncovered_po=is_uncovered,
                is_revenue_exceeded=is_rev_exceeded,
                is_orphan_cost=is_orphan,
                exposure_amount=round(wbs_exposure, 2),
                findings=wbs_findings,
            )
        )

    # Check 6: Cross-Booking Mismatches (Single Opportunity booked across multiple WBS elements)
    rule_cross = rules.get("CROSS_BOOKING_MISMATCH")
    if rule_cross and rule_cross.create_exception:
        for opp_id, mapped_wbs in sorted(opp_to_wbs_map.items()):
            if len(mapped_wbs) > 1:
                counts_by_code["CROSS_BOOKING_MISMATCH"] += 1
                wbs_str = ", ".join(sorted(mapped_wbs))
                msg = (
                    f"Commercial Opportunity '{opp_id}' is split across {len(mapped_wbs)} "
                    f"different WBS elements ({wbs_str}), creating fragmentation risk."
                )
                findings.append(
                    {
                        "code": "CROSS_BOOKING_MISMATCH",
                        "severity": rule_cross.severity,
                        "opportunity_id": opp_id,
                        "wbs_elements": sorted(mapped_wbs),
                        "exposure_amount": 0.0,
                        "explanation": msg,
                    }
                )

    total_exposure = round(total_exposure, 2)
    message = (
        f"Analyzed {len(all_wbs_codes)} WBS elements across 5 streams: "
        f"{commingled_wbs_count} commingled multi-opp, {multi_customer_wbs_count} multi-customer, "
        f"{uncovered_po_wbs_count} uncovered POs, {revenue_over_recognized_count} revenue over-recognized, "
        f"{orphan_cost_wbs_count} orphan cost parking; total exposure=${total_exposure:,.2f}."
    )

    return AnalyzeWBSIntegrityOutput(
        tool_name="analyze_wbs_integrity",
        status="SUCCESS",
        audit_run_reference=input_data.audit_run_reference,
        wbs_elements_analyzed=len(all_wbs_codes),
        commingled_wbs_count=commingled_wbs_count,
        multi_customer_wbs_count=multi_customer_wbs_count,
        uncovered_po_wbs_count=uncovered_po_wbs_count,
        revenue_over_recognized_count=revenue_over_recognized_count,
        orphan_cost_wbs_count=orphan_cost_wbs_count,
        total_exposure_amount=total_exposure,
        wbs_summaries=summaries,
        findings=findings,
        counts_by_code=dict(counts_by_code),
        message=message,
    )
