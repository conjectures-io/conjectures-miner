"""The validator's HTTP surface: one client, typed responses, mapped failures.

Split three ways so that transport, shape, and failure translation stay separable:

- `client`  -- httpx wiring, one method per endpoint, no formatting
- `models`  -- pydantic response models
- `errors`  -- the API's `reason_code` vocabulary, and what a human should do about each
"""
