"""Command bodies, one module per area.

Each command's job: read `ctx.obj`, call one `ApiClient` method, hand the result to the
renderer. Business logic that outgrows that belongs in `bundle`, `digest`, `signing`, or
`state` -- not here.
"""
