/**
 * HRD-17b: the chat outbox library. Each rule names its fence (R7).
 * The vitest environment is `node`, so the storage is injected.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  OUTBOX_KEY,
  OUTBOX_MAX_ENTRIES,
  OUTBOX_MAX_TEXT_CHARS,
  OUTBOX_MAX_TOTAL_CHARS,
  OUTBOX_STORAGE,
  OUTBOX_TTL_MS,
  createOutbox,
  type OutboxEntry,
  type OutboxStorage,
  type SendOutcome,
} from "./chatOutbox";

class FakeStorage implements OutboxStorage {
  map = new Map<string, string>();
  getItem(k: string) {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string) {
    this.map.set(k, v);
  }
  removeItem(k: string) {
    this.map.delete(k);
  }
}

class ThrowingStorage extends FakeStorage {
  setItem(): void {
    throw new Error("QuotaExceededError");
  }
}

const NOW = 1_000_000_000_000;
const clock = () => NOW;
const entry = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  threadId: "t1",
  text: `hello ${id}`,
  createdAt: NOW - 1000,
  ...over,
});
const sendAll = (out: SendOutcome, log: string[] = []) => async (e: OutboxEntry) => {
  log.push(e.id);
  return out;
};

/** The same behaviour table runs on both backends. */
const backends = [
  { name: "memory", make: () => createOutbox({ backend: "memory", now: clock, memberKey: "a@x" }) },
  {
    name: "session",
    make: () =>
      createOutbox({ backend: "session", storage: new FakeStorage(), now: clock, memberKey: "a@x" }),
  },
];

describe.each(backends)("outbox behaviour on the $name backend", ({ make }) => {
  it("ONCE: a duplicate id is refused", () => {
    const o = make();
    expect(o.enqueue(entry("m1"))).toEqual({ ok: true });
    expect(o.enqueue(entry("m1"))).toEqual({ ok: false, reason: "duplicate" });
    expect(o.list("t1")).toHaveLength(1);
  });

  it("ONCE: two concurrent releases send one entry once (single-flight)", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    const log: string[] = [];
    let unblock: () => void = () => {};
    const gate = new Promise<void>((r) => (unblock = r));
    const send = async (e: OutboxEntry): Promise<SendOutcome> => {
      log.push(e.id);
      await gate;
      return "sent";
    };
    const a = o.release("t1", send, NOW);
    const b = o.release("t1", send, NOW);
    unblock();
    const [ra, rb] = await Promise.all([a, b]);
    expect(log).toEqual(["m1"]);
    expect(rb.skipped).toBe(true);
    expect(ra.sent.map((e) => e.id)).toEqual(["m1"]);
    expect(o.list("t1")).toHaveLength(0);
  });

  it("ORDER: a retry on entry 1 blocks entry 2, then 1 goes before 2", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    o.enqueue(entry("m2"));
    const log: string[] = [];
    const r1 = await o.release("t1", sendAll("retry", log), NOW);
    expect(log).toEqual(["m1"]);
    expect(r1.retry?.id).toBe("m1");
    expect(o.list("t1").map((e) => e.id)).toEqual(["m1", "m2"]);
    const r2 = await o.release("t1", sendAll("sent", log), NOW);
    expect(log).toEqual(["m1", "m1", "m2"]);
    expect(r2.sent.map((e) => e.id)).toEqual(["m1", "m2"]);
  });

  it("A network error keeps the entry as waiting, with no maybeSent flag", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    await o.release("t1", sendAll("retry"), NOW);
    expect(o.list("t1")[0]).toMatchObject({ id: "m1", state: "waiting" });
    expect(o.list("t1")[0].maybeSent).toBeUndefined();
  });

  it("A refusal (failed) removes the entry and reports it", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    o.enqueue(entry("m2"));
    const log: string[] = [];
    const r = await o.release(
      "t1",
      async (e) => {
        log.push(e.id);
        return e.id === "m1" ? "failed" : "sent";
      },
      NOW,
    );
    expect(r.failed.map((e) => e.id)).toEqual(["m1"]);
    expect(r.sent.map((e) => e.id)).toEqual(["m2"]);
    expect(o.list("t1")).toHaveLength(0);
  });

  it("A send that throws keeps the entry and flags it maybeSent", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    const r = await o.release("t1", async () => {
      throw new Error("boom");
    }, NOW);
    expect(r.retry?.id).toBe("m1");
    expect(o.list("t1")[0].maybeSent).toBe(true);
  });

  it("TTL: an expired entry is reported and never sent", async () => {
    const o = make();
    o.enqueue(entry("old", { createdAt: NOW - OUTBOX_TTL_MS - 1 }));
    o.enqueue(entry("new"));
    const log: string[] = [];
    const r = await o.release("t1", sendAll("sent", log), NOW);
    expect(log).toEqual(["new"]);
    expect(r.expired.map((e) => e.id)).toEqual(["old"]);
    expect(r.expired[0].text).toBe("hello old");
  });

  it("TTL: an entry exactly at the limit is still sent", async () => {
    const o = make();
    o.enqueue(entry("edge", { createdAt: NOW - OUTBOX_TTL_MS }));
    const r = await o.release("t1", sendAll("sent"), NOW);
    expect(r.sent).toHaveLength(1);
  });

  it("SIGN-OUT: clear drops every entry", () => {
    const o = make();
    o.enqueue(entry("m1"));
    o.enqueue(entry("m2", { threadId: "t2" }));
    o.clear();
    expect(o.list("t1")).toHaveLength(0);
    expect(o.list("t2")).toHaveLength(0);
    expect(o.status().count).toBe(0);
  });

  it("MEMBER SWITCH: clearForMemberSwitch drops all and returns the count", () => {
    const o = make();
    o.enqueue(entry("m1"));
    o.enqueue(entry("m2", { threadId: "t2" }));
    expect(o.clearForMemberSwitch()).toBe(2);
    expect(o.status().count).toBe(0);
  });

  it("MEMBER SWITCH: setMemberKey to another member drops, the first set keeps", () => {
    const o = createOutbox({ backend: "memory", now: clock });
    o.enqueue(entry("m1"));
    expect(o.setMemberKey("a@x")).toBe(0);
    expect(o.status().count).toBe(1);
    expect(o.setMemberKey("b@x")).toBe(1);
    expect(o.status().count).toBe(0);
  });

  it("TEXT ONLY: an entry with files is refused, an empty one too", () => {
    const o = make();
    expect(o.enqueue(entry("m1", { files: [{}] }))).toEqual({ ok: false, reason: "files" });
    expect(o.enqueue(entry("m1", { files: 2 }))).toEqual({ ok: false, reason: "files" });
    expect(o.enqueue(entry("m1", { files: [] }))).toEqual({ ok: true });
    expect(o.enqueue(entry("m2", { text: "   " }))).toEqual({ ok: false, reason: "empty" });
  });

  it("SIZE: one long text, too many entries and the total are refused", () => {
    const o = make();
    expect(o.enqueue(entry("big", { text: "x".repeat(OUTBOX_MAX_TEXT_CHARS + 1) }))).toEqual({
      ok: false,
      reason: "too_long",
    });
    for (let i = 0; i < OUTBOX_MAX_ENTRIES; i++) expect(o.enqueue(entry(`e${i}`)).ok).toBe(true);
    expect(o.enqueue(entry("over"))).toEqual({ ok: false, reason: "full" });
    const p = make();
    const per = OUTBOX_MAX_TEXT_CHARS;
    const fits = Math.floor(OUTBOX_MAX_TOTAL_CHARS / per);
    for (let i = 0; i < fits; i++) expect(p.enqueue(entry(`b${i}`, { text: "y".repeat(per) })).ok).toBe(true);
    expect(p.enqueue(entry("last", { text: "y".repeat(per) }))).toEqual({ ok: false, reason: "full" });
  });

  it("SUPERSEDES: an entry keeps its supersedes id", () => {
    const o = make();
    o.enqueue(entry("m2", { supersedes: "m1" }));
    expect(o.list("t1")[0].supersedes).toBe("m1");
  });

  it("MAYBESENT: a held entry stops the queue until the caller decides", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    o.enqueue(entry("m2"));
    await o.release("t1", async () => {
      throw new Error("x");
    }, NOW);
    const log: string[] = [];
    const held = await o.release("t1", sendAll("sent", log), NOW);
    expect(log).toEqual([]);
    expect(held.held?.id).toBe("m1");
    expect(o.markAcknowledged("m1")).toBe(true);
    const r = await o.release("t1", sendAll("sent", log), NOW);
    expect(log).toEqual(["m2"]);
    expect(r.sent).toHaveLength(1);
  });

  it("MAYBESENT: markNotSent lets the entry go out again", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    await o.release("t1", async () => {
      throw new Error("x");
    }, NOW);
    expect(o.markNotSent("m1")).toBe(true);
    const log: string[] = [];
    await o.release("t1", sendAll("sent", log), NOW);
    expect(log).toEqual(["m1"]);
  });

  it("cancel removes one entry", () => {
    const o = make();
    o.enqueue(entry("m1"));
    expect(o.cancel("m1")).toBe(true);
    expect(o.cancel("m1")).toBe(false);
    expect(o.list("t1")).toHaveLength(0);
  });

  it("CANCEL IN FLIGHT: a cancelled entry is reported as neither sent nor retry", async () => {
    for (const outcome of ["sent", "retry", "failed"] as const) {
      const o = make();
      o.enqueue(entry("m1"));
      o.enqueue(entry("m2"));
      const log: string[] = [];
      const r = await o.release(
        "t1",
        async (e) => {
          log.push(e.id);
          if (e.id === "m1") o.cancel("m1");
          return e.id === "m1" ? outcome : "sent";
        },
        NOW,
      );
      expect(r.sent.map((e) => e.id)).toEqual(["m2"]);
      expect(r.failed).toEqual([]);
      expect(r.retry).toBeNull();
      expect(log).toEqual(["m1", "m2"]);
    }
  });

  it("ENQUEUE DURING RELEASE: a new entry goes out in the same pass, after the earlier ones", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    o.enqueue(entry("m2"));
    const log: string[] = [];
    const r = await o.release(
      "t1",
      async (e) => {
        log.push(e.id);
        if (e.id === "m1") o.enqueue(entry("m3"));
        return "sent";
      },
      NOW,
    );
    expect(log).toEqual(["m1", "m2", "m3"]);
    expect(r.sent.map((e) => e.id)).toEqual(["m1", "m2", "m3"]);
  });

  it("SYNC THROW: a send that throws at once frees the lock and holds the entry", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    const r = await o.release(
      "t1",
      (() => {
        throw new Error("sync");
      }) as unknown as (e: OutboxEntry) => Promise<SendOutcome>,
      NOW,
    );
    expect(r.retry?.id).toBe("m1");
    expect(o.list("t1")[0].maybeSent).toBe(true);
    const again = await o.release("t1", sendAll("sent"), NOW);
    expect(again.skipped).toBe(false);
    expect(again.held?.id).toBe("m1");
  });

  it("CLEAR IN FLIGHT: a clear during a send reports nothing and sends no later entry", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    o.enqueue(entry("m2"));
    const log: string[] = [];
    const r = await o.release(
      "t1",
      async (e) => {
        log.push(e.id);
        o.clear();
        return "sent";
      },
      NOW,
    );
    expect(log).toEqual(["m1"]);
    expect(r.sent).toEqual([]);
  });

  it("A release of one thread leaves another thread's entries", async () => {
    const o = make();
    o.enqueue(entry("m1"));
    o.enqueue(entry("n1", { threadId: "t2" }));
    await o.release("t1", sendAll("sent"), NOW);
    expect(o.list("t2")).toHaveLength(1);
  });
});

describe("the session copy", () => {
  const mk = (st: OutboxStorage, member: string | null = "a@x") =>
    createOutbox({ backend: "session", storage: st, now: clock, memberKey: member });
  const seed = (st: FakeStorage, doc: unknown) => st.setItem(OUTBOX_KEY, JSON.stringify(doc));
  const good = (id: string, over: Record<string, unknown> = {}) => ({
    id,
    threadId: "t1",
    text: "hi",
    createdAt: NOW - 5,
    state: "waiting",
    ...over,
  });

  it("RELOAD: a waiting entry comes back after a restore", () => {
    const st = new FakeStorage();
    const a = mk(st);
    a.enqueue(entry("m1", { supersedes: "m0" }));
    const b = mk(st);
    b.restore();
    expect(b.list("t1")).toEqual([
      { id: "m1", threadId: "t1", text: "hello m1", createdAt: NOW - 1000, supersedes: "m0", state: "waiting" },
    ]);
  });

  it("RELOAD: an entry inflight at the reload returns waiting and maybeSent", async () => {
    const st = new FakeStorage();
    const a = mk(st);
    a.enqueue(entry("m1"));
    let snapshot = "";
    await a.release(
      "t1",
      async () => {
        snapshot = st.getItem(OUTBOX_KEY) as string; // the page dies here
        return "sent";
      },
      NOW,
    );
    expect(JSON.parse(snapshot).entries[0].state).toBe("inflight");
    const dead = new FakeStorage();
    dead.setItem(OUTBOX_KEY, snapshot);
    const b = mk(dead);
    b.restore();
    expect(b.list("t1")[0]).toMatchObject({ id: "m1", state: "waiting", maybeSent: true });
  });

  it("RESTORE: corrupt JSON is dropped and never throws", () => {
    const st = new FakeStorage();
    st.setItem(OUTBOX_KEY, "{not json");
    const o = mk(st);
    expect(() => o.restore()).not.toThrow();
    expect(o.status().count).toBe(0);
  });

  it("RESTORE: a storage that throws on read never throws", () => {
    const st = new FakeStorage();
    st.getItem = () => {
      throw new Error("denied");
    };
    expect(() => mk(st).restore()).not.toThrow();
  });

  it("RESTORE: wrong types, a missing field and a future createdAt are dropped", () => {
    const st = new FakeStorage();
    seed(st, {
      v: 1,
      member: "a@x",
      entries: [
        good("ok"),
        good("t1", { text: 5 }),
        good("t2", { createdAt: "now" }),
        good("t3", { createdAt: NOW + 60_000 }),
        good("t4", { state: "weird" }),
        good("t5", { supersedes: 7 }),
        good("t6", { threadId: undefined }),
        { ...good("t7"), id: undefined },
        good("t8", { text: "   " }),
        good("t9", { maybeSent: "yes" }),
        null,
        "str",
        good("ok"),
      ],
    });
    const o = mk(st);
    o.restore();
    expect(o.list("t1").map((e) => e.id)).toEqual(["ok"]);
  });

  it("RESTORE: a wrong version or shape is dropped", () => {
    for (const doc of [{ v: 2, member: "a@x", entries: [] }, [1], "x", { v: 1, member: "a@x", entries: 4 }]) {
      const st = new FakeStorage();
      seed(st, doc);
      const o = mk(st);
      expect(() => o.restore()).not.toThrow();
      expect(o.status().count).toBe(0);
    }
  });

  it("MEMBER: the copy of another member is dropped, and the key is removed", () => {
    const st = new FakeStorage();
    mk(st, "a@x").enqueue(entry("m1"));
    const b = mk(st, "b@x");
    b.restore();
    expect(b.status().count).toBe(0);
    expect(st.getItem(OUTBOX_KEY)).toBeNull();
  });

  it("MEMBER: a copy with no member does not load for a known member", () => {
    const st = new FakeStorage();
    seed(st, { v: 1, member: null, entries: [good("m1")] });
    const o = mk(st, "a@x");
    o.restore();
    expect(o.status().count).toBe(0);
  });

  it("CLEAR: sign-out removes the session copy", () => {
    const st = new FakeStorage();
    const o = mk(st);
    o.enqueue(entry("m1"));
    expect(st.getItem(OUTBOX_KEY)).not.toBeNull();
    o.clear();
    expect(st.getItem(OUTBOX_KEY)).toBeNull();
  });

  it("DEGRADED: a storage that throws falls back to memory and says so", async () => {
    const o = mk(new ThrowingStorage());
    expect(o.status().degraded).toBe(false);
    expect(o.enqueue(entry("m1"))).toEqual({ ok: true });
    expect(o.status().degraded).toBe(true);
    expect(o.list("t1")).toHaveLength(1);
    const r = await o.release("t1", sendAll("sent"), NOW);
    expect(r.sent).toHaveLength(1);
  });

  it("DEGRADED: the session backend with no storage reports degraded", () => {
    const o = createOutbox({ backend: "session", storage: null, now: clock });
    expect(o.status().degraded).toBe(true);
    expect(o.enqueue(entry("m1")).ok).toBe(true);
  });

  it("The memory backend writes no copy", () => {
    const st = new FakeStorage();
    const o = createOutbox({ backend: "memory", storage: st, now: clock });
    o.enqueue(entry("m1"));
    expect(st.map.size).toBe(0);
  });

  it("The default backend is the proposed one", () => {
    expect(OUTBOX_STORAGE).toBe("session");
  });
});

describe("source fences", () => {
  const root = join(__dirname, "..");
  const read = (p: string) => readFileSync(join(root, p), "utf-8");

  it("NEVER localStorage: chatOutbox.ts does not name it", () => {
    expect(read("lib/chatOutbox.ts")).not.toMatch(/localStorage/);
  });

  it("chatOutbox.ts does not import React and holds no steer kind", () => {
    const src = read("lib/chatOutbox.ts");
    expect(src).not.toMatch(/from ["']react["']/);
    expect(src).not.toMatch(/kind:\s*["']steer["']/);
  });

  it("nothing in the chat calls the outbox yet (HRD-17d wires it)", () => {
    for (const f of ["hooks/useAgentChat.ts", "components/AgentChat.tsx"]) {
      expect(read(f)).not.toMatch(/chatOutbox/);
    }
  });

  it("CLEARED AT A SWITCH: switchTo, addAccount and signOutAll each call clearOutbox", () => {
    const src = read("lib/accountSwitch.ts");
    for (const name of ["switchTo", "addAccount", "signOutThis", "signOutAll"]) {
      const start = src.indexOf(`export async function ${name}(`);
      expect(start, name).toBeGreaterThan(-1);
      const end = src.indexOf("\nexport ", start + 10);
      expect(src.slice(start, end < 0 ? undefined : end), name).toMatch(/clearOutbox\(\)/);
    }
  });

  it("CLEARED AT SIGN-OUT: each forgetThisDevice call has clearOutbox just before it", () => {
    for (const f of ["components/Sidebar.tsx", "lib/accountSwitch.ts", "app/signup/SignUpForm.tsx"]) {
      const src = read(f);
      expect(src).toMatch(/import \{ clearOutbox \} from "@\/lib\/chatOutbox"/);
      const calls = [...src.matchAll(/forgetThisDevice\(/g)].map((m) => m.index as number);
      expect(calls.length).toBeGreaterThan(0);
      for (const at of calls) {
        expect(src.slice(Math.max(0, at - 400), at)).toMatch(/clearOutbox\(\)/);
      }
    }
  });
});
