"""Canonical JSON serialization shared across domain and infrastructure layers.

Lives in the Domain layer so both ``context_checkpoint`` infrastructure and the
``TokenCounterPort`` adapter can depend on a single stable serializer without
introducing a reverse dependency from Infrastructure back into Domain.
"""

from __future__ import annotations

import json


def canonical_json(value: object) -> str:
    """Serialize ``value`` with stable key ordering and compact separators.

    Deterministic output is required so callers can hash evidence identity and
    count tokens against a stable byte representation regardless of insertion
    order in the source mapping.
    """

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
