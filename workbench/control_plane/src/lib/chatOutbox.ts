/**
 * The chat outbox (HRD-17b, `project-docs/specs/chat_failure_modes.md` §4.7a).
 *
 * A message that the server did not acknowledge waits here. The outbox hands
 * each entry to a caller-supplied `send`, in order and once. It is pure
 * TypeScript with no React. Nothing calls it from the chat yet: the wiring is
 * HRD-17d, and it waits for the server dedupe of HRD-17a.
 *
 * What this module never does:
 * - It never holds a steer. A send during a live run stays on the steer path
 *   (`chatSteer.ts`), and the entry kind has no steer value.
 * - It never writes message text to the browser store that outlives the tab. A source fence pins it.
 * - It never re-sends an entry that may have reached the server. A `maybeSent`
 *   entry stops the queue until the caller checks the history for its id.
 *
 * Where the copy lives (D-HRD-17-1, PROPOSED): ONE exported constant,
 * `OUTBOX_STORAGE`, picks the backend. `"session"` keeps the entries in memory
 * and a copy in `sessionStorage` for a reload of the same tab. `"memory"`
 * keeps no copy. Either answer of the owner is a one-line change.
 *
 * The session copy is ONE JSON value for all threads, under `OUTBOX_KEY`. One
 * value keeps one write and one quota check. It carries the member key, so a
 * copy that another member left in a shared browser is dropped at `restore`.
 */

/** Which backend the default outbox uses. D-HRD-17-1 default: `"session"`. */
export const OUTBOX_STORAGE: "session" | "memory" = "session";
/** A waiting message older than this is not sent. It comes back as expired. */
export const OUTBOX_TTL_MS = 10 * 60 * 1000;
/** The longest text of one entry, in characters. */
export const OUTBOX_MAX_TEXT_CHARS = 20_000;
/** The total text of all entries, in characters. Far under the 5 MB quota. */
export const OUTBOX_MAX_TOTAL_CHARS = 200_000;
/** The most entries at one time. */
export const OUTBOX_MAX_ENTRIES = 20;
/** The one `sessionStorage` key. */
export const OUTBOX_KEY = "metorite.chatOutbox.v1";

export type OutboxEntry = {
  /** The existing `user_message_id`. No second id exists. */
  id: string;
  threadId: string;
  /** Text only. A send with files is not enqueued. */
  text: string;
  /** Epoch milliseconds. */
  createdAt: number;
  /** The id that an edit-and-resend replaces (`chatEdit.ts`). */
  supersedes?: string;
  /** `inflight` while a `send` runs. A reload turns it back to `waiting`. */
  state: "waiting" | "inflight";
  /** The server may hold this message. Check the history before a re-send. */
  maybeSent?: boolean;
};

export type EnqueueInput = {
  id: string;
  threadId: string;
  text: string;
  createdAt: number;
  supersedes?: string;
  /** A count or a list. Any non-empty value makes `enqueue` refuse. */
  files?: number | readonly unknown[];
};

export type EnqueueResult =
  | { ok: true }
  | { ok: false; reason: "files" | "empty" | "duplicate" | "full" | "too_long" };

/** What `send` answers. `retry` is a network error. `failed` is a refusal. */
export type SendOutcome = "sent" | "retry" | "failed";

export type ReleaseReport = {
  sent: OutboxEntry[];
  /** Removed after a non-network refusal. The caller shows these as not sent. */
  failed: OutboxEntry[];
  /** Past the TTL. Removed, never sent. The caller puts the text back. */
  expired: OutboxEntry[];
  /** The queue stopped on a network error. This entry stays at the front. */
  retry: OutboxEntry | null;
  /** The queue stopped on an entry that may be at the server. */
  held: OutboxEntry | null;
  /** True when another release of the same thread was already running. */
  skipped: boolean;
};

/** The part of `Storage` that the outbox uses. */
export type OutboxStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export type OutboxStatus = { degraded: boolean; backend: "session" | "memory"; count: number };

export type OutboxOptions = {
  /** The backend. The default outbox reads `OUTBOX_STORAGE`. */
  backend?: "session" | "memory";
  /** The injected storage for the `"session"` backend. */
  storage?: OutboxStorage | null;
  /** Opaque. The same id that binds `dataCache.bindIdentity`. */
  memberKey?: string | null;
  /** The clock. For the tests. */
  now?: () => number;
};

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Validate one stored entry. Returns null for anything malformed. */
function parseEntry(raw: unknown, now: number): OutboxEntry | null {
  if (!isObj(raw)) return null;
  const { id, threadId, text, createdAt, supersedes, state, maybeSent } = raw;
  if (typeof id !== "string" || !id) return null;
  if (typeof threadId !== "string" || !threadId) return null;
  if (typeof text !== "string" || !text.trim()) return null;
  if (text.length > OUTBOX_MAX_TEXT_CHARS) return null;
  if (typeof createdAt !== "number" || !Number.isFinite(createdAt)) return null;
  // Never trust a time in the future: it would never expire.
  if (createdAt > now) return null;
  if (supersedes !== undefined && (typeof supersedes !== "string" || !supersedes)) return null;
  if (state !== "waiting" && state !== "inflight") return null;
  if (maybeSent !== undefined && typeof maybeSent !== "boolean") return null;
  const entry: OutboxEntry = { id, threadId, text, createdAt, state: "waiting" };
  if (supersedes !== undefined) entry.supersedes = supersedes;
  // An entry that was in flight at the reload may be at the server.
  if (maybeSent === true || state === "inflight") entry.maybeSent = true;
  return entry;
}

export function createOutbox(options: OutboxOptions = {}) {
  const clock = options.now ?? (() => Date.now());
  const backend = options.backend ?? OUTBOX_STORAGE;
  const storage: OutboxStorage | null = backend === "session" ? (options.storage ?? null) : null;
  let memberKey: string | null = options.memberKey ?? null;
  let entries: OutboxEntry[] = [];
  let degraded = backend === "session" && storage === null;
  const running = new Set<string>();

  function persist(): void {
    if (!storage) return;
    try {
      if (entries.length === 0) {
        storage.removeItem(OUTBOX_KEY);
      } else {
        storage.setItem(OUTBOX_KEY, JSON.stringify({ v: 1, member: memberKey, entries }));
      }
    } catch {
      // Private mode or quota. The entries stay in memory.
      degraded = true;
    }
  }

  function totalChars(): number {
    let n = 0;
    for (const e of entries) n += e.text.length;
    return n;
  }

  function remove(id: string): OutboxEntry | null {
    const i = entries.findIndex((e) => e.id === id);
    if (i < 0) return null;
    const [gone] = entries.splice(i, 1);
    return gone;
  }

  return {
    /** Add a waiting message. It refuses a send with files, and says why. */
    enqueue(input: EnqueueInput): EnqueueResult {
      const files = input.files;
      if ((typeof files === "number" && files > 0) || (Array.isArray(files) && files.length > 0)) {
        return { ok: false, reason: "files" };
      }
      if (!input.text.trim() || !input.id || !input.threadId) return { ok: false, reason: "empty" };
      if (input.text.length > OUTBOX_MAX_TEXT_CHARS) return { ok: false, reason: "too_long" };
      if (entries.some((e) => e.id === input.id)) return { ok: false, reason: "duplicate" };
      if (
        entries.length >= OUTBOX_MAX_ENTRIES ||
        totalChars() + input.text.length > OUTBOX_MAX_TOTAL_CHARS
      ) {
        return { ok: false, reason: "full" };
      }
      const entry: OutboxEntry = {
        id: input.id,
        threadId: input.threadId,
        text: input.text,
        createdAt: input.createdAt,
        state: "waiting",
      };
      if (input.supersedes) entry.supersedes = input.supersedes;
      entries.push(entry);
      persist();
      return { ok: true };
    },

    /** The waiting entries of a thread, first in first out. */
    list(threadId: string): OutboxEntry[] {
      return entries.filter((e) => e.threadId === threadId).map((e) => ({ ...e }));
    },

    /**
     * Send the entries of a thread in order, once each. Single-flight for the
     * thread: a second call while one runs does nothing.
     */
    async release(
      threadId: string,
      send: (entry: OutboxEntry) => Promise<SendOutcome>,
      now?: number,
    ): Promise<ReleaseReport> {
      const report: ReleaseReport = {
        sent: [],
        failed: [],
        expired: [],
        retry: null,
        held: null,
        skipped: false,
      };
      if (running.has(threadId)) {
        report.skipped = true;
        return report;
      }
      running.add(threadId);
      try {
        for (;;) {
          const next = entries.find((e) => e.threadId === threadId);
          if (!next) break;
          const t = now ?? clock();
          if (t - next.createdAt > OUTBOX_TTL_MS) {
            remove(next.id);
            persist();
            report.expired.push({ ...next });
            continue;
          }
          if (next.maybeSent) {
            // The server may have it. The caller checks the history first.
            report.held = { ...next };
            break;
          }
          next.state = "inflight";
          persist();
          let outcome: SendOutcome;
          try {
            outcome = await send({ ...next });
          } catch {
            // A cancel, a clear or a member switch removed it while it flew.
            if (!entries.includes(next)) continue;
            // The result is unknown, so the server may hold it.
            next.state = "waiting";
            next.maybeSent = true;
            persist();
            report.retry = { ...next };
            break;
          }
          // A cancel, a clear or a member switch removed it while it flew:
          // report nothing, and go on with the entries that remain.
          if (!entries.includes(next)) continue;
          if (outcome === "sent") {
            remove(next.id);
            persist();
            report.sent.push({ ...next });
          } else if (outcome === "failed") {
            remove(next.id);
            persist();
            report.failed.push({ ...next });
          } else {
            // A network error: keep the entry and the order, send no later one.
            next.state = "waiting";
            persist();
            report.retry = { ...next };
            break;
          }
        }
      } finally {
        running.delete(threadId);
      }
      return report;
    },

    /** The server acknowledged the id (first stream event, or the poll found the row). */
    markAcknowledged(id: string): boolean {
      const gone = remove(id);
      if (gone) persist();
      return gone !== null;
    },

    /** The history check found no row for the id. The entry may go out again. */
    markNotSent(id: string): boolean {
      const e = entries.find((x) => x.id === id);
      if (!e) return false;
      delete e.maybeSent;
      e.state = "waiting";
      persist();
      return true;
    },

    /** The member cancels one waiting entry. */
    cancel(id: string): boolean {
      const gone = remove(id);
      if (gone) persist();
      return gone !== null;
    },

    /** Sign-out. Drop every entry and the session copy. */
    clear(): void {
      entries = [];
      if (storage) {
        try {
          storage.removeItem(OUTBOX_KEY);
        } catch {
          degraded = true;
        }
      }
    },

    /** A member switch. Drop every entry and return how many were waiting. */
    clearForMemberSwitch(): number {
      const n = entries.length;
      this.clear();
      return n;
    },

    /**
     * Set the member once. A change from one member to another drops the
     * entries, as a switch does. The first set (from null) keeps them.
     */
    setMemberKey(key: string | null): number {
      const old = memberKey;
      memberKey = key;
      if (old !== null && old !== key) return this.clearForMemberSwitch();
      persist();
      return 0;
    },

    /**
     * Read the session copy at start. It drops anything malformed and never
     * throws. It drops the copy of another member and any time in the future.
     */
    restore(now?: number): void {
      if (!storage) return;
      const t = now ?? clock();
      let parsed: unknown;
      try {
        const raw = storage.getItem(OUTBOX_KEY);
        if (raw === null || raw === undefined) return;
        parsed = JSON.parse(raw);
      } catch {
        // Corrupt JSON, or a storage that throws.
        try {
          storage.removeItem(OUTBOX_KEY);
        } catch {
          degraded = true;
        }
        return;
      }
      if (!isObj(parsed) || parsed.v !== 1 || !Array.isArray(parsed.entries)) {
        persistAfterDrop();
        return;
      }
      // A copy of another member is dropped whole (a shared browser).
      if (parsed.member !== memberKey) {
        persistAfterDrop();
        return;
      }
      const seen = new Set(entries.map((e) => e.id));
      let total = totalChars();
      for (const raw of parsed.entries) {
        const e = parseEntry(raw, t);
        if (!e || seen.has(e.id)) continue;
        if (entries.length >= OUTBOX_MAX_ENTRIES || total + e.text.length > OUTBOX_MAX_TOTAL_CHARS) {
          continue;
        }
        seen.add(e.id);
        total += e.text.length;
        entries.push(e);
      }
      persist();

      function persistAfterDrop(): void {
        try {
          storage?.removeItem(OUTBOX_KEY);
        } catch {
          degraded = true;
        }
      }
    },

    status(): OutboxStatus {
      return { degraded, backend, count: entries.length };
    },
  };
}

export type Outbox = ReturnType<typeof createOutbox>;

let defaultOutbox: Outbox | null = null;

/** The one outbox of the page. It reads `OUTBOX_STORAGE`. */
export function getOutbox(): Outbox {
  if (!defaultOutbox) {
    let storage: OutboxStorage | null = null;
    if (OUTBOX_STORAGE === "session" && typeof window !== "undefined") {
      try {
        storage = window.sessionStorage;
      } catch {
        storage = null;
      }
    }
    defaultOutbox = createOutbox({ backend: OUTBOX_STORAGE, storage });
  }
  return defaultOutbox;
}

/**
 * Forget every waiting message. Call it with `forgetThisDevice` at each
 * sign-out and each member switch. It never throws. Returns the count that
 * was waiting, so a notice can say so later.
 */
export function clearOutbox(): number {
  try {
    const n = getOutbox().clearForMemberSwitch();
    // A copy that this page never loaded must go too.
    if (typeof window !== "undefined") window.sessionStorage.removeItem(OUTBOX_KEY);
    return n;
  } catch {
    return 0;
  }
}
