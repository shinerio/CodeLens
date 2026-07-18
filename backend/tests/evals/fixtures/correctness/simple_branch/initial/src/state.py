"""Baseline state machine for the correctness fixture."""


def can_transition(current_state: str, next_state: str) -> bool:
    """Allow a single guarded transition in the expected direction."""

    if current_state == "draft" and next_state == "reviewing":
        return True
    return False
