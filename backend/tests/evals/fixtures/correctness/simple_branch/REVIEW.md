# Correctness review fixture

Validate the state transition behavior in `src/state.py`.

The change must preserve the draft-to-reviewing guard and fail closed for all other states.
Any guard inversion here is a correctness regression.
