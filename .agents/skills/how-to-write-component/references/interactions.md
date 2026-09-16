# Interactions

Read only the sections relevant to the component change. Repository paths in this guide are relative to the Dify root.

## Keyboard Shortcuts

- Distinguish application commands from local keyboard semantics before choosing an API. Use `@tanstack/react-hotkeys` for application commands. Keep menu navigation, dialog Escape handling owned by a primitive, editor commands, and other widget-scoped ARIA interactions in their local component or primitive.
- Use `useHotkey` or `useHotkeys` for registered commands. For a command intentionally owned by an existing `onKeyDown`, use `matchesKeyboardEvent` instead of hand-written `metaKey` / `ctrlKey` parsing or a second global listener.
- Define a reusable string command with `satisfies Hotkey` and an object-form command with `satisfies RawHotkey`. Reserve `RegisterableHotkey` for API boundaries that intentionally accept either form. A one-time inline literal passed directly to TanStack is already type-checked; extract it when registration, display, metadata, or another production consumer needs the same source.
- Keep registered hotkeys distinct from held keys and display-only accelerators. Use `IndividualKey` with `useKeyHold` for held-key interactions, and use an explicitly named `displayKey` for local widget accelerators that are not registered `Hotkey` values.
- Keep registration and keycap/menu display derived from one canonical command. Do not maintain a hotkey string beside a separate `['Mod', ...]` display array.
- Keep a single-owner command constant in its owning component. Create a feature-local `hotkeys.ts` only when multiple production files consume the same command. Keep a dedicated definitions/registry module when a feature owns a real command system with IDs, metadata, alternate bindings, and centralized registration. Tests do not count as another production owner, and file-name uniformity alone is not a reason to extract.
- Make scope and availability explicit. Use `enabled` for business or surface lifecycle, `ignoreInputs` for whether input-like elements may trigger the command, and `target` when the command belongs to a concrete DOM subtree. Global application commands may use the document target; inline editors and composed overlays should prefer the actual editor or Base UI Popup ref when that owner is exposed.
- Put a scoped ref on the real behavior owner. Do not add a wrapper DOM element solely to obtain a hotkey target. If a shared overlay convenience component hides the Popup ref, either rely on its modal lifecycle/focus boundary when that is sufficient or design the primitive API separately; do not create a fake owner at the call site.
- Set `preventDefault` and `stopPropagation` according to the existing product behavior and browser interaction. Do not silently accept TanStack defaults when migrating from another listener if that changes typing, submission, or propagation semantics.
- Test observable command behavior, disabled/input/target scope, and the shared registration/display contract at the owning feature boundary. Prefer partial mocks that retain TanStack formatting and matching behavior when a registration boundary must be isolated.

## Boundaries And Overlays

- Use the first level below a page or tab to organize independent page sections when it adds structure or the root folder becomes noisy. This layer is layout/semantic first, not automatically the data owner.
- Treat component names, semantic roles, and user- or design-marked visual regions as boundary constraints. Keep adjacent UI as a sibling owner or introduce a correctly named broader owner.
- Keep cohesive forms, menu bodies, and one-off helpers local unless they need their own state, reuse, or semantic boundary.
- Separate hidden secondary surfaces from the trigger's main flow. For dialogs, dropdowns, popovers, and similar branches, extract a small local component when hidden content would obscure the parent.
- Preserve composability by separating behavior ownership from placement ownership: an action can own trigger/open/menu content while the caller owns slots, offsets, and alignment.
- When a dialog, dropdown, or popover accepts controlled `open`, mount it unconditionally unless unmounting is required for performance or reset semantics. Use keyed scope or local state reset instead of `{open && <Surface />}` wrappers.
- When opening a dialog from a menu item, keep the menu and dialog as sibling surfaces. Let the menu command open the dialog, and mount the dialog outside menu popup content.
- For dialogs and alert dialogs, keep the root responsible for `open` wiring and put query/mutation hooks inside the content component when work should mount only after the overlay opens.
- Prefer uncontrolled overlay roots when the library can own open state. Use `onOpenChange` for side effects and CSS/data selectors for open-state styling.
- Avoid wrapper DOM unless it provides layout, semantics, accessibility, state ownership, or library integration. Avoid shallow wrappers, hook-to-props adapters, layout-only render props, children pass-through wrappers, and prop renaming unless they add real behavior or a real boundary.

## Effects, Navigation, And Performance

- Use Effects only to synchronize with external systems. Do not use Effects to transform props/state for rendering, handle user actions, copy state, reset state from props, or fetch data.
- For forms initialized from query data, prefer keyed remounts or surface-entry atom hydration over Effects that copy query data into form state.
- Prefer framework data APIs or TanStack Query for data fetching.
- Prefer `Link` for normal navigation. Use router APIs only for command-flow side effects such as mutation success, guarded redirects, or form submission.
- Before using `memo`, move changing state down to the smallest component that uses it. If state must wrap stable content, lift the stable content up and pass it as `children`.
- Avoid `memo`, `useMemo`, and `useCallback` unless there is a clear performance reason.
