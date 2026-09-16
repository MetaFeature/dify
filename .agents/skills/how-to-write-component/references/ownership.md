# Ownership

Read only the sections relevant to the component change. Repository paths in this guide are relative to the Dify root.

## First Decisions

| Question | Default | Promote or extract only when |
| --- | --- | --- |
| Where should code live? | Keep it local to the feature workflow, route, or owner. | Multiple verticals need the same stable primitive. |
| How should route/tab folders be named? | Match the current route segment, tab name, or user-visible surface. | Keep a historical or broader parent only when it still owns multiple surfaces. |
| Who owns state, data, and handlers? | The lowest component that uses them. | A parent coordinates shared loading, errors, empty UI, selection, submission, navigation, or one consistent snapshot. |
| Should this become Jotai state? | Keep synchronous UI/form state in component or DOM state. | Siblings need one source of truth, the value drives atoms, or scoped workflow state must survive hidden/unmounted steps. |
| Should URL state enter Jotai? | Let Next.js route params and `nuqs` own URL state and updates. | Query atoms or shared derived atoms need a read-only bridge hydrated at the route/surface boundary. |
| Should this query/mutation become an atom? | Use TanStack Query hooks at the lowest owner. | It reads atom state, feeds derived atoms, or participates in shared Jotai workflow orchestration. |
| Should this be a helper/wrapper? | Prefer direct readable code at the use site. | The name captures a stable domain rule or the wrapper owns real behavior, validation, state, error handling, or semantics. |
| Where should a hotkey live? | Keep a single-owner hotkey constant in its component. | Multiple production files share one command, or the feature owns a real command registry with shared metadata and behavior. |
| Is an Effect needed? | No. Derive during render or handle the user action in the event handler. | It synchronizes with an external system such as browser APIs, subscriptions, timers, analytics, or imperative DOM/non-React widgets. |

## Core Defaults

- Search before adding UI, hooks, helpers, query utilities, or styling patterns. Reuse existing base components, feature components, hooks, utilities, and design styles when they fit.
- Follow Dify's CSS-first Tailwind v4 contract from `packages/dify-ui/README.md` and `packages/dify-ui/AGENTS.md`. Prefer design-system tokens, utilities, and radius mappings over generic Tailwind choices.
- Preserve visible keyboard focus states on the final focusable element. Prefer styled `@langgenius/dify-ui/*` controls when available, because components such as `Button` and form/control primitives carry the standard Dify UI `focus-visible` styling. Do not assume every Dify UI export provides visual focus styles: headless anatomy parts and direct Base UI re-exports such as dialog/popover/tooltip/drawer triggers usually only provide behavior and semantics. When using native `button` / `a`, custom trigger `render` props, clickable rows, icon buttons, menu-like items, or direct trigger parts, verify the rendered focusable element has a visible focus state. If it does not, add the standard Dify UI focus style: `outline-hidden focus-visible:ring-2 focus-visible:ring-state-accent-solid`. Do not hide outlines without an equivalent visible `focus-visible` indicator. Component-specific focus styles should follow an existing styled primitive pattern or a concrete design constraint, not a new ad hoc style.
- Group feature code by workflow, route, or ownership area with route-aligned names: components, hooks, local types, query helpers, atoms, constants, tests, and small utilities should live near the code that changes with them.
- Keep source/default selection, validation, dirty checks, and payload shaping close to the workflow that owns submit behavior. Do not hide flow-specific priority order, fallback behavior, or submit semantics in generic utilities.
- Prefer direct conditionals for small branch-specific decisions, especially form source selection and request payload assembly.
- Loading states for page sections, cards, lists, tables, forms, and drawers should be skeletons scoped to the content being loaded. Use spinners only for small inline busy indicators.
- Maintain an existing module boundary note when this task changes its contract. Create a new README only when the user requests it or the changed boundary needs a durable explanation; document only the meaningful relationships.

## Layout And Ownership

- State-heavy wizards, drawers, modals, and secondary workflows can be a small feature surface: an entry file, one feature-local state file when Jotai is actually needed, and shallow `ui/` owners that match real visual regions.
- The entry file handles route integration, provider wiring, close behavior, and surface mounting. The composition owner handles high-level workflow branching. The closest visual owner handles section branching.
- When a page or tab maps to a route segment, name its feature folder after that route/tab surface instead of a stale parent grouping. Remove misleading intermediate folders when only one surface remains.
- When a tab folder grows into several independent sections or action areas, split the first level by product/visual owners. Keep the root for the entry component and cross-owner state, colocate tests with the owner folder, and put truly shared local UI under a specifically named `components/` file.
- Repeated TanStack query calls in sibling components are acceptable when each component independently consumes the data; TanStack Query deduplicates and shares cache.
- Pass stable domain identity across boundaries. Do not forward derived presentation state when the receiver can derive it from its own data source.
- A component that owns a visual surface should also own data access, loading, empty, and error states for content rendered inside it unless a parent truly coordinates that state.
- Avoid prop drilling. One pass-through layer is acceptable; repeated forwarding means ownership should move down or into feature-scoped Jotai UI state. Keep server/cache state in Query and API flow.
- Do not replace prop drilling with one large view-model hook threaded through section props. Move each hook, query, derived value, and handler to the concrete section that consumes it.
- Keep callbacks in a parent only for workflow coordination such as form submission, shared selection, batch behavior, or navigation. Otherwise let the child, menu, or row own the action.

## Components, Props, And Types

- Type component signatures directly; do not use `FC` or `React.FC`.
- Prefer `function` for top-level components and module helpers. Use arrow functions for local callbacks, handlers, and lambda-style APIs.
- Prefer named exports. Use default exports only where the framework requires them, such as Next.js route files.
- Avoid barrel files that only re-export secondary owners. `index.tsx` is acceptable for a route/tab entry component; import header controls, switches, sections, and row owners from their concrete owner files.
- Type simple one-off props inline. Use a named `Props` type only when reused, exported, complex, or clearer.
- Use API-generated or API-returned types at component boundaries. Keep small UI conversion helpers and one-off UI extensions beside the component that needs them.
- Preserve domain value types for selection components. Do not widen enum, union, boolean, numeric, object, or nullable select/radio values to `string`; keep wrappers and option value carriers typed from their feature option collection.
- Avoid `common.tsx` buckets for shared UI. Use a feature-local `components/` folder with concrete filenames that describe the shared role.
- Do not create type aliases that only rename another type. Use aliases only for real UI concepts, refinements, or reusable local contracts.
- Name values by their domain role and backend API contract, especially persistent IDs and route params. Normalize framework or route params at the boundary.
- Put fallback and invariant checks in the lowest component that already handles that state. Do not extract helpers whose only behavior is hiding missing display data.
