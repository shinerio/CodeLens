"""Resolve instruction-only Skills from frozen host facts."""

from fnmatch import fnmatchcase

from codelens.capabilities.domain.models import (
    CapabilityProfile,
    FrozenSkillActivation,
    ToolContractReference,
)
from codelens.capabilities.domain.skills import (
    SkillActivationFacts,
    SkillManifest,
    SkillPolicy,
)


class SkillActivationResolver:
    """Activate Skills deterministically without granting or executing capabilities."""

    def resolve(
        self,
        *,
        policy: SkillPolicy,
        profile: CapabilityProfile,
        facts: SkillActivationFacts,
    ) -> tuple[FrozenSkillActivation, ...]:
        """Return sorted frozen instructions whose host-derived rules match.

        Raises:
            ValueError: If an activated Skill requires a tool contract absent
                from the statically bound Capability Profile.
        """

        available_tools = self._available_tools(profile)
        activations: list[FrozenSkillActivation] = []
        for manifest in policy.manifests:
            reasons = self._activation_reasons(manifest, facts)
            if not reasons:
                continue
            missing_tools = tuple(
                tool for tool in manifest.required_tools if tool not in available_tools
            )
            if missing_tools:
                missing = ", ".join(
                    f"{tool.name}:v{tool.version}" for tool in sorted(missing_tools)
                )
                raise ValueError(
                    f"Skill {manifest.reference} requires unavailable required capability: "
                    f"{missing}"
                )
            activations.append(
                FrozenSkillActivation(
                    skill_id=manifest.skill_id,
                    version=manifest.version,
                    content_hash=manifest.content_hash,
                    instruction_text=manifest.instruction_text,
                    activation_reason=", ".join(reasons),
                )
            )
        return tuple(sorted(activations, key=lambda item: (item.skill_id, item.version)))

    @staticmethod
    def _available_tools(profile: CapabilityProfile) -> frozenset[ToolContractReference]:
        return frozenset(
            (*profile.builtin_tools, *(binding.contract for binding in profile.mcp_tools))
        )

    @staticmethod
    def _activation_reasons(
        manifest: SkillManifest,
        facts: SkillActivationFacts,
    ) -> tuple[str, ...]:
        languages = sorted(set(manifest.activation_languages) & set(facts.languages))
        paths = sorted(
            path
            for path in set(facts.changed_paths)
            if any(fnmatchcase(path, pattern) for pattern in manifest.activation_path_patterns)
        )
        if manifest.activation_languages and not languages:
            return ()
        if manifest.activation_path_patterns and not paths:
            return ()
        reasons = tuple(f"language:{language}" for language in languages)
        return (*reasons, *(f"path:{path}" for path in paths))
