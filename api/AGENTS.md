# API Agent Guide

## Relevant context

Use surrounding code, docstrings, and comments to understand affected contracts and non-obvious invariants. Keep those notes current when the task changes their meaning; routine edits do not require new documentation or a separate notes pass.

## Coding Style

This is the default standard for backend code in this repo. Follow it for new code and use it as the checklist when reviewing changes.

### Linting & Formatting

- Use Ruff for formatting and linting (follow `.ruff.toml`).
- Keep each line under 120 characters (including spaces).

### Types

Use explicit public types and Pydantic v2. Prefer TypedDict for fixed-shape dictionaries and dict/Mapping for dynamic keys; retain existing class member annotations. Use dataclass defaults and slots when they fit the data container.

### General Rules

- Use Pydantic v2 conventions.
- Use `uv` for Python package management in this repo (usually with `--project api`).
- Prefer simple functions over small “utility classes” for lightweight helpers.
- Avoid implementing dunder methods unless it’s clearly needed and matches existing patterns.
- When authorized work requires local runtime validation, reuse an existing service or start only the required local service and clean up what this task started. Keep production changes and billable calls within the user's authorization.
- Keep files below ~800 lines; split when necessary.
- Keep code readable and explicit—avoid clever hacks.

### Architecture & Boundaries

- Mirror the layered architecture: controller → service → core/domain.
- Reuse existing helpers in `core/`, `services/`, and `libs/` before creating new abstractions.
- Optimise for observability: deterministic control flow, clear logging, actionable errors.

### Owner-Bound Resource References

- Resolve and validate the outer owner before binding a nested resource ID.
- For stable single-parent chains, use immutable nested `NamedTuple` refs.
- Root refs carry tenant plus root ID; child refs carry the parent ref.
- In production, construct refs through the domain ref service.
- Python allowing direct construction does not grant authorization.
- Scope every consuming query with complete owner predicates; refs are not security tokens.
- Keep polymorphic owners flat until explicit nominal owner types exist.
- Do not add generic ref bases or compatibility fields only for uniformity.
- Reconstruct internal refs from validated database state after payload or async boundaries.

### Logging & Errors

- Never use `print`; use a module-level logger:
  - `logger = logging.getLogger(__name__)`
- Include tenant/app/workflow identifiers in log context when relevant.
- Raise domain-specific exceptions (`services/errors`, `core/errors`) and translate them into HTTP responses in controllers.
- Log retryable events at `warning`, terminal failures at `error`.

### SQLAlchemy Patterns

- Models inherit from `models.base.TypeBase`; do not create ad-hoc metadata or engines.
- Open sessions with context managers:

```python
from sqlalchemy.orm import Session

with Session(db.engine, expire_on_commit=False) as session:
    stmt = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.tenant_id == tenant_id,
    )
    workflow = session.execute(stmt).scalar_one_or_none()
```

- Prefer SQLAlchemy expressions; avoid raw SQL unless necessary.
- Always scope queries by `tenant_id` and protect write paths with safeguards (`FOR UPDATE`, row counts, etc.).
- Introduce repository abstractions only for very large tables (e.g., workflow executions) or when alternative storage strategies are required.

### Storage & External I/O

- Access storage via `extensions.ext_storage.storage`.
- Use `core.helper.ssrf_proxy` for outbound HTTP fetches.
- Background tasks that touch storage must be idempotent, and should log relevant object identifiers.

### Pydantic Usage

- Define DTOs with Pydantic v2 models and forbid extras by default.
- Use `@field_validator` / `@model_validator` for domain rules.

### Generics & Protocols

- Use `typing.Protocol` to define behavioural contracts (e.g., cache interfaces).
- Apply generics (`TypeVar`, `Generic`) for reusable utilities like caches or providers.
- Validate dynamic inputs at runtime when generics cannot enforce safety alone.

### Tooling & Checks

Quick checks while iterating:

- Format: `make format`
- Lint (includes auto-fix): `make lint`
- Type check: `make type-check`
- Unit tests: `make test`
- Full backend tests, including Docker-backed suites: `make test-all`
- Targeted tests: `make test TARGET_TESTS=./api/tests/<target_tests>`

Before opening a PR / submitting:

- `make lint`
- `make type-check`
- `make test`

### Controllers & Services

- Controllers: parse input via Pydantic, invoke services, return serialised responses; no business logic.
- Services: coordinate repositories, providers, background tasks; keep side effects explicit.
- Document non-obvious behaviour with concise docstrings and comments.
- For `204 No Content` responses, return an empty body only; never return a dict, model, or other payload.
- For Flask-RESTX controller request, query, and response schemas, follow `controllers/API_SCHEMA_GUIDE.md`.
  In short: use Pydantic models, document GET query params with `query_params_from_model(...)`, register response
  DTOs with `register_response_schema_models(...)`, serialize response DTOs with `dump_response(...)`,
  and avoid adding new legacy `ns.model(...)`, `@marshal_with(...)`, or GET `@ns.expect(...)` patterns.

### Miscellaneous

- Use `configs.dify_config` for configuration—never read environment variables directly.
- Maintain tenant awareness end-to-end; `tenant_id` must flow through every layer touching shared resources.
- Queue async work through `services/async_workflow_service`; implement tasks under `tasks/` with explicit queue selection.
- Keep experimental scripts under `dev/`; do not ship them in production builds.
