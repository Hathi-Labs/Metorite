/**
 * Task 3976 fence: a refused "Make live" says why in the confirm strip.
 * The runner has no DOM. Each flow runs through `runRollback` and the same
 * reducer as the page, and the strip renders to a string.
 *
 * Mutation 1: return `rollbackFailedLine` for a 409. "shows the 409 detail" turns red.
 * Mutation 2: return null for each non-ok answer. "shows the fallback line" turns red.
 * Mutation 3: drop `error: null` from "send". "on a new try" turns red.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import RollbackStrip from "../components/RollbackStrip";
import {
  ROLLBACK_IDLE,
  rollbackFailedLine,
  rollbackReducer,
  runRollback,
  type RollbackEvent,
  type RollbackState,
} from "./rollback";

const DETAIL =
  "Version 2 cannot go live: its admin review is pending. Only an approved " +
  "version, or one that needed no review, can go live.";

const answer = (status: number, body: unknown) =>
  new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
  });

/** A strip as the page holds it, with the version `asked`. */
function strip(asked = 2) {
  let state: RollbackState = ROLLBACK_IDLE;
  const dispatch = (event: RollbackEvent) => {
    state = rollbackReducer(state, event);
  };
  dispatch({ type: "ask", version: asked });
  const refresh = vi.fn(async () => {});
  /** One Confirm press, with `server` in the place of the browser's fetch. */
  const press = (
    server: (url: string, init: RequestInit) => Promise<Response>
  ) => {
    vi.stubGlobal("fetch", server);
    return runRollback({
      slug: "vendor list",
      version: asked,
      dispatch,
      refresh,
    });
  };
  return { get: () => state, dispatch, refresh, press };
}

const refuse = async () => answer(409, { detail: DETAIL });

/** The text of the alert in the strip, or null when it draws none. */
function alertText(state: RollbackState): string | null {
  const html = renderToStaticMarkup(
    createElement(RollbackStrip, {
      version: state.version ?? 0,
      busy: state.busy,
      error: state.error,
      onConfirm: () => undefined,
      onCancel: () => undefined,
    })
  );
  const hit = html.match(/<div role="alert"[^>]*>.*?<span[^>]*>(.*?)<\/span>/);
  return hit ? hit[1] : null;
}

afterEach(() => vi.unstubAllGlobals());

describe("a refused Make live", () => {
  it("shows the 409 detail and keeps the strip open", async () => {
    const s = strip();
    const send = vi.fn(refuse);
    await s.press(send);
    expect(send).toHaveBeenCalledWith("/api/apps/vendor%20list/rollback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: 2 }),
    });
    expect(s.get()).toEqual({ version: 2, busy: false, error: DETAIL });
    expect(alertText(s.get())).toBe(DETAIL);
    expect(s.refresh).not.toHaveBeenCalled();
  });

  it.each([
    ["a 500", answer(500, { detail: "psycopg: deadlock detected" })],
    ["a 403", answer(403, { detail: "Forbidden" })],
    ["a 404", answer(404, { detail: "Version not found" })],
    ["a proxy 502", answer(502, { error: "TypeError: fetch failed" })],
    ["a 409 with no detail", answer(409, {})],
    ["a 409 with an object detail", answer(409, { detail: { error: "x" } })],
    ["a 409 that is not JSON", answer(409, "<html>Conflict</html>")],
    ["a 409 with a null body", answer(409, "null")],
  ])("shows the fallback line for %s", async (_name, res) => {
    const s = strip();
    await s.press(async () => res);
    expect(s.get()).toEqual({
      version: 2,
      busy: false,
      error: rollbackFailedLine(2),
    });
    expect(alertText(s.get())).toBe("Could not make v2 live. Try again.");
  });

  it("shows the fallback line for a network failure", async () => {
    const s = strip();
    await s.press(async () => {
      throw new TypeError("Failed to fetch");
    });
    expect(s.get().error).toBe(rollbackFailedLine(2));
    expect(s.get().busy).toBe(false);
  });
});

describe("the message clears", () => {
  it("on a new try, while the request is in flight", async () => {
    const s = strip();
    await s.press(refuse);
    let release: (res: Response) => void = () => undefined;
    const second = s.press(
      () => new Promise<Response>((resolve) => (release = resolve))
    );
    expect(s.get()).toEqual({ version: 2, busy: true, error: null });
    expect(alertText(s.get())).toBeNull();
    release(answer(409, { detail: DETAIL }));
    await second;
    expect(s.get().error).toBe(DETAIL);
  });

  it("on Cancel, and the next ask starts clean", async () => {
    const s = strip();
    await s.press(refuse);
    s.dispatch({ type: "cancel" });
    expect(s.get()).toEqual(ROLLBACK_IDLE);
    s.dispatch({ type: "ask", version: 2 });
    expect(s.get().error).toBeNull();
  });

  it("on a move to another version", async () => {
    const s = strip();
    await s.press(refuse);
    s.dispatch({ type: "ask", version: 1 });
    expect(s.get()).toEqual({ version: 1, busy: false, error: null });
  });

  it("on success, and the strip closes", async () => {
    const s = strip();
    await s.press(refuse);
    await s.press(async () => answer(200, { version: 2 }));
    expect(s.get()).toEqual(ROLLBACK_IDLE);
    expect(s.refresh).toHaveBeenCalledTimes(1);
  });

  it("stays clear when the reload fails after a success", async () => {
    const s = strip();
    s.refresh.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await s.press(async () => answer(200, { version: 2 }));
    expect(s.get()).toEqual(ROLLBACK_IDLE);
  });
});

describe("a late answer", () => {
  it("puts no message under another version", async () => {
    const s = strip(2);
    let release: (res: Response) => void = () => undefined;
    const first = s.press(
      () => new Promise<Response>((resolve) => (release = resolve))
    );
    s.dispatch({ type: "ask", version: 1 });
    release(answer(409, { detail: DETAIL }));
    await first;
    expect(s.get()).toEqual({ version: 1, busy: false, error: null });
  });

  it("opens no strip after Cancel", async () => {
    const s = strip(2);
    const first = s.press(refuse);
    s.dispatch({ type: "cancel" });
    await first;
    expect(s.get()).toEqual(ROLLBACK_IDLE);
  });
});

describe("the run page", () => {
  const page = readFileSync(
    new URL("../[slug]/page.tsx", import.meta.url),
    "utf-8"
  );

  it("sends through runRollback and draws the reason in the strip", () => {
    expect(page).toContain("runRollback({");
    expect(page).toContain("error={rollback.error}");
    expect(page).not.toContain("/rollback`");
    expect(page).not.toContain("confirmVersion");
  });

  it("clears on Cancel and on an outside click", () => {
    expect(page.match(/dispatchRollback\(\{ type: "cancel" \}\)/g)).toHaveLength(2);
  });
});
