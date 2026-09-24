"""Agent Execution Orchestrator Module.

Manages the autonomous LLM function-calling loop, validates tool calls, enforces execution limits,
records invocation traces, processes intermediate audit steps, and guarantees mandatory audit stages.
"""

import json
import logging
import time
import uuid
from typing import Dict, Any, List, Optional
from app.core.config import settings
from app.tools.registry import registry
from app.services.llm_provider import LLMProvider, get_llm_provider
from app.services.execution_context import AuditExecutionContext
from app.schemas.tools import (
    CalculateDifferenceInput,
    ApplyThresholdInput,
    GenerateExplanationInput,
    CalculateKRIMetricsInput,
    BuildEvidenceInput,
)
from app.tools.calculate_difference import calculate_difference_handler
from app.tools.apply_threshold import apply_threshold_handler
from app.tools.generate_explanation import generate_explanation_handler
from app.tools.calculate_kri_metrics import calculate_kri_metrics_handler
from app.tools.build_evidence import build_evidence_handler

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Orchestrates LLM function calling and deterministic tool executions for an audit run."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm_provider = llm_provider or get_llm_provider()

    def run_audit(self, context: AuditExecutionContext) -> Dict[str, Any]:
        """Execute the agentic audit loop for the given audit execution context."""
        kri = context.kri
        tools_definitions = registry.get_openai_tool_definitions()

        # Build initial prompt messages
        system_prompt = (
            "You are the Continuous Internal Audit Agent Orchestrator. "
            "Your objective is to evaluate customer Order Intake transactions against Purchase Orders, "
            "identify variances and missing PO exceptions, compute aggregate KRI metrics, and build evidence."
        )

        steps_text = "\n".join(
            f"{s.step_number}. {s.title}: {s.instruction}"
            for s in sorted(kri.test_steps, key=lambda s: s.step_number)
            if s.is_active
        )
        if not steps_text:
            steps_text = (
                "1. Extract order intake records from SAP ECC.\n"
                "2. Extract purchase orders from Red Box PO.\n"
                "3. Match order intake with purchase orders.\n"
                "4. Compare amounts and apply threshold.\n"
                "5. Calculate aggregate KRI metrics and build evidence."
            )

        user_message = (
            f"KRI Identifier: {kri.identifier}\n"
            f"KRI Name: {kri.name}\n"
            f"Audit Window: {context.start_date} to {context.end_date}\n"
            f"Customer Filter: {context.customer_filter or 'ALL'}\n\n"
            f"Configured Test Steps:\n{steps_text}\n\n"
            f"Execute the audit workflow using the registered tools."
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        step_counter = 0
        max_calls = settings.max_tool_calls_per_run
        threshold_val = context.get_active_threshold_value()

        logger.info("Starting audit run %s for KRI %s", context.run_reference, kri.identifier)

        while step_counter < max_calls:
            step_counter += 1

            if context.run_logger:
                context.run_logger.log_event(
                    stage="AGENT_ITERATION_START",
                    message=f"Starting agent iteration #{step_counter}",
                    step_number=step_counter,
                    payload={"history_message_count": len(messages)},
                )

            try:
                response = self.llm_provider.create_response(messages=messages, tools=tools_definitions)
            except Exception as e:
                logger.error("LLM Provider call error: %s", e)
                if context.run_logger:
                    context.run_logger.log_event(
                        stage="LLM_ERROR",
                        message=f"LLM Provider invocation failed: {e}",
                        step_number=step_counter,
                        payload={"error": str(e)},
                    )
                context.audit_repo.record_tool_invocation(
                    audit_run_id=context.audit_run_id,
                    step_number=step_counter,
                    tool_name="LLM_PROVIDER",
                    input_payload={"message_count": len(messages)},
                    output_payload={},
                    status="ERROR",
                    error_message=str(e),
                )
                raise RuntimeError(f"LLM Provider execution error: {e}") from e

            tool_calls = response.get("tool_calls")
            assistant_content = response.get("content")

            if context.run_logger:
                context.run_logger.log_event(
                    stage="LLM_RESPONSE",
                    message=f"LLM Response received: {assistant_content or f'Requested {len(tool_calls)} tool call(s)'}",
                    step_number=step_counter,
                    payload={"content": assistant_content, "tool_calls": tool_calls},
                )

            # Append assistant turn to messages
            messages.append({
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls,
            })

            # If LLM didn't request any tool call, the agent has finished
            if not tool_calls:
                logger.info("Agent concluded audit workflow.")
                if context.run_logger:
                    context.run_logger.log_event(
                        stage="AGENT_CONCLUDED",
                        message="LLM concluded workflow without further tool calls.",
                        step_number=step_counter,
                    )
                break

            # Execute each requested tool call
            for tc in tool_calls:
                call_id = tc.get("id")
                func_data = tc.get("function", {})
                tool_name = func_data.get("name")
                raw_args = func_data.get("arguments", "{}")

                start_tool_time = time.time()
                error_msg = None
                output_dict: Dict[str, Any] = {}
                status = "SUCCESS"

                try:
                    args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception as parse_err:
                    error_msg = f"Failed to parse tool arguments: {parse_err}"
                    status = "ERROR"
                    args_dict = {}

                if status == "SUCCESS":
                    if not registry.is_registered(tool_name):
                        error_msg = f"Tool '{tool_name}' is not in the registered allowlist."
                        status = "ERROR"
                    else:
                        tool = registry.get_tool(tool_name)
                        try:
                            # Execute registered tool with Pydantic validation
                            tool_output = tool.execute(args_dict, context)
                            output_dict = tool_output.model_dump(mode="json")

                            # Post-processing: If compare_records was called, evaluate candidate exceptions
                            if tool_name == "compare_records":
                                comp_ref = output_dict.get("comparison_reference")
                                comp_data = context.get_comparison(comp_ref)
                                if comp_data:
                                    self._process_comparison_candidates(comp_data, context, threshold_val)

                        except Exception as tool_err:
                            logger.exception("Error executing tool %s: %s", tool_name, tool_err)
                            error_msg = str(tool_err)
                            status = "ERROR"
                            output_dict = {"status": "ERROR", "error": error_msg}

                exec_time_ms = round((time.time() - start_tool_time) * 1000.0, 2)
                context.tool_invocations_count += 1

                # Record tool invocation trace in database
                context.audit_repo.record_tool_invocation(
                    audit_run_id=context.audit_run_id,
                    step_number=context.tool_invocations_count,
                    tool_name=tool_name,
                    input_payload=args_dict,
                    output_payload=output_dict,
                    status=status,
                    error_message=error_msg,
                    execution_time_ms=exec_time_ms,
                )

                # Record in run logger
                if context.run_logger:
                    context.run_logger.log_tool_call(
                        step_number=context.tool_invocations_count,
                        tool_name=tool_name,
                        input_payload=args_dict,
                        output_payload=output_dict,
                        status=status,
                        execution_time_ms=exec_time_ms,
                        error_message=error_msg,
                    )

                # Feed tool result back to LLM context
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": tool_name,
                    "content": json.dumps(output_dict),
                })

        # Mandatory Stage Enforcement: Calculate metrics if not done
        if not context.calculated_metrics:
            logger.info("Enforcing mandatory calculate_kri_metrics stage.")
            metrics_out = calculate_kri_metrics_handler(
                CalculateKRIMetricsInput(audit_run_reference=context.run_reference),
                context,
            )
            context.calculated_metrics = metrics_out.model_dump(mode="json")

        if context.run_logger and context.calculated_metrics:
            context.run_logger.log_metrics(context.calculated_metrics)

        # Mandatory Stage Enforcement: Build evidence if not done
        if not context.generated_evidence and context.candidate_exceptions:
            logger.info("Enforcing mandatory build_evidence stage.")
            build_evidence_handler(
                BuildEvidenceInput(audit_run_reference=context.run_reference),
                context,
            )

        if context.run_logger:
            context.run_logger.log_evidence_built(len(context.candidate_exceptions))

        # Persist all exceptions to the database
        self._persist_exceptions(context)

        return {
            "status": "COMPLETED",
            "audit_run_reference": context.run_reference,
            "metrics": context.calculated_metrics,
            "total_exceptions": len(context.candidate_exceptions),
            "tool_invocations_count": context.tool_invocations_count,
        }

    def _process_comparison_candidates(
        self,
        comp_data: Dict[str, Any],
        context: AuditExecutionContext,
        threshold_val: float,
    ) -> None:
        """Evaluate matched pairs and missing POs through deterministic calculation and threshold tools."""
        # 1. Process Missing POs
        for item in comp_data.get("missing_pos", []):
            order = item.get("order", {})
            order_id = order.get("order_id")
            order_amt = float(order.get("order_amount", 0.0))

            diff_out = calculate_difference_handler(
                CalculateDifferenceInput(left_value=order_amt, right_value=None),
                context,
            )
            th_out = apply_threshold_handler(
                ApplyThresholdInput(
                    value=None,
                    threshold_value=threshold_val,
                    is_missing_counterpart=True,
                ),
                context,
            )
            exp_out = generate_explanation_handler(
                GenerateExplanationInput(
                    exception_type="MISSING_PO",
                    calculation={"order_amount": order_amt, "currency": order.get("currency", "USD")},
                ),
                context,
            )

            exc_ref = f"exc_{uuid.uuid4().hex[:8]}"
            exc_item = {
                "exception_reference": exc_ref,
                "order_id": order_id,
                "po_id": None,
                "exception_type": "MISSING_PO",
                "severity": th_out.severity,
                "reason_code": th_out.reason_code,
                "order_amount": order_amt,
                "po_amount": None,
                "difference_amount": diff_out.difference,
                "difference_percentage": diff_out.difference_percentage,
                "threshold_value": threshold_val,
                "explanation": exp_out.explanation,
                "order_record": order,
                "po_record": None,
                "calculation_details": diff_out.model_dump(mode="json"),
                "threshold_details": th_out.model_dump(mode="json"),
            }
            context.add_candidate_exception(exc_item)
            if context.run_logger:
                context.run_logger.log_exception_detected(exc_item)

        # 2. Process Matched Pairs for Variance Threshold
        for pair in comp_data.get("matched_pairs", []):
            order = pair.get("order", {})
            po = pair.get("po", {})
            order_id = order.get("order_id")
            po_id = po.get("po_id")
            order_amt = float(order.get("order_amount", 0.0))
            po_amt = float(po.get("po_amount", 0.0))

            diff_out = calculate_difference_handler(
                CalculateDifferenceInput(left_value=order_amt, right_value=po_amt),
                context,
            )
            th_out = apply_threshold_handler(
                ApplyThresholdInput(
                    value=diff_out.difference_percentage,
                    threshold_value=threshold_val,
                    is_missing_counterpart=False,
                ),
                context,
            )

            if th_out.is_exception:
                exp_out = generate_explanation_handler(
                    GenerateExplanationInput(
                        exception_type="AMOUNT_MISMATCH",
                        calculation={
                            "order_amount": order_amt,
                            "po_amount": po_amt,
                            "difference_percentage": diff_out.difference_percentage,
                            "difference_amount": diff_out.absolute_difference,
                            "threshold": threshold_val,
                        },
                    ),
                    context,
                )

                exc_ref = f"exc_{uuid.uuid4().hex[:8]}"
                exc_item = {
                    "exception_reference": exc_ref,
                    "order_id": order_id,
                    "po_id": po_id,
                    "exception_type": "AMOUNT_MISMATCH",
                    "severity": th_out.severity,
                    "reason_code": th_out.reason_code,
                    "order_amount": order_amt,
                    "po_amount": po_amt,
                    "difference_amount": diff_out.difference,
                    "difference_percentage": diff_out.difference_percentage,
                    "threshold_value": threshold_val,
                    "explanation": exp_out.explanation,
                    "order_record": order,
                    "po_record": po,
                    "calculation_details": diff_out.model_dump(mode="json"),
                    "threshold_details": th_out.model_dump(mode="json"),
                }
                context.add_candidate_exception(exc_item)
                if context.run_logger:
                    context.run_logger.log_exception_detected(exc_item)

        # 3. Process Ambiguous Matches
        for item in comp_data.get("ambiguous_matches", []):
            order = item.get("order", {})
            order_id = order.get("order_id")
            order_amt = float(order.get("order_amount", 0.0))
            matching_pos = item.get("matching_pos", [])

            exp_out = generate_explanation_handler(
                GenerateExplanationInput(
                    exception_type="AMBIGUOUS_MATCH",
                    calculation={"matching_pos_count": len(matching_pos)},
                ),
                context,
            )

            exc_ref = f"exc_{uuid.uuid4().hex[:8]}"
            exc_item = {
                "exception_reference": exc_ref,
                "order_id": order_id,
                "po_id": None,
                "exception_type": "AMBIGUOUS_MATCH",
                "severity": "HIGH",
                "reason_code": "AMBIGUOUS_MATCH",
                "order_amount": order_amt,
                "po_amount": None,
                "difference_amount": 0.0,
                "difference_percentage": 0.0,
                "threshold_value": threshold_val,
                "explanation": exp_out.explanation,
                "order_record": order,
                "po_record": {"matching_pos": matching_pos},
                "calculation_details": {"ambiguous_pos_count": len(matching_pos)},
                "threshold_details": {"threshold_value": threshold_val},
            }
            context.add_candidate_exception(exc_item)
            if context.run_logger:
                context.run_logger.log_exception_detected(exc_item)

    def _persist_exceptions(self, context: AuditExecutionContext) -> None:
        """Persist all candidate exceptions into the database audit_exceptions table."""
        for exc in context.candidate_exceptions:
            evidence_data = context.generated_evidence.get(exc.get("exception_reference"))
            ev_ref = evidence_data.get("evidence_reference") if evidence_data else None

            db_exc = context.audit_repo.create_exception(
                audit_run_id=context.audit_run_id,
                order_id=exc.get("order_id"),
                po_id=exc.get("po_id"),
                exception_type=exc.get("exception_type"),
                severity=exc.get("severity"),
                reason_code=exc.get("reason_code"),
                order_amount=exc.get("order_amount"),
                po_amount=exc.get("po_amount"),
                difference_amount=exc.get("difference_amount"),
                difference_percentage=exc.get("difference_percentage"),
                threshold_value=exc.get("threshold_value"),
                explanation=exc.get("explanation"),
                evidence_reference=ev_ref,
                exception_reference=exc.get("exception_reference"),
            )
