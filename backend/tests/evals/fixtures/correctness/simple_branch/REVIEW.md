# Correctness review fixture

Validate the state transition behavior in `src/state.py`, cache isolation in
`src/cache.py`, and authorization preservation for `src/permissions.py`.

The change must preserve the draft-to-reviewing guard and fail closed for all other states.
Any guard inversion here is a correctness regression.
Cache keys must preserve per-user isolation, and deleting an authorization guard
must be reported as a security regression.
