/**
 * The buttons of a reminder push (RX-4, `calendar_events_reminders.md` §5.9).
 *
 * `public/sw.js` runs against a fake worker scope, as `push.test.ts` does it.
 * A reminder that waits gets "Done" and "In 1 hour". A button posts to the
 * reminder routes of the BFF with the session, and a failed request opens the
 * chat card, so nothing is lost. A plain tap behaves as it always did.
 *
 * The BFF (`/api/projects/[...path]`) has no origin or CSRF check of its own.
 * The session cookie is `SameSite=Lax` (`sessionCookie.test.ts`), and a request
 * from the worker is same-site, so the cookie goes with it. A signed-out
 * request meets `proxy.ts`, which answers a 401 JSON for an `/api/` path.
 *
 * Mutations, run by hand (2026-11-02):
 *   - drop the `UUID.test(id)` check in `notificationOf`: "a bad id gets no buttons" fails;
 *   - give `reminder_unconfirmed` buttons: "only a reminder that waits" fails;
 *   - treat a 404 as done in `pressButton`: "a failed request opens the chat card" fails;
 *   - post `/resolve` for "snooze": "In 1 hour posts /snooze" fails;
 *   - let a plain tap call `fetch`: "a plain tap never calls the routes" fails;
 *   - drop `options.renotify = true`: "a check-back that replaces a fired reminder alerts" fails;
 *   - set `renotify` outside the `tag` branch: "a push with no tag" fails.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it, vi } from "vitest";

const ROOT = path.join(__dirname, "..", "..");
const ORIGIN = "https://app.metorite.test";
const RID = "6f1c2b9e-0000-4000-8000-00000000abcd";

type Options = NotificationOptions & {
  actions?: { action: string; title: string }[];
  data?: Record<string, string>;
  renotify?: boolean;
};

interface Scope {
  listeners: Record<string, (e: unknown) => void>;
  shown: Array<[string, Options]>;
  /** One entry for each `showNotification`: whether the device alerts. */
  alerted: boolean[];
  openWindow: ReturnType<typeof vi.fn>;
}

function loadWorker(extra: { fetch?: ReturnType<typeof vi.fn>; Notification?: { maxActions?: number } } = {}): Scope {
  const listeners: Scope["listeners"] = {};
  const shown: Scope["shown"] = [];
  const alerted: boolean[] = [];
  const tags = new Set<string>();
  const openWindow = vi.fn(async (url: string) => ({ url }));
  const scope = {
    location: { origin: ORIGIN },
    addEventListener: (type: string, fn: (e: unknown) => void) => {
      listeners[type] = fn;
    },
    skipWaiting: vi.fn(),
    registration: {
      // The show steps of the Notifications standard: `renotify` with no tag
      // is a TypeError, and a replacement alerts only with `renotify`.
      showNotification: vi.fn(async (title: string, options: Options) => {
        if (options.renotify && !options.tag) throw new TypeError("renotify needs a tag");
        const replaces = Boolean(options.tag && tags.has(options.tag));
        if (options.tag) tags.add(options.tag);
        alerted.push(!replaces || options.renotify === true);
        shown.push([title, options]);
      }),
    },
    clients: { claim: vi.fn(async () => undefined), matchAll: vi.fn(async () => []), openWindow },
    ...extra,
  };
  new Function("self", fs.readFileSync(path.join(ROOT, "public", "sw.js"), "utf8"))(scope);
  return { listeners, shown, alerted, openWindow };
}

async function dispatch(scope: Scope, type: string, event: Record<string, unknown>) {
  const pending: Promise<unknown>[] = [];
  scope.listeners[type]({ ...event, waitUntil: (p: Promise<unknown>) => pending.push(p) });
  await Promise.all(pending);
}

const REMINDER_PUSH = {
  title: "Call the Acme buyer at 9:30 AM",
  body: "Fracktal Works",
  url: `/chat?reminder=${RID}`,
  tag: RID,
  reminder_id: RID,
  kind: "reminder_due",
};

async function pushOf(w: Scope, payload: Record<string, unknown>): Promise<Options> {
  await dispatch(w, "push", { data: { json: () => payload } });
  return w.shown[w.shown.length - 1][1];
}

describe("the buttons of a reminder push", () => {
  it("adds Done and In 1 hour for a reminder that waits, and keeps the id for the click", async () => {
    const w = loadWorker();
    for (const kind of ["reminder_due", "reminder_check"]) {
      const options = await pushOf(w, { ...REMINDER_PUSH, kind });
      expect(options.actions).toEqual([
        { action: "done", title: "Done" },
        { action: "snooze", title: "In 1 hour" },
      ]);
      expect(options.data).toMatchObject({ url: `/chat?reminder=${RID}`, reminder_id: RID });
    }
  });

  it("only a reminder that waits gets buttons", async () => {
    const w = loadWorker();
    expect(await pushOf(w, { ...REMINDER_PUSH, kind: "reminder_unconfirmed" })).not.toHaveProperty("actions");
    expect(await pushOf(w, { title: "T", url: "/", kind: "reminder_due" })).not.toHaveProperty("actions");
    expect(await pushOf(w, { title: "T", url: "/", tag: "g" })).not.toHaveProperty("actions");
  });

  it("a bad id gets no buttons, so no path can be built from it", async () => {
    const w = loadWorker();
    for (const reminder_id of ["../../admin", `${RID}/x`, "r1", 7, null]) {
      expect(await pushOf(w, { ...REMINDER_PUSH, reminder_id })).not.toHaveProperty("actions");
    }
  });

  it("a device that shows fewer than two buttons gets none", async () => {
    const w = loadWorker({ Notification: { maxActions: 1 } });
    expect(await pushOf(w, REMINDER_PUSH)).not.toHaveProperty("actions");
  });

  it("with the details hidden the title is plain and the buttons stay", async () => {
    const w = loadWorker();
    const options = await pushOf(w, { ...REMINDER_PUSH, title: "Metorite: a reminder" });
    expect(w.shown[0][0]).toBe("Metorite: a reminder");
    expect(options.actions).toHaveLength(2);
  });
});

describe("a push that replaces an older one", () => {
  it("a check-back that replaces a fired reminder alerts", async () => {
    const w = loadWorker();
    await pushOf(w, { ...REMINDER_PUSH, renotify: true });
    const options = await pushOf(w, { ...REMINDER_PUSH, kind: "reminder_check", renotify: true });
    expect(options.tag).toBe(RID);
    expect(options.renotify).toBe(true);
    expect(w.alerted).toEqual([true, true]);
  });

  it("today's summary that replaces yesterday's alerts", async () => {
    const w = loadWorker();
    const summary = { title: "3 due today", body: "File the GST return", url: "/", tag: "due-summary", renotify: true };
    await pushOf(w, summary);
    await pushOf(w, { ...summary, title: "1 overdue" });
    expect(w.alerted).toEqual([true, true]);
  });

  it("with no flag the replacement stays silent, as the standard says", async () => {
    const w = loadWorker();
    await pushOf(w, { title: "T", url: "/", tag: "metorite-test" });
    const options = await pushOf(w, { title: "T", url: "/", tag: "metorite-test" });
    expect(options).not.toHaveProperty("renotify");
    expect(w.alerted).toEqual([true, false]);
  });

  it("a push with no tag never sets renotify, so the push still shows", async () => {
    const w = loadWorker();
    expect(await pushOf(w, { title: "T", url: "/", renotify: true })).not.toHaveProperty("renotify");
    expect(await pushOf(w, { title: "T", url: "/", tag: "", renotify: true })).not.toHaveProperty("renotify");
    expect(w.shown).toHaveLength(2);
  });

  it("only a true flag counts", async () => {
    const w = loadWorker();
    for (const renotify of [1, "true", null]) {
      expect(await pushOf(w, { title: "T", url: "/", tag: "g", renotify })).not.toHaveProperty("renotify");
    }
  });
});

describe("a tap on a button", () => {
  const click = (w: Scope, action: string | undefined) =>
    dispatch(w, "notificationclick", {
      action,
      notification: { close: vi.fn(), data: { url: `/chat?reminder=${RID}`, reminder_id: RID } },
    });

  it("Done posts /resolve with the session, and opens nothing", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 200 }));
    const w = loadWorker({ fetch: fetchImpl });
    await click(w, "done");
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/projects/my/reminders/${RID}/resolve`);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(String(init.body))).toEqual({ action: "done" });
    expect(w.openWindow).not.toHaveBeenCalled();
  });

  it("In 1 hour posts /snooze with a time one hour ahead", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 200 }));
    const w = loadWorker({ fetch: fetchImpl });
    const before = Date.now();
    await click(w, "snooze");
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/projects/my/reminders/${RID}/snooze`);
    const at = Date.parse(JSON.parse(String(init.body)).fire_at);
    expect(at).toBeGreaterThanOrEqual(before + 3_600_000);
    expect(at).toBeLessThanOrEqual(Date.now() + 3_600_000);
    expect(w.openWindow).not.toHaveBeenCalled();
  });

  it("a failed request opens the chat card, so nothing is lost", async () => {
    const answers: Array<() => Promise<Response>> = [
      async () => new Response("{}", { status: 401 }),
      async () => new Response("{}", { status: 500 }),
      async () => new Response("{}", { status: 422 }),
      // Another account of this device: the routes answer "not found".
      async () => new Response("{}", { status: 404 }),
      async () => {
        throw new TypeError("offline");
      },
    ];
    for (const answer of answers) {
      const w = loadWorker({ fetch: vi.fn(answer) });
      await click(w, "done");
      expect(w.openWindow).toHaveBeenCalledWith(`${ORIGIN}/chat?reminder=${RID}`);
    }
  });

  it("a reminder that is already closed (409) needs nothing more", async () => {
    const w = loadWorker({ fetch: vi.fn(async () => new Response("{}", { status: 409 })) });
    await click(w, "done");
    expect(w.openWindow).not.toHaveBeenCalled();
  });

  it("a plain tap never calls the routes: it opens the chat card as before", async () => {
    const fetchImpl = vi.fn();
    const w = loadWorker({ fetch: fetchImpl });
    await click(w, undefined);
    await click(w, "");
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(w.openWindow).toHaveBeenCalledTimes(2);
  });

  it("an unknown button, or a bad id in the data, opens the chat card and calls nothing", async () => {
    const fetchImpl = vi.fn();
    const w = loadWorker({ fetch: fetchImpl });
    await click(w, "archive");
    await dispatch(w, "notificationclick", {
      action: "done",
      notification: { close: vi.fn(), data: { url: "/", reminder_id: "../x" } },
    });
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(w.openWindow).toHaveBeenCalledTimes(2);
  });

  it("still has no fetch handler", () => {
    expect(Object.keys(loadWorker().listeners).sort()).toEqual(["activate", "install", "notificationclick", "push"]);
  });
});
