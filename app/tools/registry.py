"""Centralized Tool Registry for Allowlisting and Secure Dispatch."""

from typing import Dict, List, Optional, Any
from app.tools.base import RegisteredTool


class ToolRegistry:
    """Central registry enforcing tool allowlisting and schema generation."""

    def __init__(self):
        self._tools: Dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        """Register a new deterministic tool."""
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[RegisteredTool]:
        """Retrieve registered tool by name."""
        return self._tools.get(name)

    def is_registered(self, name: str) -> bool:
        """Check if tool name is registered and enabled."""
        tool = self._tools.get(name)
        return tool is not None and tool.enabled

    def get_all_tools(self) -> List[RegisteredTool]:
        """Return list of all registered tools."""
        return list(self._tools.values())

    def get_openai_tool_definitions(self) -> List[Dict[str, Any]]:
        """Export OpenAI function definitions for all enabled tools."""
        return [tool.to_openai_tool_definition() for tool in self._tools.values() if tool.enabled]


# Global tool registry instance
registry = ToolRegistry()
