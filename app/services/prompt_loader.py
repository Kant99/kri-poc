"""Prompt Loader for Externalized Agent and Planner Prompts.

Both ``app/prompts/*.txt`` files were previously dead: the orchestrator inlined its own
system prompt and the planner prompt was never loaded at all. This module makes the files
the single source of truth and fails loudly when a required prompt is missing.
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

PLANNER_PROMPT_FILE = "planner_prompt.txt"
AGENT_SYSTEM_PROMPT_FILE = "agent_system_prompt.txt"


@lru_cache(maxsize=32)
def _read(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def load_prompt(name: str, default: Optional[str] = None) -> str:
    """Load a prompt file by name, optionally falling back to a literal default."""
    try:
        return _read(name)
    except FileNotFoundError:
        if default is not None:
            logger.warning("Prompt file %s missing; using inline default.", name)
            return default
        raise


def load_planner_prompt() -> str:
    """Load the plan interpreter (planner) system prompt."""
    return load_prompt(
        PLANNER_PROMPT_FILE,
        default=(
            "You are the Audit Planner for the Continuous Internal Audit KRI Engine. "
            "Map each administrator test step onto exactly one approved operation."
        ),
    )


def load_agent_system_prompt() -> str:
    """Load the audit execution agent system prompt."""
    return load_prompt(
        AGENT_SYSTEM_PROMPT_FILE,
        default=(
            "You are the Continuous Internal Audit Agent Orchestrator. Execute the "
            "structured execution plan for the target KRI using the registered tools."
        ),
    )
