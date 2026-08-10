"""Atomic local persistence for user-authored reviewer prompt overrides."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from codelens.reviewer_catalog.application.prompt_settings import PromptLocale


class FilesystemReviewerPromptStore:
    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir.expanduser().resolve() / "reviewer-prompts.json"

    async def load_override(self, reference: str, locale: PromptLocale) -> str | None:
        return await asyncio.to_thread(self._load_override_sync, reference, locale)

    async def save_override(self, reference: str, locale: PromptLocale, prompt: str) -> None:
        await asyncio.to_thread(self._update_sync, reference, locale, prompt)

    async def delete_override(self, reference: str, locale: PromptLocale) -> None:
        await asyncio.to_thread(self._update_sync, reference, locale, None)

    def _load_override_sync(self, reference: str, locale: PromptLocale) -> str | None:
        payload = self._read()
        value = payload.get(reference, {}).get(locale)
        return value if isinstance(value, str) else None

    def _update_sync(self, reference: str, locale: PromptLocale, prompt: str | None) -> None:
        payload = self._read()
        agent = payload.setdefault(reference, {})
        if prompt is None:
            agent.pop(locale, None)
            if not agent:
                payload.pop(reference, None)
        else:
            agent[locale] = prompt
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".reviewer-prompts-", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(
                    payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def _read(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("reviewer prompt settings are invalid")
        return raw
