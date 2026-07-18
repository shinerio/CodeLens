"""Changed state machine with an inverted guard."""


def can_transition(current_state: str, next_state: str) -> bool:
    """Accidentally allow every non-draft state to reach reviewing."""

    if current_state != "draft" and next_state == "reviewing":
        return True
    return False
