# Agent Guide

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
- Use `uv` for Python package management in this repo (usually with `--project dify-agent`).
- Use `make typecheck` to run `basedpyright` against `dify-agent/src` and `dify-agent/tests`.
- Run type checking after a coherent change affecting typed behavior and at the required completion gate. Fix task-caused errors; report unrelated baseline errors separately. Rerun only when later changes or evidence invalidate the result.
- Use `pytest` for all tests in this package.
- When integrating with, implementing, or mocking a dependency, inspect the dependency's source code to confirm its API shape and runtime behavior instead of guessing from names alone.
- Prefer simple functions over small “utility classes” for lightweight helpers.
- Avoid implementing dunder methods unless it’s clearly needed and matches existing patterns.
- Keep code readable and explicit—avoid clever hacks.

### Testing

- Work in TDD style: write or update a failing test first when changing behavior, then make the implementation pass, then refactor while keeping tests and typecheck green.
- Use `make test` to run the agent pytest suite.
- Keep local tests under `dify-agent/tests/local/`.
- Mirror the `dify-agent/src/` package structure inside `dify-agent/tests/local/` so test locations stay predictable.

#### Local Tests

- Write local tests for stable, externally observable behavior that can run quickly without real external services.
- In this repo, code, comments, docs, and tests are expected to change together. Because of that, a local test is only useful if it would still be correct after an internal refactor that does not change the intended contract.
- Local tests should verify:
  - what callers and downstream code can observe and rely on
  - how the unit is expected to use its dependencies at the boundary
  - how the unit handles dependency success, failure, empty responses, malformed responses, and documented error cases
  - documented invariants, error mapping, and output/input shape guarantees
- When asserting dependency interactions, assert only the parts of the request or response that are part of the real boundary contract. Do not over-specify incidental details that callers or dependencies do not rely on.
- It is acceptable to mock dependencies in local tests, but only when the mock represents a real contract, schema, documented behavior, or known regression.
- Tests may use line-scoped type-ignore comments when intentionally exercising runtime validation paths that static typing would normally reject. Keep the ignore on the exact invalid call.
- Do not use local tests to prove real integration, network wiring, serialization, framework configuration, or third-party runtime behavior; cover those in higher-level tests.
- Meaningless local tests include:
  - tests that only mirror the current implementation or must be updated whenever internal code changes even though the contract did not change
  - tests of private helpers, local variables, temporary state, internal branching, or exact internal call order unless those details are part of the published contract
  - tests with mocked dependency behavior that is invented only to make the current implementation pass
  - tests that add no value beyond static type checking or linting

### Logging & Errors

- Never use `print`; use a module-level logger:
  - `logger = logging.getLogger(__name__)`
- Include tenant/app/workflow identifiers in log context when relevant.
- Raise domain-specific exceptions and translate them into HTTP responses in controllers.
- Log retryable events at `warning`, terminal failures at `error`.

### Pydantic Usage

- Define DTOs with Pydantic v2 models and forbid extras by default.
- Use `@field_validator` / `@model_validator` for domain rules.

### Generics & Protocols

- Use `typing.Protocol` to define behavioural contracts (e.g., cache interfaces).
- Apply generics (`TypeVar`, `Generic`) for reusable utilities like caches or providers.
- Validate dynamic inputs at runtime when generics cannot enforce safety alone.
