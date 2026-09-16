# State And Data

Read only the sections relevant to the component change. Repository paths in this guide are relative to the Dify root.

## Feature-Scoped Jotai

- A Jotai-backed feature has one feature-local state file for shared primitive atoms, query atoms, derived atoms, write-only actions, mutation atoms, submission orchestration, provider exports, and optional scope configuration.
- Keep component-owned synchronous UI state local even inside Jotai features: dialog open flags, menus/popovers, confirmations, field drafts, and selected local options usually belong in component state.
- Use uncontrolled `@langgenius/dify-ui/form` and `@langgenius/dify-ui/field` controls for edit/create forms whose fields are read only at submit time. Initialize query-backed defaults with `defaultValue` and keyed remounts.
- Promote form state to atoms only when another component must react to in-progress values, a draft must survive unmount/remount in the scoped workflow, or multiple steps share the same editable draft before submit.
- Treat `useParams`, route args, and `nuqs` query state as framework-owned state. When atom logic needs those values, hydrate primitive atoms at the route or surface boundary, such as with `useHydrateAtoms(..., { dangerouslyForceHydrate: true })`; keep URL updates in the route/query-state APIs instead of write atoms.
- Within a route-owned feature, choose one source for route identity. If route params are bridged into feature atoms, use that bridge consistently for route-derived queries and actions instead of also threading the same route id through page, tab, and section props.
- For async work tied to atom state, use `atomWithQuery` or `atomWithMutation`; write atoms should update only the inputs that drive those atoms. This applies to pure frontend async work as well as network requests, so do not hand-roll loading/error/in-flight state with `useState` or `useRef` for atom-orchestrated async behavior. For component-owned remote work, use `useQuery` or `useMutation` directly.
- `jotai-tanstack-query` query atoms do not support TanStack Query tracked properties. A component that reads `useAtomValue(queryAtom)` subscribes to the whole query result, even if it only accesses `data`, `isLoading`, or `isError`. Export field-specific derived atoms and have components read the exact fields they render; use `selectAtom(queryAtom, result => result.field)` for query-result fields so unchanged selections do not notify subscribers. Keep direct `useAtomValue(queryAtom)` only when the component or hook genuinely needs the full observer result.
- Row-local async state belongs to the row owner unless it participates in a shared Jotai workflow or needs atom-scoped reset semantics.
- Leave query and mutation atoms unscoped so they keep shared QueryClient cache and invalidation behavior. Scope resettable primitives and explicit hydration tuples; scope a derived atom only when every dependency should be private to that surface.
- For scoped primitives that are always hydrated by `ScopeProvider`, prefer `atomWithLazy<T>(() => { throw new Error(...) })` when consumers should see a non-null type.
- Order state files by dependency graph: types/constants, primitives, query atoms, query-data derived atoms, business/readiness derived atoms, write actions, mutation atoms, submission orchestration, provider exports.
- Name derived atoms as business facts and write atoms as user or workflow commands. Components should read or write the exact atom they need with `useAtomValue` or `useSetAtom`.
- Menu/dialog `open` state usually stays local, but a scoped atom is acceptable when a composed menu plus secondary surface would otherwise pass confusing `open`/`onClose` props through unrelated layers. Scope that primitive with the surface instance so reset behavior stays local.
- Keep independent dialog lifecycles separate. Avoid one discriminated "current action dialog" atom when dialogs have separate open state, loading guards, or reset behavior.

## Generated API And Nullable Data

- Treat generated contracts as authoritative at API, query, mutation, cache, and service boundaries. For enterprise APIs, use `packages/contracts/generated/enterprise/*`.
- Do not hand-write DTO mirrors, widen generated fields/enums, or add parallel frontend enum/status layers unless they model product state not represented by the API.
- Use generated enum objects and union types directly in props, comparisons, status logic, and i18n keys. Presentation-only tone maps should be keyed by generated enums.
- Normalize or coerce only at real boundaries: user-entered forms, search, URL/query params, file names, DOM IDs, or legacy adapters.
- Do not coerce nullable or optional API strings to `''` in query, derived model, or payload-building code. Keep `null` or `undefined` until the final boundary requiring a string.
- Do not use `value || undefined` for mutation fields where `''` means "clear this value". Trim or normalize at the form boundary, then preserve intentional empty strings.
- Prefer nullable-tolerant render props for API-returned rows. Narrow only where a real value is required, such as mutation params, route hrefs, select values, query input, or required React keys.
- Build required values in the same branch that proves them, using `flatMap`, a local loop, or an early return. Avoid truthiness guards, `filter(Boolean)`, `filter(item => item.id)`, and `!` after filters.
- Use conditional spreads or explicit pushes for conditional array items instead of `undefined` placeholders followed by narrowing filters.
- Empty collection fallbacks are for not-yet-loaded query data or genuinely nullable collections at the owning render boundary, not for hiding required API fields.

## Queries And Mutations

- Keep `web/contract/*` as the API shape source of truth and follow the `{ params, query?, body? }` input shape.
- Consume generated queries with `useQuery(consoleQuery.xxx.queryOptions(...))` or `useQuery(marketplaceQuery.xxx.queryOptions(...))`.
- If a generated query input comes from an atom, including a route-identity bridge atom, keep the query in `atomWithQuery`; do not unwrap the atom in a component just to call `useQuery`.
- Consume owner-local mutations with `useMutation(consoleQuery.xxx.mutationOptions(...))` or `useMutation(marketplaceQuery.xxx.mutationOptions(...))` when pending/error state is not consumed by feature atoms.
- In `atomWithQuery`, `atomWithInfiniteQuery`, and `atomWithMutation`, return generated `queryOptions()`, `infiniteOptions()`, or `mutationOptions()` directly. Pass `enabled`, `retry`, `placeholderData`, `select`, and pagination options into the generated call instead of spreading options into a hand-built object.
- For generated oRPC options with missing required input, branch the whole input with `input: condition ? validInput : skipToken` and `enabled: Boolean(condition)`. Never place `skipToken` inside a nested placeholder payload or coerce required IDs to `''`.
- When prefetch and render use the same request, extract local query options or a query-options atom so `prefetchQuery` and `useQuery`/`atomWithQuery` share the exact options.
- For custom query or mutation functions, wrap options with TanStack `queryOptions(...)` or `mutationOptions(...)`.
- Do not extract generated `queryOptions(...)` into a helper solely to share input construction; extract only when prefetch/render must share exact options or the helper owns real domain behavior.
- Avoid pass-through hooks and thin `web/service/use-*` wrappers that only rename generated options. Keep feature hooks for real orchestration, workflow state, or shared domain behavior.
- Put shared cache behavior in `createTanstackQueryUtils(...experimental_defaults...)`. Component or atom callbacks may handle local toasts, closing dialogs, and navigation, but should not replace shared invalidation or patch shared server state locally.
- For overlays that may open heavier secondary content, prefetch from the trigger/menu open event with `queryClient.prefetchQuery(queryOptions)` when `onOpenChange` is available. Do not mount hidden subscribers just to warm cache.
- Do not use deprecated `useInvalid` or `useReset`.
- Prefer `mutate(...)`; use `mutateAsync(...)` only when Promise semantics are required, and wrap awaited calls in `try/catch`.
