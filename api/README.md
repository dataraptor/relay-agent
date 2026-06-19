# api

The backend service — a thin HTTP layer that exposes the engine over a network.

This layer is an adapter, not a place for business logic. It handles requests,
validation, configuration, and serialization, then delegates the real work to `core`.
If you deleted it, the engine would still work; you'd just lose the HTTP interface.

**Contains:** the web app and its routes, request/response schemas, configuration,
a container definition, and API-level tests.

**Depends on:** `core`.
