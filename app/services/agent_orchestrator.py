"""Agent Execution Orchestrator.

Thin dispatcher that hands control to the deterministic plan executor. The plan is the
execution contract (R1): the LLM no longer dispatches tools.

``EXECUTION_MODE=agent`` retains the legacy free-form LLM loop for side-by-side comparison
during rollout; it is not the default path.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.tools.registry import registry
from app.services.llm_provider import LLMProvider, get_llm_provider
from app.services.execution_context import AuditExecutionContext
from app.services.plan_executor import PlanExecutor
from app.services.prompt_loader import load_agent_system_prompt

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Runs an audit from a stored execution plan, or the legacy LLM loop when configured."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm_provider = llm_provider or get_llm_provider()
        self.executor = PlanExecutor(llm_provider=self.llm_provider)

    def run_audit(self, context: AuditExecutionContext, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute the audit for the given context."""
        plan = plan or context.plan or {}

        if not settings.is_plan_executor_enabled():
            logger.warning(
                "EXECUTION_MODE=%s: using the legacy LLM dispatch loop. Set EXECUTION_MODE=plan "
                "to execute the interpreted execution plan.",
                settings.execution_mode,
            )
            return self._run_legacy_loop(context)

        if not plan:
            raise RuntimeError(
                "No execution plan is bound to this run. The plan is the execution contract "
                "and must be generated before the KRI can be run."
            )

        kri = context.kri
        if not [s for s in (kri.test_steps or []) if s.is_active]:
            raise RuntimeError(
                f"KRI '{kri.identifier}' has no active test steps. Define at least one step "
                f"before running an audit."
            )

        logger.info(
            "Executing plan v%s (%s steps) for KRI %s",
            plan.get("version"),
            len(plan.get("steps", [])),
            kri.identifier,
        )
        result = self.executor.execute(plan, context)
        if context.calculated_metrics and context.run_logger:
            context.run_logger.log_metrics(context.calculated_metrics)
        if context.run_logger:
            context.run_logger.log_evidence_built(len(context.candidate_exceptions))
        result["prepared_notes"] = context.prepared_notes
        return result

    # -- legacy path ----------------------------------------------------------

    def _run_legacy_loop(self, context: AuditExecutionContext) -> Dict[str, Any]:
        """Original free-form LLM tool-dispatch loop, retained only for rollout comparison."""
        kri = context.kri
        tools_definitions = registry.get_openai_tool_definitions()

        steps_text = "\n".join(
            f"{s.step_number}. {s.title}: {s.instruction}"
            for s in sorted(kri.test_steps, key=lambda s: s.step_number)
            if s.is_active
        )
        if not steps_text:
            raise RuntimeError(
                f"KRI '{kri.identifier}' has no active test steps; refusing to fall back to a "
                f"default workflow."
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
            {"role": "system", "content": load_agent_system_prompt()},
            {"role": "user", "content": user_message},
        ]

        step_counter = 0
        max_calls = settings.max_tool_calls_per_run
        threshold_val = context.get_active_threshold_value()

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
                raise RuntimeError(f"LLM Provider execution error: {e}") from e

            tool_calls = response.get("tool_calls")
            messages.append({"role": "assistant", "content": response.get("content"), "tool_calls": tool_calls})
            if not tool_calls:
                break

            for tc in tool_calls:
                func_data = tc.get("function", {})
                tool_name = func_data.get("name")
                raw_args = func_data.get("arguments", "{}")
                start = time.time()
                status, output, error = "SUCCESS", {}, None
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception as parse_err:
                    args, status, error = {}, "ERROR", f"Failed to parse arguments: {parse_err}"

                if status == "SUCCESS":
                    if not registry.is_registered(tool_name):
                        status, error = "ERROR", f"Tool '{tool_name}' is not registered."
                    else:
                        try:
                            output = registry.get_tool(tool_name).execute(args, context).model_dump(mode="json")
                        except Exception as tool_err:
                            status, error = "ERROR", str(tool_err)
                            output = {"status": "ERROR", "error": error}

                context.tool_invocations_count += 1
                elapsed = round((time.time() - start) * 1000.0, 2)
                context.audit_repo.record_tool_invocation(
                    audit_run_id=context.audit_run_id,
                    step_number=context.tool_invocations_count,
                    tool_name=tool_name,
                    input_payload=args,
                    output_payload=output,
                    status=status,
                    error_message=error,
                    execution_time_ms=elapsed,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": tool_name,
                        "content": json.dumps(output),
                    }
                )

        executor = PlanExecutor()
        if context.plan and context.plan.get("steps"):
            # The legacy loop only performed the LLM dispatch; the deterministic executor
            # still owns per-record maths, exception derivation and persistence.
            return executor.execute(context.plan, context)

        return {
            "status": "COMPLETED",
            "audit_run_reference": context.run_reference,
            "metrics": context.calculated_metrics,
            "total_exceptions": len(context.candidate_exceptions),
            "tool_invocations_count": context.tool_invocations_count,
        }
