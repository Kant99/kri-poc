"""Tool Framework Base Classes and Registration Models."""

from typing import Callable, Any, Type, Dict, Optional
from pydantic import BaseModel, ValidationError


class RegisteredTool:
    """Encapsulates a registered, controlled deterministic audit tool."""

    def __init__(
        self,
        name: str,
        description: str,
        input_model: Type[BaseModel],
        output_model: Type[BaseModel],
        handler: Callable[..., Any],
        version: str = "1.0.0",
        enabled: bool = True,
        allowed_contexts: Optional[list] = None,
    ):
        self.name = name
        self.description = description
        self.input_model = input_model
        self.output_model = output_model
        self.handler = handler
        self.version = version
        self.enabled = enabled
        self.allowed_contexts = allowed_contexts or ["AUDIT_RUN"]

    def to_openai_tool_definition(self) -> Dict[str, Any]:
        """Generate official OpenAI function definition schema from Pydantic input model."""
        schema = self.input_model.model_json_schema()
        # Clean schema for OpenAI compatibility
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def execute(self, arguments: Dict[str, Any], context: Any) -> BaseModel:
        """Validate input arguments using Pydantic, execute handler, and validate output."""
        if not self.enabled:
            raise ValueError(f"Tool '{self.name}' is currently disabled.")

        try:
            validated_input = self.input_model.model_validate(arguments)
        except ValidationError as e:
            raise ValueError(f"Invalid arguments for tool '{self.name}': {e}") from e

        # Execute deterministic handler
        raw_output = self.handler(validated_input, context)

        if isinstance(raw_output, self.output_model):
            return raw_output

        # If raw dict, validate to output model
        return self.output_model.model_validate(raw_output)
