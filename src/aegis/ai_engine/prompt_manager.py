"""AEGIS prompt manager — versioned prompts for AI runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class PromptVersion:
    """AC-05.04: Prompt versions are explicit and traceable."""

    version: str
    template: str
    description: str = ""

    @property
    def hash(self) -> str:
        return sha256(self.template.encode()).hexdigest()


class PromptManager:
    """Manages versioned prompts for AI runs."""

    def __init__(self) -> None:
        self._prompts: dict[str, PromptVersion] = {}

    def register(self, version: PromptVersion) -> None:
        """Register a prompt version."""
        self._prompts[version.version] = version

    def get(self, version: str) -> PromptVersion:
        """Get a prompt version."""
        if version not in self._prompts:
            raise KeyError(f"Prompt version not found: {version}")
        return self._prompts[version]

    def render(
        self,
        version: str,
        context: dict[str, Any],
    ) -> str:
        """Render a prompt with context."""
        prompt = self.get(version)
        return prompt.template.format(**context)

    def list_versions(self) -> list[str]:
        """List all registered prompt versions."""
        return list(self._prompts.keys())
