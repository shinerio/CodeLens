"""Declarative, instruction-only Skill values for deterministic activation."""

import re
from dataclasses import dataclass

from codelens.capabilities.domain.models import ToolContractReference

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_LANGUAGE_PATTERN = re.compile(r"^[a-z][a-z0-9_+.-]{0,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SkillManifest:
    """Describe one immutable instruction artifact and its activation inputs.

    A manifest contains text only. It cannot register tools, execute code, read
    repositories, or expand the enclosing Capability Profile.
    """

    skill_id: str
    version: int
    content_hash: str
    required_tools: tuple[ToolContractReference, ...]
    activation_languages: tuple[str, ...]
    instruction_text: str
    activation_path_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _IDENTIFIER_PATTERN.fullmatch(self.skill_id) is None:
            raise ValueError("Skill ID is invalid")
        if self.version < 1:
            raise ValueError("Skill version must be positive")
        if _SHA256_PATTERN.fullmatch(self.content_hash) is None:
            raise ValueError("Skill content hash must be a lowercase SHA-256 digest")
        if not self.instruction_text.strip() or "\0" in self.instruction_text:
            raise ValueError("Skill instruction text must be non-blank text")
        if len(self.required_tools) != len(set(self.required_tools)):
            raise ValueError("Skill contains duplicate required capabilities")
        if len(self.activation_languages) != len(set(self.activation_languages)):
            raise ValueError("Skill contains duplicate activation languages")
        if any(
            _LANGUAGE_PATTERN.fullmatch(language) is None for language in self.activation_languages
        ):
            raise ValueError("Skill activation language is invalid")
        if any(not pattern or "\0" in pattern for pattern in self.activation_path_patterns):
            raise ValueError("Skill activation path pattern is invalid")
        if not self.activation_languages and not self.activation_path_patterns:
            raise ValueError("Skill requires at least one deterministic activation rule")

    @property
    def reference(self) -> str:
        """Return the canonical immutable Skill reference."""

        return f"{self.skill_id}:v{self.version}"


@dataclass(frozen=True)
class SkillActivationFacts:
    """Contain only normalized host-derived facts from the frozen Snapshot."""

    languages: tuple[str, ...]
    changed_paths: tuple[str, ...]

    @classmethod
    def empty(cls) -> "SkillActivationFacts":
        """Return facts that deterministically activate no Skill."""

        return cls((), ())

    def __post_init__(self) -> None:
        if any(_LANGUAGE_PATTERN.fullmatch(language) is None for language in self.languages):
            raise ValueError("Skill activation fact language is invalid")
        if any(
            not path
            or path.startswith("/")
            or "\\" in path
            or "\0" in path
            or ".." in path.split("/")
            for path in self.changed_paths
        ):
            raise ValueError("Skill activation fact path is unsafe")


@dataclass(frozen=True)
class SkillPolicy:
    """Bind an ordered set of immutable Skill manifests to a policy version."""

    policy_id: str
    version: int
    manifests: tuple[SkillManifest, ...]

    def __post_init__(self) -> None:
        if _IDENTIFIER_PATTERN.fullmatch(self.policy_id) is None:
            raise ValueError("Skill policy ID is invalid")
        if self.version < 1:
            raise ValueError("Skill policy version must be positive")
        references = tuple(manifest.reference for manifest in self.manifests)
        if len(references) != len(set(references)):
            raise ValueError("Skill policy contains a duplicate Skill manifest")

    @property
    def reference(self) -> str:
        """Return the canonical immutable policy reference."""

        return f"{self.policy_id}:v{self.version}"
