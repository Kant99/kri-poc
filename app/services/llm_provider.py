"""LLM Provider Abstraction Module.

Defines the LLMProvider Protocol and implements:
1. AzureOpenAIProvider for live Azure OpenAI function-calling deployments.
2. MockLLMProvider for deterministic offline execution, local testing, and automated test suites.
"""

import json
import logging
import re
import time
from typing import Protocol, List, Dict, Any, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

#: Operations that are expanded per record by the executor rather than dispatched once.
_PER_RECORD = {
    "COMPARE_AMOUNTS",
    "CALCULATE_DIFFERENCE",
    "APPLY_THRESHOLD",
    "EVALUATE_THRESHOLD",
}


class LLMProvider(Protocol):
    """Protocol for LLM orchestration providers."""

    def create_response(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        plan: Optional[Dict[str, Any]] = None,
        completed_tools: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create response from LLM, returning tool calls or final message content."""
        ...

    def classify_steps(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Classify test steps into approved operations. Returns raw JSON text."""
        ...


class AzureOpenAIProvider:
    """Live Azure OpenAI client wrapper with retry handling and telemetry."""

    def __init__(self):
        import httpx
        from openai import AzureOpenAI

        if not settings.is_azure_configured():
            raise ValueError(
                "Azure OpenAI credentials are not configured or invalid. "
                "Check AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, and AZURE_OPENAI_DEPLOYMENT_NAME in .env."
            )

        endpoint = settings.get_normalized_azure_endpoint()
        http_client = httpx.Client(verify=settings.ssl_verify)

        self.client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            timeout=float(settings.llm_timeout_seconds),
            http_client=http_client,
        )
        self.deployment_name = settings.azure_openai_deployment_name
        self.temperature = settings.llm_temperature

    def create_response(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        plan: Optional[Dict[str, Any]] = None,
        completed_tools: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        start_time = time.time()
        kwargs: Dict[str, Any] = {
            "model": self.deployment_name,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(**kwargs)
                latency = time.time() - start_time
                choice = response.choices[0]
                message = choice.message

                tool_calls_data = []
                if message.tool_calls:
                    for tc in message.tool_calls:
                        tool_calls_data.append({
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        })

                return {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": tool_calls_data if tool_calls_data else None,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                        "total_tokens": response.usage.total_tokens if response.usage else 0,
                    },
                    "latency_seconds": round(latency, 3),
                }
            except Exception as e:
                last_error = e
                logger.warning("Azure OpenAI API call failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
                time.sleep(1.0 * (attempt + 1))

        raise RuntimeError(f"Azure OpenAI call failed after {max_retries} attempts: {last_error}") from last_error

    def classify_steps(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Classify administrator test steps into approved operations."""
        from app.services.prompt_loader import load_planner_prompt

        response = self.create_response(
            messages=[
                {"role": "system", "content": load_planner_prompt()},
                {
                    "role": "user",
                    "content": (
                        "Classify each test step into exactly one approved operation.\n"
                        "Return JSON: "
                        '{"classifications": [{"id": <step id>, "operation": "...", '
                        '"data_source_code": null, "entity_code": null, "match_field": null, '
                        '"rationale": "..."}]}\n\n'
                        + json.dumps(payload, indent=2, default=str)
                    ),
                },
            ],
            tools=None,
        )
        content = response.get("content") or ""
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


class MockLLMProvider:
    """Deterministic offline provider.

    When a plan is supplied, this walks the plan in order and emits exactly the tool calls the
    plan declares, so offline mode is behaviourally identical to production. Without a plan
    it degrades to answering text-only prompts, which is all the plan executor needs for a
    ``PREPARE`` step.
    """

    def __init__(self):
        self.iteration = 0

    # -- plan-driven tool emission -------------------------------------------

    def create_response(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        plan: Optional[Dict[str, Any]] = None,
        completed_tools: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """Create response from LLM, returning tool calls or final message content.

        When ``plan`` is supplied, ``completed_tools`` carries the step numbers already
        dispatched, so repeated use of the same tool is tracked per plan step rather than
        per tool name.
        """
        self.iteration += 1

        if plan:
            return self._next_planned_call(plan, completed_tools or [], messages)

        return {
            "role": "assistant",
            "content": self._text_summary(messages),
            "tool_calls": None,
        }

    def _next_planned_call(
        self,
        plan: Dict[str, Any],
        completed_tools: List[Any],
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Return the first unplanned tool step of the plan, in order."""
        window = self._window_from_messages(messages)
        done = {str(c) for c in completed_tools}
        aliases: Dict[str, str] = {}
        comparisons: Dict[str, str] = {}

        for step in sorted(plan.get("steps", []), key=lambda s: s.get("step_number", 0)):
            tool_name = step.get("tool_name")
            operation = step.get("operation")
            step_number = step.get("step_number")
            if not tool_name or tool_name == "prepare" or operation in _PER_RECORD:
                continue
            if str(step_number) in done:
                continue

            arguments = self._bind_arguments(step, window, aliases, comparisons)
            if tool_name == "fetch_financial_data":
                reference = f"mock_{tool_name}_{step_number}"
                output_alias = (step.get("parameters") or {}).get("dataset_alias")
                if output_alias:
                    aliases[output_alias] = reference
            if tool_name == "compare_records":
                reference = f"mock_{tool_name}_{step_number}"
                comparison_alias = (step.get("parameters") or {}).get("comparison_alias")
                if comparison_alias:
                    comparisons[comparison_alias] = reference

            return {
                "role": "assistant",
                "content": f"Executing planned step {step.get('step_number')}: {operation}.",
                "tool_calls": [
                    {
                        "id": f"call_plan_{step.get('step_number')}_{self.iteration}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(arguments),
                        },
                    }
                ],
            }

        return {
            "role": "assistant",
            "content": (
                "All planned steps have been dispatched. Execution complete."
            ),
            "tool_calls": None,
        }

    def _bind_arguments(
        self,
        step: Dict[str, Any],
        window: Dict[str, Any],
        aliases: Dict[str, str],
        comparisons: Dict[str, str],
    ) -> Dict[str, Any]:
        params = dict(step.get("parameters") or {})
        tool_name = step["tool_name"]

        if tool_name == "fetch_financial_data":
            return {
                "data_source_code": params.get("data_source_code") or step.get("data_source_code"),
                "entity_code": params.get("entity_code") or step.get("entity_code"),
                "start_date": window["start_date"],
                "end_date": window["end_date"],
                "filters": window["filters"],
            }
        if tool_name == "compare_records":
            return {
                "left_dataset_reference": aliases.get(params.get("left_alias"), "dataset_left"),
                "right_dataset_reference": aliases.get(params.get("right_alias"), "dataset_right"),
                "match_configuration": params.get("match_configuration") or {},
            }
        if tool_name in ("calculate_kri_metrics", "build_evidence"):
            return {"audit_run_reference": "current_run", **params}
        return params

    @staticmethod
    def _window_from_messages(messages: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        messages = messages or []
        start_date, end_date, filters = "2026-01-01", "2026-03-31", {}
        for message in messages:
            if message.get("role") != "user":
                continue
            content = message.get("content", "")
            date_match = re.search(
                r"Audit Window:\s*(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})", content
            )
            if date_match:
                start_date, end_date = date_match.group(1), date_match.group(2)
            cust_match = re.search(r"Customer Filter:\s*([^\n]+)", content)
            if cust_match:
                value = cust_match.group(1).strip()
                if value and value.upper() not in ("ALL", "NONE", "NULL", "STRING", "UNDEFINED"):
                    filters["customer_name"] = value
        return {"start_date": start_date, "end_date": end_date, "filters": filters}

    @staticmethod
    def _text_summary(messages: List[Dict[str, Any]]) -> str:
        for message in reversed(messages or []):
            if message.get("role") == "user":
                return (
                    "Offline analysis: the audit workflow is executed deterministically from the "
                    f"stored execution plan. Requested analysis: "
                    f"{str(message.get('content', ''))[:400]}"
                )
        return "Offline analysis completed."

    # -- step classification --------------------------------------------------

    def classify_steps(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Offline classification is unavailable: the interpreter falls back to rules."""
        return {}


def get_llm_provider() -> LLMProvider:
    """Factory to retrieve configured LLM provider (Azure or Mock)."""
    if settings.use_mock_llm or not settings.is_azure_configured():
        return MockLLMProvider()
    return AzureOpenAIProvider()
