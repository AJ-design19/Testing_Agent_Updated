"""
Deterministic script base interface (§12.1 scripts/ store).

Every script in scripts/ must subclass DeterministicScript and implement run().
Scripts are referenced by journey YAML via the deterministic_script field.
They handle steps that have a known-correct mechanical procedure that doesn't
need LLM judgement — e.g. clicking a specific tab, reading a specific element.

Change control: pull request, ≥1 ML/infra reviewer, smoke-test green (§13.1).
"""

from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ScriptResult:
    success: bool
    output: Any = None
    screenshot_path: Optional[str] = None
    error: Optional[str] = None
    notes: str = ""


class DeterministicScript(ABC):
    """
    Base class for all deterministic step scripts.
    Subclasses implement run(page, args) and are discovered by name.
    """

    name: str = ""      # set on each subclass; matches deterministic_script field in YAML

    @abstractmethod
    async def run(self, page, args: dict) -> ScriptResult:
        """
        Execute the deterministic step.
        page: Playwright page object
        args: dict of arguments from the journey YAML step's script_args field
        """
        ...

    async def safe_run(self, page, args: dict) -> ScriptResult:
        """Wraps run() with error handling so a script failure never crashes the run."""
        try:
            return await self.run(page, args)
        except Exception as e:
            logger.error("[Script:%s] Error: %s", self.name, e)
            return ScriptResult(success=False, error=str(e))
