---
name: how-to-write-component
description: "Apply Dify component contracts when changing component ownership, state, generated API calls, or interaction primitives."
---

# Dify component decisions

Apply the contracts needed by the requested component change. Use existing code as evidence, and fix other callers only when the changed shared contract requires it; do not expand a local task into cleanup of similar patterns elsewhere.

Keep state and handlers at the lowest owner that needs them. Use generated API contracts for migrated calls, Dify UI primitives and design tokens, and preserve visible keyboard focus and existing user behavior. Add abstractions only for shared behavior or a meaningful boundary.

Choose the relevant reference:

- [ownership.md](references/ownership.md): component placement, props/types, public boundaries, loading UI and focus styling.
- [state-and-data.md](references/state-and-data.md): Jotai scope/hydration, generated API nullability, and query/mutation ownership.
- [interactions.md](references/interactions.md): keyboard commands, overlays, effects, navigation and performance.

Do not load all three by default. For an isolated style change, use the touched component and relevant design-token or primitive contract. Keep existing boundary notes current when their contract changes; new documentation follows the user's scope. Use `web/docs/test.md` to choose validation based on behavior and regression risk.
