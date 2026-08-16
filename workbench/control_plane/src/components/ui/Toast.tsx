"use client";

/**
 * Toast — THE confirmation channel (WS-27ak item 3, D-PM-15).
 *
 * A Metorite wrapper over `@base-ui/react`'s `toast`. **This file and
 * `Modal.tsx` are the only places in `src/` allowed to import that library**
 * (conformance rule 8): call sites import `useToast`, never the substrate, or
 * the library's own defaults quietly become a second design system —
 * D-PM-15 condition 1, and condition 2 is why there is exactly one substrate to
 * wrap.
 *
 * ## Why this exists
 *
 * Measured across the tree for §11's parity table: **zero** `useToast`, **zero**
 * `<Toaster>`, **zero** `ToastProvider`. Not "thin" — *absent*. A mutation in
 * `/projects` either reported inline, on a surface the reader may have scrolled
 * past, or reported nothing at all; success reported nothing anywhere. That is
 * the gap, and it is the reason Toast was promoted above Skeleton in WS-27ak's
 * order.
 *
 * ## The API, and the one call that is the point of the ticket
 *
 *     const toast = useToast();
 *
 *     await toast.promise(projectsApi.patchTask(id, patch), {
 *       key: `task-status:${id}`,
 *       loading: "Changing status…",
 *       success: (task) => `Moved to ${nameOf(task.status_id)}`,
 *       error: "Couldn't change the status",
 *       errorAction: { label: "Retry", onClick: retry },
 *     });
 *
 * **One call, one toast, mutated in place through `loading → success | error`.**
 * Not three toasts for one operation, which is what a call site hand-rolling
 * `show()` three times produces and what `e2e/toast.spec.ts` asserts against by
 * counting elements rather than by reading the last one.
 *
 * ⚠️ **`promise()` re-throws.** The returned promise settles exactly as the one
 * handed in did, so a call site can still revert its optimistic state — but
 * `void toast.promise(…)` on a rejecting promise is an unhandled rejection.
 * `await` it inside the `try/catch` the call site already has. All three wired
 * call sites do.
 *
 * ⚠️ **Two-argument `then`, never `.then().catch()`.** The latter also catches a
 * throw from the *success* handler, which would turn a bug in a message
 * resolver into an error toast about a mutation that succeeded — a lie of
 * exactly the kind `lib/toast.ts` exists to prevent.
 *
 * ## What lives in `src/lib/toast.ts` and why none of it is here
 *
 * `vitest.config.ts` here is `environment: "node"` with
 * `include: ["src/**‍/*.test.ts"]` — **`.tsx` is not collected and there is no
 * DOM runner** — so a decision written in this file would have no fence. Message
 * resolution, the readable-rejection rule, the dedupe key and the
 * `loading → success | error` machine are therefore all pure functions next
 * door, unit-tested in `src/lib/toast.test.ts`. This file translates a
 * `ToastPatch` into the substrate's options and draws it. Same split as
 * `ControlLink`/`controlLink.ts` and `LayoutBoundary`/`layoutBoundary.ts`.
 *
 * ## Dedupe is the id, not a registry
 *
 * `toastIdFor(key)` namespaces a caller key into a stable toast id, and Base
 * UI's store (`toast/store.mjs`, `addToast`) **updates in place when handed an
 * id it already holds** and removes-then-re-adds when that toast is already
 * dismissing. So the whole of "fire the same keyed toast twice and it updates
 * rather than stacks" is one pure function plus the substrate — there is no
 * second map of live toasts to keep in step, which is the thing that would go
 * stale.
 *
 * ## Accessibility, and where each half comes from
 *
 * * `priority: "low"` (loading, success) is announced by the viewport's own
 *   `aria-live="polite"` (`toast/viewport/ToastViewport.mjs:181`).
 * * `priority: "high"` (**errors only**) additionally renders a
 *   visually-hidden `role="alert"` copy — i.e. assertive (`:219`). Errors are
 *   the only thing that earns an interruption.
 * * ⚠️ **The price of `priority: "high"`, measured, not assumed:** the same
 *   flag makes Base UI stamp `aria-hidden="true"` on the VISIBLE toast until
 *   the viewport takes focus (`ToastRoot.mjs:418`) — deliberate, because the
 *   assertive copy is already saying the words and a reader should not hear
 *   them twice. The cost is that an error's **`Retry` and dismiss controls are
 *   out of the a11y tree** while unfocused; F6 is the way in, and the last
 *   test in `e2e/toast.spec.ts` walks that path. It is also why two tests
 *   there address toast controls structurally: `getByRole` cannot see them.
 * * **Dismissible from the keyboard, two ways.** Escape while a toast has focus
 *   closes that toast (`toast/root/ToastRoot.mjs:374`), and F6 moves focus into
 *   the viewport from anywhere on the page (`ToastViewport.mjs`), so a toast is
 *   reachable without a pointer and without Tabbing through the whole document.
 *   Every toast also carries a real dismiss `<Button>`.
 *
 * ## Theming
 *
 * Semantic tokens only (`bg-card`, `border-border`, `text-success`,
 * `text-destructive`), icons through `<Icon name>` with names that are mapped
 * for every pack in `lib/theme/icon-data/registry.json`, and every control is a
 * `<Button>` so it carries `.cc-control`. The layer is `z-50`, the value
 * `DESIGN_SYSTEM.md` §4a names — **not** a `z-[60]` invented to win a fight
 * with `Modal`.
 *
 * ⚠️ **Do not raise a toast from inside a `Modal` in this slice.** Base UI's
 * `markOthers` sets `aria-hidden="true"` on the dialog portal's siblings, and
 * the toast portal is one of them — so a toast raised under an open dialog is
 * hidden from assistive tech, and at the same `z-50` it stacks by mount order,
 * i.e. behind a dialog that was portalled later. That is why the three wired
 * call sites are all outside dialogs. Closing it means the viewport opting out
 * of `markOthers`, which is a board decision, not a call site's
 * (`project_management_app.md` §11.32).
 *
 * ## Retirement note
 *
 * `app/tasks/components/UndoToast.tsx` is an app-local toast with its own
 * timer, its own markup and no live region — the `/tasks` half of the same
 * primitive. It is owed a retirement onto this one, an app ticket rather than
 * this slice, exactly as WS-27ak recorded for the email `Modal` and WS-27bd for
 * the email `ContextMenu`. Its call sites were left alone. Do not add a second
 * consumer to it.
 */

import { Toast as BaseToast } from "@base-ui/react/toast";
import { createContext, useContext, useMemo } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import {
  type ToastAction,
  type ToastPatch,
  type ToastPromiseSpec,
  type ToastVariant,
  loadingPatch,
  priorityFor,
  settlePatch,
  timeoutFor,
  toastIdFor,
} from "@/lib/toast";

export type { ToastAction, ToastPromiseSpec, ToastVariant };

/** Custom payload carried per toast. Base UI hands it back on `toast.data`. */
interface ToastData {
  action?: ToastAction;
}

/** The glyph per variant. Every name is mapped for all three icon packs. */
const ICONS: Record<ToastVariant, string> = {
  loading: "Loader2",
  success: "CheckCircle2",
  error: "AlertCircle",
};

/** The tone per variant — semantic state tokens, never a palette class. */
const TONES: Record<ToastVariant, string> = {
  loading: "animate-spin text-muted-foreground",
  success: "text-success",
  error: "text-destructive",
};

/** A `ToastPatch` as the substrate's option bag. */
function toOptions(patch: ToastPatch) {
  return {
    // Base UI's `type` is what draws `data-type` and what suppresses the
    // auto-dismiss timer while a toast is `loading`.
    type: patch.variant,
    title: patch.title,
    // ⚠️ Explicit keys, never omitted ones. The store MERGES
    // (`{...prevToast, ...updates}`), so leaving `description` out of the
    // success patch would keep the failure's detail line on screen underneath
    // a "Saved" — the merge is what makes an omitted key a lie.
    description: patch.description,
    priority: patch.priority,
    timeout: patch.timeout,
    data: { action: patch.action } satisfies ToastData,
  };
}

export interface ToastApi {
  /**
   * Run a promise and mutate ONE toast through `loading → success | error`.
   *
   * Returns a promise that settles exactly as the one passed in, rejection
   * included — see this file's header before writing `void toast.promise(…)`.
   */
  promise<T>(work: Promise<T>, spec: ToastPromiseSpec<T>): Promise<T>;
  /** A toast with no promise behind it. Same dedupe rules. */
  show(spec: {
    key?: string;
    variant?: ToastVariant;
    title: string;
    description?: string;
    action?: ToastAction;
  }): string;
  /** Close one keyed toast. Silent when that key is not on screen. */
  dismiss(key: string): void;
  /** Close everything. For a route change, not for a mutation. */
  dismissAll(): void;
}

/**
 * The API a caller gets when no provider is above it.
 *
 * ⚠️ Not a convenience — a **hazard control**. `BaseToast.useToastManager()`
 * throws outside `<Toast.Provider>`, and the caller of a toast is a mutation
 * handler: a component rendered in a tree without the provider would crash on
 * the click rather than merely fail to confirm. `promise` here still returns
 * the promise it was handed, so the mutation itself is untouched.
 *
 * The arm is unreachable in the product, and that is fenced rather than
 * asserted: `conformance.test.ts` rule 8 checks `app/layout.tsx` mounts
 * `ToastProvider`. Without that check, deleting the provider would turn every
 * confirmation in the app off with no test going red — the silent failure this
 * whole primitive exists to end, arriving through its own front door.
 */
const UNMOUNTED: ToastApi = {
  promise: (work) => work,
  show: () => "",
  dismiss: () => undefined,
  dismissAll: () => undefined,
};

const ToastContext = createContext<ToastApi>(UNMOUNTED);

/**
 * The app-wide provider. Mounted once, in `src/app/layout.tsx`, above
 * everything — a toast must outlive the surface that raised it (a panel that
 * closes on save is the ordinary case), so it cannot live inside a page.
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  return (
    <BaseToast.Provider limit={3}>
      <ToastBridge>{children}</ToastBridge>
      <ToastViewport />
    </BaseToast.Provider>
  );
}

/** Read the API out of a hook, hand it down as a context value. */
export function useToast(): ToastApi {
  return useContext(ToastContext);
}

/**
 * Sits between the substrate's provider and the app, so `useToast()` is a
 * plain context read and needs no `try`/branch for the un-provided case.
 *
 * ⚠️ **This component re-renders on every toast change** — `useToastManager()`
 * subscribes to the store's `toasts` — and that is deliberately harmless: the
 * `children` element it renders is a prop of a parent that did *not* re-render,
 * so React bails out on the identical element, and the context value is
 * memoized on the store's stable methods, so no consumer re-renders either.
 * Move the `useToastManager()` call up into `ToastProvider` and the whole app
 * re-renders once per toast.
 */
function ToastBridge({ children }: { children: React.ReactNode }) {
  const manager = BaseToast.useToastManager<ToastData>();
  // `manager` is re-memoized on every toast change, but the METHODS on it are
  // stable class properties of the store — so an API memoized on them is stable
  // across renders and safe in an effect's dependency list. Memoizing on
  // `manager` itself would hand every consumer a new object per toast.
  const { add, close } = manager;

  const api = useMemo<ToastApi>(
    () => ({
      promise<T>(work: Promise<T>, spec: ToastPromiseSpec<T>): Promise<T> {
        const id = add({
          id: toastIdFor(spec.key),
          ...toOptions(loadingPatch(spec.loading)),
        });
        // `add` rather than `update` on settlement, deliberately: `add` with a
        // known id updates in place when the toast is still up AND re-raises it
        // when the reader has already dismissed the loading toast, whereas
        // `updateToast` drops updates for a toast that is `ending`
        // (`store.mjs`) — which would swallow the failure of a slow request
        // somebody dismissed while waiting. Either way it is one toast: the id
        // is the same.
        const settle = (patch: ToastPatch) => add({ id, ...toOptions(patch) });
        return work.then(
          (value) => {
            settle(settlePatch({ ok: true, value }, spec));
            return value;
          },
          (reason: unknown) => {
            settle(settlePatch({ ok: false, reason }, spec));
            throw reason;
          },
        );
      },

      show({ key, variant = "success", title, description, action }) {
        return add({
          id: toastIdFor(key),
          // `priorityFor`/`timeoutFor`, never a ternary here: whether a message
          // interrupts a screen-reader user, and how long a failure stays on
          // screen, are decided in ONE place (`lib/toast.ts`) for the promise
          // form and this one alike. A second copy is how the two forms end up
          // announcing differently.
          ...toOptions({
            variant,
            title,
            description,
            priority: priorityFor(variant),
            timeout: timeoutFor(variant),
            action,
          }),
        });
      },

      dismiss: (key: string) => close(toastIdFor(key)),
      dismissAll: () => close(),
    }),
    [add, close],
  );

  return <ToastContext.Provider value={api}>{children}</ToastContext.Provider>;
}

function ToastViewport() {
  const { toasts, close } = BaseToast.useToastManager<ToastData>();
  return (
    <BaseToast.Portal>
      {/* `pointer-events-none` on the container so an empty corner of the
          screen is not a dead zone over the board underneath; each toast turns
          them back on for itself. `flex-col-reverse` because the store keeps
          toasts newest-first and this viewport is anchored to the bottom — the
          newest belongs nearest the corner it grew from. */}
      <BaseToast.Viewport className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col-reverse gap-2 outline-none">
        {toasts.map((toast) => {
          const variant = (toast.type ?? "loading") as ToastVariant;
          const action = toast.data?.action;
          return (
            <BaseToast.Root
              key={toast.id}
              toast={toast}
              data-cc-toast={variant}
              className="pointer-events-auto flex w-full items-start gap-2 rounded-lg border border-border bg-card p-3 shadow-lg outline-none transition-opacity duration-150 data-[ending-style]:opacity-0 data-[limited]:hidden data-[starting-style]:opacity-0"
            >
              <Icon
                name={ICONS[variant] ?? ICONS.loading}
                size={16}
                className={`mt-px shrink-0 ${TONES[variant] ?? TONES.loading}`}
              />
              <div className="min-w-0 flex-1">
                {/* No children: the parts fall back to the toast's own `title`
                    and `description`, and render nothing when those are absent
                    — so an empty `<h2>`/`<p>` never lands in the live region. */}
                <BaseToast.Title className="text-xs font-medium text-foreground" />
                <BaseToast.Description className="mt-0.5 text-[11px] text-muted-foreground" />
              </div>
              {action ? (
                <Button
                  variant="secondary"
                  size="sm"
                  className="shrink-0"
                  // Closed BEFORE the action runs. A "Retry" re-fires under the
                  // same key, and closing afterwards would dismiss the toast the
                  // retry had just raised.
                  onClick={() => {
                    close(toast.id);
                    action.onClick();
                  }}
                >
                  {action.label}
                </Button>
              ) : null}
              <Button
                variant="ghost"
                size="icon-xs"
                icon="X"
                aria-label="Dismiss notification"
                className="shrink-0"
                onClick={() => close(toast.id)}
              />
            </BaseToast.Root>
          );
        })}
      </BaseToast.Viewport>
    </BaseToast.Portal>
  );
}
