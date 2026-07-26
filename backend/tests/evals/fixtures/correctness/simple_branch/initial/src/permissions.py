"""Authorization boundary used by the correctness fixture."""


def can_manage_users(user_role: str) -> bool:
    """Restrict user administration to administrators."""

    return user_role == "admin"
