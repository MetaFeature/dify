# E2E instructions

Dify E2E uses Cucumber and Playwright against the backend from source, a production frontend artifact, and Docker middleware. For setup, commands, runner architecture, or debugging, read the relevant section of [workflow.md](docs/workflow.md). Detailed procedures belong there; this file keeps the execution and scenario contracts.

## Execution boundaries

- Run only one `pnpm -C e2e e2e*` process per local workspace: runners share ports, auth bootstrap state and log paths.
- Reuse the frontend artifact when appropriate. Set `E2E_FORCE_WEB_BUILD=1` when the changed frontend needs a fresh artifact.
- Reset commands remove Docker data and E2E state. Use them only for the disposable environment owned by the authorized test task; consult the reset list in the workflow before running one.
- Deterministic runs exclude `@prepared`, `@external-model`, and `@external-tool`. External runtime calls are opt-in and stay within existing paid-call authorization. Fixture selection or installation alone is not an external runtime call.
- Cucumber's exit status and at least one `testCaseStarted` event define a valid run. An empty tag selection must fail; do not add scenario-count baselines or skipped-scenario allowlists.
- Check affected scenarios and relevant static checks after coherent changes. Preserve required submission checks; repeat only when subsequent changes or evidence warrant it.

## Scenario contracts

- Put scenarios under `features/<capability>/` and domain glue under `features/step-definitions/<capability>/`. Reuse matching existing steps and avoid duplicate globally registered step text.
- Tag by capability; auth tags must reflect actual session behavior. Keep steps declarative, with one user action or assertion per step.
- Use `async function` with typed `this: DifyWorld`. Keep cross-scenario state isolated; per-scenario state belongs in the world.
- Prefer semantic locators and Playwright's retrying assertions. Avoid fixed sleeps and raw selectors when an accessible locator expresses the user action.
- Seed scripts own fixed models, plugins, datasets and apps. Missing or drifted shared fixtures must fail; scenario setup must not silently repair them or skip behavior.
- Scenario-owned setup may create disposable resources, with typed cleanup fields or `DifyWorld.registerCleanup(...)`. Remove dependent resources before owners; attach cleanup failures to the report, including when the scenario fails.
- Use `support/naming.ts` for E2E resource names and `support/test-materials.ts` for small deterministic files in `fixtures/test-materials/`.
- Import generated Console/Web/Service API shapes directly from `@dify/contracts/.../types.gen`. Keep local types for E2E state and intentionally narrowed views; fix and regenerate missing API contracts instead of duplicating them.
- Keep feature-specific fixture and runtime rules with the feature. For Agent v2 scenarios or their step definitions, also use `features/agent-v2/AGENTS.md`.

When adding scenarios, use the authoring and locator sections of the workflow. When running external or feature-owned services, read its execution-tag and seed sections first. Failed-scenario screenshots, HTML and console diagnostics are already captured by the After hook.
