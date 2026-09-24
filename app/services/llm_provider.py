"""LLM Provider Abstraction Module.

Defines the LLMProvider Protocol and implements:
1. AzureOpenAIProvider for live Azure OpenAI function-calling deployments.
2. MockLLMProvider for deterministic offline execution, local testing, and automated test suites.
"""

import json
import logging
import time
from typing import Protocol, List, Dict, Any, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    """Protocol for LLM orchestration providers."""

    def create_response(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Create response from LLM, returning tool calls or final message content."""
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


class MockLLMProvider:
    """Deterministic Mock LLM Provider simulating intelligent agent orchestration.

    Interprets current conversation context and produces sequential tool calls
    matching the planned test steps for the audit run.
    """

    def __init__(self):
        self.iteration = 0

    def create_response(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        import re
        self.iteration += 1

        # Extract audit window from user message
        start_date = "2026-01-01"
        end_date = "2026-03-31"
        filters: Dict[str, Any] = {}
        for m in messages:
            if m.get("role") == "user":
                content = m.get("content", "")
                date_match = re.search(r"Audit Window:\s*(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})", content)
                if date_match:
                    start_date = date_match.group(1)
                    end_date = date_match.group(2)
                cust_match = re.search(r"Customer Filter:\s*([^\n]+)", content)
                if cust_match:
                    cust_val = cust_match.group(1).strip()
                    if cust_val and cust_val.upper() not in ("ALL", "NONE", "NULL", "STRING", "UNDEFINED"):
                        filters["customer_name"] = cust_val

        # Check what tool outputs exist in message history
        tool_responses = [m for m in messages if m.get("role") == "tool"]
        tool_names_called = []
        for tr in tool_responses:
            name = tr.get("name")
            if name:
                tool_names_called.append(name)

        # Inspect tool response contents to extract dataset references
        orders_ds = "dataset_orders_001"
        pos_ds = "dataset_pos_001"
        comp_ref = "comp_001"
        for tr in tool_responses:
            try:
                content = json.loads(tr.get("content", "{}"))
                if content.get("entity") == "ORDER_INTAKE" and content.get("dataset_reference"):
                    orders_ds = content.get("dataset_reference")
                elif content.get("entity") == "PURCHASE_ORDER" and content.get("dataset_reference"):
                    pos_ds = content.get("dataset_reference")
                elif content.get("comparison_reference"):
                    comp_ref = content.get("comparison_reference")
            except Exception:
                pass

        # Step 1: Extract Order Intake data
        if "fetch_financial_data" not in tool_names_called:
            return {
                "role": "assistant",
                "content": "Extracting Order Intake records from SAP ECC for the audit period.",
                "tool_calls": [
                    {
                        "id": f"call_extract_orders_{self.iteration}",
                        "type": "function",
                        "function": {
                            "name": "fetch_financial_data",
                            "arguments": json.dumps({
                                "source_system": "SAP_ECC",
                                "entity": "ORDER_INTAKE",
                                "start_date": start_date,
                                "end_date": end_date,
                                "filters": filters,
                            }),
                        },
                    }
                ],
            }

        # Step 2: Extract Purchase Orders data
        order_fetches = [tr for tr in tool_responses if "ORDER_INTAKE" in tr.get("content", "")]
        po_fetches = [tr for tr in tool_responses if "PURCHASE_ORDER" in tr.get("content", "")]
        if not po_fetches:
            return {
                "role": "assistant",
                "content": "Extracting Purchase Orders from Red Box PO for the audit period.",
                "tool_calls": [
                    {
                        "id": f"call_extract_pos_{self.iteration}",
                        "type": "function",
                        "function": {
                            "name": "fetch_financial_data",
                            "arguments": json.dumps({
                                "source_system": "RED_BOX_PO",
                                "entity": "PURCHASE_ORDER",
                                "start_date": start_date,
                                "end_date": end_date,
                                "filters": filters,
                            }),
                        },
                    }
                ],
            }

        # Step 3: Match records
        if "compare_records" not in tool_names_called:
            return {
                "role": "assistant",
                "content": "Matching order intake records with purchase orders on order_id.",
                "tool_calls": [
                    {
                        "id": f"call_compare_{self.iteration}",
                        "type": "function",
                        "function": {
                            "name": "compare_records",
                            "arguments": json.dumps({
                                "left_dataset_reference": orders_ds,
                                "right_dataset_reference": pos_ds,
                                "match_configuration": {
                                    "left_field": "order_id",
                                    "right_field": "order_id",
                                    "fallback_field": "po_reference",
                                },
                            }),
                        },
                    }
                ],
            }

        # Step 4: Calculate aggregate KRI metrics
        if "calculate_kri_metrics" not in tool_names_called:
            return {
                "role": "assistant",
                "content": "Calculating aggregate KRI metrics and exception summaries.",
                "tool_calls": [
                    {
                        "id": f"call_metrics_{self.iteration}",
                        "type": "function",
                        "function": {
                            "name": "calculate_kri_metrics",
                            "arguments": json.dumps({
                                "audit_run_reference": "current_run",
                                "calculation_policy": {
                                    "mismatch_value_definition": "ORDER_VALUE_OF_QUALIFYING_EXCEPTIONS"
                                },
                            }),
                        },
                    }
                ],
            }

        # Step 5: Build evidence packages
        if "build_evidence" not in tool_names_called:
            return {
                "role": "assistant",
                "content": "Building reproducible evidence records for all identified exceptions.",
                "tool_calls": [
                    {
                        "id": f"call_evidence_{self.iteration}",
                        "type": "function",
                        "function": {
                            "name": "build_evidence",
                            "arguments": json.dumps({
                                "audit_run_reference": "current_run",
                            }),
                        },
                    }
                ],
            }

        # Step 6: Complete workflow
        return {
            "role": "assistant",
            "content": "Audit workflow execution completed successfully. All test steps interpreted, tools executed deterministically, metrics computed, and evidence packages stored.",
            "tool_calls": None,
        }


def get_llm_provider() -> LLMProvider:
    """Factory to retrieve configured LLM provider (Azure or Mock)."""
    if settings.use_mock_llm or not settings.is_azure_configured():
        return MockLLMProvider()
    return AzureOpenAIProvider()
