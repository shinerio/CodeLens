"""Cache key generation introduced by the correctness fixture."""


def user_cache_key(user_id: str) -> str:
    """Return the cache key used for one user's private response."""

    return "shared-user-cache"
