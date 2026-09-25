/**
 * The toast rules — the fence for WS-27ak(3)'s pure half.
 *
 * `src/components/ui/Toast.tsx` cannot be tested here at all (`vitest.config.ts`
 * is `environment: "node"` and collects only `src/**‍/*.test.ts`, so no `.tsx`
 * is even loaded), which is why every decision worth getting wrong lives in
 * `src/lib/toast.ts`. What a browser has to answer instead — one operation
 * produces one toast rather than three, a keyed re-fire updates rather than
 * stacks — is `e2e/toast.spec.ts`.
 *
 * The load-bearing case is `"a rejection can never produce a success toast"`:
 * it runs the whole table of rejection shapes through the same function the
 * component calls and asserts the variant, because the defect class this
 * primitive exists to remove is a "Saved" that appears on a failure.
 */

import { describe, expect, it } from "vitest";

import {
  ERROR_TIMEOUT,
  GENERIC_FAILURE,
  KEY_PREFIX,
  LOADING_TIMEOUT,
  MAX_REASON,
  SUCCESS_TIMEOUT,
  type ToastOutcome,
  loadingPatch,
  priorityFor,
  readableReason,
  resolveAction,
  resolveMessage,
  settlePatch,
  showTimeout,
  timeoutFor,
  toastIdFor,
} from "./toast";

/**
 * Every shape a rejection actually arrives in around here.
 *
 * `ProjectsApiError` (an `Error` carrying the gateway's `detail` on `.message`)
 * is the common one; a bare `TypeError: fetch failed` is what a stopped gateway
 * produces — `/projects` renders that today; a plain `{detail}` is a 422 body
 * somebody parsed before throwing; and the rest are the ones a careless
 * `throw` produces, each of which has to degrade to something readable rather
 * than to `"[object Object]"` on screen.
 */
const REJECTIONS: Array<[name: string, reason: unknown]> = [
  ["an Error with a message", new Error("Status is not valid for this project")],
  ["a TypeError from fetch", new TypeError("fetch failed")],
  ["a FastAPI detail body", { detail: "Tag name already exists" }],
  ["an object with a message", { message: "Request failed (500)" }],
  ["a bare string", "the network went away"],
  ["an empty string", ""],
  ["whitespace only", "   \n "],
  ["the object stringification", "[object Object]"],
  ["undefined", undefined],
  ["null", null],
  ["a number", 500],
  ["a bare object", {}],
  ["an Error with no message", new Error()],
];

describe("readableReason", () => {
  it("reads the sentence a person wrote, whatever wrapped it", () => {
    expect(readableReason(new Error("Status is not valid"))).toBe("Status is not valid");
    expect(readableReason({ detail: "Tag name already exists" })).toBe(
      "Tag name already exists",
    );
    expect(readableReason({ message: "Request failed (500)" })).toBe(
      "Request failed (500)",
    );
    expect(readableReason("  the network went away  ")).toBe("the network went away");
  });

  it("prefers `detail` over `message` on the same object", () => {
    // FastAPI's `detail` is the sentence somebody wrote for a person to read;
    // a `message` beside it is the transport's own description of the failure.
    expect(
      readableReason({ detail: "That tag is on 12 tasks", message: "Bad Request" }),
    ).toBe("That tag is on 12 tasks");
  });

  it("refuses the strings that are present and say nothing", () => {
    for (const junk of ["", "   ", "[object Object]", "undefined", "null", "{}", "Error"]) {
      expect(readableReason(junk), `"${junk}" was treated as readable`).toBeNull();
    }
    expect(readableReason(new Error("[object Object]"))).toBeNull();
    expect(readableReason(new Error())).toBeNull();
  });

  it("refuses what is not a string and not a carrier of one", () => {
    expect(readableReason(undefined)).toBeNull();
    expect(readableReason(null)).toBeNull();
    expect(readableReason(500)).toBeNull();
    expect(readableReason(false)).toBeNull();
    expect(readableReason({})).toBeNull();
    expect(readableReason({ detail: { nested: "no" } })).toBeNull();
  });

  it("truncates rather than drops, because a clipped reason still says more", () => {
    // A BFF proxy failure arrives as an HTML error page on `.message`. Dropping
    // it would fall back to the generic line and lose the only clue there is.
    const huge = `${"the gateway said no ".repeat(40)}END`;
    const read = readableReason(huge);
    expect(read).not.toBeNull();
    // `<=` rather than `===`: the cut is trimmed before the ellipsis, so a
    // slice landing on a space is one character shorter. What must hold is
    // that it is bounded and that it says where it stopped.
    expect(read!.length).toBeLessThanOrEqual(MAX_REASON + 1);
    expect(read!.length).toBeGreaterThan(MAX_REASON - 5);
    expect(read!.endsWith("…")).toBe(true);
    expect(huge.startsWith(read!.slice(0, 20))).toBe(true);
  });

  it("leaves a message exactly at the limit alone", () => {
    const exact = "x".repeat(MAX_REASON);
    expect(readableReason(exact)).toBe(exact);
  });
});

describe("dedupe is the id", () => {
  it("the same key is the same toast, a different key is a different one", () => {
    expect(toastIdFor("task-status:7")).toBe(toastIdFor("task-status:7"));
    expect(toastIdFor("task-status:7")).not.toBe(toastIdFor("task-status:8"));
  });

  it("no key means no id, i.e. mint a fresh toast every time", () => {
    // The substrate generates one when `id` is absent. Returning a constant
    // here instead would make every unkeyed toast in the app one toast.
    expect(toastIdFor(undefined)).toBeUndefined();
  });

  it("caller keys cannot collide with the substrate's own generated ids", () => {
    // Base UI mints `toast-<n>`. Without the namespace, a caller key of
    // "toast-3" could land on an unrelated toast raised somewhere else.
    expect(toastIdFor("toast-3")).toBe(`${KEY_PREFIX}toast-3`);
    expect(toastIdFor("toast-3")).not.toBe("toast-3");
  });
});

describe("priority is the live region", () => {
  it("only an error interrupts", () => {
    // `high` renders the viewport's visually-hidden `role="alert"` copy, i.e.
    // assertive; `low` is announced by its `aria-live="polite"`. A "Saved" that
    // talks over a screen-reader user mid-sentence is how the channel gets
    // switched off.
    expect(priorityFor("error")).toBe("high");
    expect(priorityFor("success")).toBe("low");
    expect(priorityFor("loading")).toBe("low");
  });
});

describe("timeouts", () => {
  it("a failure stays until it is dismissed", () => {
    // The one that matters: five seconds is exactly long enough to miss while
    // looking at the thing you were editing, and a failure nobody saw was
    // never reported.
    expect(timeoutFor("error")).toBe(0);
    expect(ERROR_TIMEOUT).toBe(0);
  });

  it("a loading toast is closed by its settlement, never by a clock", () => {
    expect(timeoutFor("loading")).toBe(0);
    expect(LOADING_TIMEOUT).toBe(0);
  });

  it("a success goes away on its own", () => {
    expect(timeoutFor("success")).toBe(SUCCESS_TIMEOUT);
    expect(SUCCESS_TIMEOUT).toBeGreaterThan(0);
  });

  it("a show() may set a success's window, and only a success's", () => {
    // My Tasks' undo closes on its own window (continuity P3).
    expect(showTimeout("success", 7_000)).toBe(7_000);
    expect(showTimeout("success")).toBe(SUCCESS_TIMEOUT);
    expect(showTimeout("success", 0)).toBe(SUCCESS_TIMEOUT);
    // A failure still stays until dismissed, whatever the caller asks.
    expect(showTimeout("error", 7_000)).toBe(ERROR_TIMEOUT);
    expect(showTimeout("loading", 7_000)).toBe(LOADING_TIMEOUT);
  });
});

describe("the loading → success | error machine", () => {
  const spec = {
    loading: "Changing status…",
    success: "Status changed",
    error: "Couldn't change the status",
  };

  it("starts as one loading toast that will not time out", () => {
    expect(loadingPatch("Changing status…")).toEqual({
      variant: "loading",
      title: "Changing status…",
      priority: "low",
      timeout: 0,
    });
  });

  it("a rejection can never produce a success toast", () => {
    // ⚠️ THE assertion. Not "the error path works" — that a rejection has no
    // route to `success` for ANY shape a rejection arrives in. A table, not one
    // case, because the version of this bug that ships is the one whose
    // rejection did not look like the example.
    for (const [name, reason] of REJECTIONS) {
      const patch = settlePatch({ ok: false, reason }, spec);
      expect(patch.variant, `${name} produced a ${patch.variant} toast`).toBe("error");
      expect(patch.title, `${name} produced an empty toast`).not.toBe("");
      expect(patch.priority).toBe("high");
      expect(patch.timeout).toBe(0);
    }
  });

  it("a resolution always produces exactly a success toast", () => {
    for (const value of [undefined, null, 0, "", false, {}, { id: "t1" }]) {
      const patch = settlePatch({ ok: true, value } as ToastOutcome<unknown>, spec);
      expect(patch.variant).toBe("success");
      expect(patch.priority).toBe("low");
      expect(patch.timeout).toBe(SUCCESS_TIMEOUT);
    }
  });

  it("the success message may be derived from what the promise settled with", () => {
    const patch = settlePatch(
      { ok: true, value: { title: "Ship the thing" } },
      { loading: "…", success: (task: { title: string }) => `Saved “${task.title}”` },
    );
    expect(patch.title).toBe("Saved “Ship the thing”");
  });

  it("the call site's sentence names what failed; the rejection says why", () => {
    const patch = settlePatch(
      { ok: false, reason: new Error("Status is not valid for this project") },
      spec,
    );
    expect(patch.title).toBe("Couldn't change the status");
    expect(patch.description).toBe("Status is not valid for this project");
  });

  it("with no sentence, the rejection IS the message", () => {
    const patch = settlePatch(
      { ok: false, reason: { detail: "Tag name already exists" } },
      { loading: "…", success: "done" },
    );
    expect(patch.title).toBe("Tag name already exists");
    expect(patch.description).toBeUndefined();
  });

  it("an unreadable rejection falls back to the generic line, never to silence", () => {
    const patch = settlePatch(
      { ok: false, reason: {} },
      { loading: "…", success: "done" },
    );
    expect(patch.title).toBe(GENERIC_FAILURE);
    expect(patch.description).toBeUndefined();
  });

  it("a sentence survives an unreadable rejection, with nothing invented under it", () => {
    const patch = settlePatch({ ok: false, reason: undefined }, spec);
    expect(patch.title).toBe("Couldn't change the status");
    expect(patch.description).toBeUndefined();
  });

  it("the error sentence may itself be derived from the rejection", () => {
    const patch = settlePatch(
      { ok: false, reason: { status: 409 } },
      {
        loading: "…",
        success: "done",
        error: (reason) =>
          (reason as { status: number }).status === 409
            ? "Somebody else changed it first"
            : "Couldn't save",
      },
    );
    expect(patch.title).toBe("Somebody else changed it first");
  });

  it("the settled patch clears the previous one's description explicitly", () => {
    // The substrate MERGES updates onto the toast it already holds, so a
    // success patch that merely omitted `description` would leave the failure's
    // detail line on screen underneath a "Saved". `undefined` is the key being
    // present, which is what overwrites.
    const patch = settlePatch({ ok: true, value: 1 }, spec);
    expect("description" in patch).toBe(true);
    expect(patch.description).toBeUndefined();
  });
});

describe("the action slot", () => {
  it("carries at most one button, and may be derived from the outcome", () => {
    const undo = { label: "Undo", onClick: () => undefined };
    expect(resolveAction(undo, 1)).toBe(undo);
    expect(resolveAction((v: number) => (v > 0 ? undo : null), 1)).toBe(undo);
    expect(resolveAction((v: number) => (v > 0 ? undo : null), -1)).toBeUndefined();
    expect(resolveAction(undefined, 1)).toBeUndefined();
  });

  it("rides through settlement onto the toast it belongs to", () => {
    const retry = { label: "Retry", onClick: () => undefined };
    const undo = { label: "Undo", onClick: () => undefined };
    const spec = {
      loading: "…",
      success: "done",
      successAction: undo,
      errorAction: retry,
    };
    expect(settlePatch({ ok: true, value: 1 }, spec).action).toBe(undo);
    expect(settlePatch({ ok: false, reason: new Error("no") }, spec).action).toBe(retry);
  });

  it("a settlement with no action clears any the previous state drew", () => {
    // Same merge trap as the description: an omitted key leaves a stale
    // "Retry" button sitting on a toast that now says the save worked.
    const patch = settlePatch({ ok: true, value: 1 }, { loading: "…", success: "done" });
    expect("action" in patch).toBe(true);
    expect(patch.action).toBeUndefined();
  });
});

describe("resolveMessage", () => {
  it("takes a string as-is and calls a function with the value", () => {
    expect(resolveMessage("Saved", 1)).toBe("Saved");
    expect(resolveMessage((n: number) => `Saved ${n}`, 3)).toBe("Saved 3");
  });
});
