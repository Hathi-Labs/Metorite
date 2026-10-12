"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  composeAssistStream,
  DraftStreamError,
  type ComposeAssistArgs,
  type DraftErrorReason,
} from "./api";

/**
 * One "Draft with AI" session for a composer.
 *
 * Drafting is iterative, not one-shot: the first run writes a draft, and every
 * later run REFINES the draft that's currently in the box ("make it shorter",
 * "mention the deadline"). Each run is kept as a revision the user can flip
 * back to, so trying an edit is never destructive.
 *
 * The hook owns the whole session — live backend steps, revisions, and the
 * busy/error state — so all three composer surfaces (compose window, inline
 * reply, conversation draft card) behave identically.
 */

/** A merged step in the live activity feed (start/done events collapse into one). */
export interface DraftStep {
  key: string;
  label: string;
  detail: string;
  state: "running" | "done" | "failed";
  /** Consults only: the question put to the specialist agent, and its answer. */
  question?: string;
  answer?: string;
}

/** A saved version of the body — the first one is the user's own starting text. */
export interface DraftRevision {
  id: number;
  body: string;
  label: string;
  source: "user" | "ai";
  /** The instruction that produced this revision ("" for the starting text). */
  instruction: string;
}

interface ActivityEvent {
  kind?: string;
  id?: string;
  status?: string;
  detail?: string;
  emailKind?: string;
  consults?: number;
  agent?: string;
  question?: string;
  answer?: string;
}

const STAGE_LABELS: Record<string, string> = {
  context: "Thread context",
  memory: "Sender history",
  draft: "Writing draft",
};

/** "agent-sales-assistant" → "sales assistant" */
export function prettyAgent(agent: string): string {
  return agent.replace(/^agent-/, "").replace(/-/g, " ");
}

function applyEvent(steps: DraftStep[], evt: ActivityEvent): DraftStep[] {
  const upsert = (key: string, next: Partial<DraftStep> & { label: string }) => {
    const i = steps.findIndex((s) => s.key === key);
    if (i === -1) {
      return [...steps, { key, detail: "", state: "running", ...next } as DraftStep];
    }
    const merged = [...steps];
    merged[i] = { ...merged[i], ...next };
    return merged;
  };

  if (evt.kind === "stage" && evt.id) {
    return upsert(`stage:${evt.id}`, {
      label: STAGE_LABELS[evt.id] ?? evt.id,
      detail: evt.detail ?? "",
      state: evt.status === "done" ? "done" : "running",
    });
  }
  if (evt.kind === "qualify") {
    return upsert("qualify", {
      label: evt.emailKind ? `Qualified as ${evt.emailKind}` : "Qualified",
      detail: evt.detail ?? "",
      state: "done",
    });
  }
  if (evt.kind === "consult" && evt.agent) {
    return upsert(`consult:${evt.agent}`, {
      label: `Asked ${prettyAgent(evt.agent)}`,
      question: evt.question,
      answer: evt.answer,
      detail: evt.detail ?? "",
      state:
        evt.status === "done"
          ? "done"
          : evt.status === "failed"
            ? "failed"
            : "running",
    });
  }
  return steps;
}

/**
 * A server `status` event as a step (EM-T17 §17.3.7). It adds a running step
 * for a stage the panel does not show yet. It never moves a step that an
 * `activity` event already marked done back to running.
 */
export function applyStatus(steps: DraftStep[], stage: string, detail?: string): DraftStep[] {
  const key = `stage:${stage}`;
  if (steps.some((s) => s.key === key)) return steps;
  return [
    ...steps,
    { key, label: STAGE_LABELS[stage] ?? stage, detail: detail ?? "", state: "running" },
  ];
}

/** A step that still runs when the round ends without a draft has failed. */
function failRunning(steps: DraftStep[]): DraftStep[] {
  return steps.some((s) => s.state === "running")
    ? steps.map((s) => (s.state === "running" ? { ...s, state: "failed" as const } : s))
    : steps;
}

/**
 * The composer's bound on one round, in ms (EM-T17 §17.3.7). It stays above
 * the server's `SERVER_TOTAL_S = 170` (`drafting.py`), so the server ends
 * first and sends a typed reason. `useDraftSession.test.ts` holds the two
 * numbers apart (A2d).
 */
export const CLIENT_TOTAL_MS = 180_000;

/** What the panel says when a round ends with no draft for a known cause. */
export const DRAFT_NOTICES: Record<"stopped" | "timeout" | DraftErrorReason, string> = {
  stopped: "Drafting stopped. Your text is unchanged.",
  timeout: "The draft took too long. Try again.",
  model_slow: "The model did not start the draft in time. Try again.",
  model_stalled: "The model stopped in the middle of the draft. The text so far stays. Try again.",
  model_timeout: "The draft took too long. The text so far stays. Try again.",
  server_timeout: "The draft took too long. The text so far stays. Try again.",
};

/** The `DOMException` name the client bound aborts with. Stop aborts with
 *  the default `AbortError`. */
const CLIENT_TIMEOUT = "TimeoutError";

/** Stop does nothing for this long after a round starts. Stop takes the
 *  place of the run button, so a double-click on Draft must not stop the
 *  draft it just started. */
export const STOP_GRACE_MS = 400;

/** One round of a session: its abort, and whether it still owns the
 *  composer. A round that a reset or a newer round ended writes nothing. */
export interface DraftRound {
  controller: AbortController;
  isCurrent: () => boolean;
}

/**
 * The rounds of one session, with no React in it. `begin` ends the round
 * before it. `reset` aborts the round and ends it, so it writes nothing to
 * a composer that now shows another mail. `stop` aborts the round and keeps
 * it current, so it can say why it stopped. It does nothing inside
 * {@link STOP_GRACE_MS}.
 */
export function createDraftRounds(now: () => number = () => Date.now()) {
  let seq = 0;
  let current: (DraftRound & { startedAt: number }) | null = null;
  return {
    begin(): DraftRound {
      current?.controller.abort();
      const token = ++seq;
      current = {
        controller: new AbortController(),
        isCurrent: () => seq === token,
        startedAt: now(),
      };
      return current;
    },
    /** True when Stop aborted the round. */
    stop(): boolean {
      if (!current || now() - current.startedAt < STOP_GRACE_MS) return false;
      current.controller.abort();
      return true;
    },
    reset(): void {
      current?.controller.abort();
      seq += 1;
      current = null;
    },
    finish(round: DraftRound): void {
      if (current === round) current = null;
    },
  };
}

export type DraftRounds = ReturnType<typeof createDraftRounds>;

/** The state setters a round writes. The hook passes its own, and a test
 *  passes recorders, so the round runs with no DOM. */
export interface DraftRoundIO {
  controller: AbortController;
  /** False once a reset or a newer round ended this round. Default: true. */
  isCurrent?: () => boolean;
  onBody: (body: string) => void;
  setSteps: (update: (prev: DraftStep[]) => DraftStep[]) => void;
  setThinking: (update: (prev: string) => string) => void;
  setNotice: (notice: string | null) => void;
  onError?: (message: string) => void;
  /** The client bound. Tests keep the default. */
  totalMs?: number;
}

/**
 * One draft or refine round, with no React in it. Resolves with the final
 * draft, or with null when the round gave none.
 *
 * - The round aborts at {@link CLIENT_TOTAL_MS}, and on Stop (the caller
 *   aborts `io.controller`). The member keeps their own text (A5).
 * - A `model_*` or `server_timeout` error keeps the streamed preview (A6).
 * - On a failure, the round gives back the text of the member only when the
 *   round itself wrote to the body. A round that wrote nothing leaves the
 *   body alone, so text that the member typed meanwhile stays.
 * - A round that `isCurrent` calls ended writes nothing at all.
 */
export async function runDraftRound(
  args: ComposeAssistArgs,
  opts: { instruction: string; quote?: string | null },
  io: DraftRoundIO,
): Promise<string | null> {
  const priorBody = args.body ?? "";
  const quote = opts.quote ?? "";
  const withQuote = (text: string) =>
    quote ? `${text.replace(/\s+$/, "")}\n\n${quote}` : text;
  const { signal } = io.controller;
  const live = io.isCurrent ?? (() => true);
  // Each write goes through these, so an ended round writes nothing.
  let wrote = false;
  let streamed = "";
  const body = (text: string) => {
    if (!live()) return;
    wrote = true;
    io.onBody(text);
  };
  const restoreBody = () => {
    if (wrote) body(withQuote(priorBody));
  };
  const notice = (text: string | null) => {
    if (live()) io.setNotice(text);
  };
  const steps = (update: (prev: DraftStep[]) => DraftStep[]) => {
    if (live()) io.setSteps(update);
  };
  const timer = setTimeout(
    () => io.controller.abort(new DOMException("The draft took too long.", CLIENT_TIMEOUT)),
    io.totalMs ?? CLIENT_TOTAL_MS,
  );

  notice(null);
  try {
    // A result that arrived wins over a later abort: the stream returns at
    // `done`, so a Stop after it reaches no catch.
    const res = await composeAssistStream(args, {
      signal,
      onActivity: (evt) => steps((prev) => applyEvent(prev, evt)),
      onStatus: (stage, detail) => steps((prev) => applyStatus(prev, stage, detail)),
      onDelta: (kind, text) => {
        if (kind === "reasoning") {
          if (live()) io.setThinking((t) => (t + text).slice(-4000));
          return;
        }
        streamed += text;
        body(withQuote(streamed));
      },
    });
    if (!live()) return null;
    if (!res.draft) {
      restoreBody(); // never leave a half-streamed preview
      io.onError?.(
        res.skipped === "low_confidence"
          ? "The assistant wasn't confident enough to draft this — try adding an instruction."
          : "AI couldn't draft this — try adding a quick instruction.",
      );
      return null;
    }
    body(withQuote(res.draft));
    return res.draft;
  } catch (err) {
    if (!live()) return null;
    steps(failRunning);
    if (signal.aborted) {
      // Stop or the client bound. The member keeps their own text.
      restoreBody();
      const timedOut = (signal.reason as Error | undefined)?.name === CLIENT_TIMEOUT;
      notice(timedOut ? DRAFT_NOTICES.timeout : DRAFT_NOTICES.stopped);
      return null;
    }
    const reason = err instanceof DraftStreamError ? err.reason : null;
    if (reason) {
      // The model or the server ran out of time. The text so far stays.
      notice(DRAFT_NOTICES[reason]);
      return null;
    }
    restoreBody();
    io.onError?.((err as Error)?.message || "AI draft failed");
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export interface UseDraftSessionOptions {
  /** Push streamed/final text into the composer's body. */
  onBody: (body: string) => void;
  /** Called with the final draft once a run succeeds (mark dirty, etc.). */
  onSettled?: (draft: string) => void;
  /** Surface a failure to the composer's own error slot. */
  onError?: (message: string) => void;
}

export function useDraftSession({
  onBody,
  onSettled,
  onError,
}: UseDraftSessionOptions) {
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState<DraftStep[]>([]);
  const [thinking, setThinking] = useState("");
  const [revisions, setRevisions] = useState<DraftRevision[]>([]);
  const [activeRevision, setActiveRevision] = useState<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  /** Why the last round ended with no draft: Stop, a bound or a model error. */
  const [notice, setNotice] = useState<string | null>(null);
  const revisionSeq = useRef(0);
  const [rounds] = useState(() => createDraftRounds());

  // A composer that unmounts mid-draft aborts the round. A composer that
  // closes and stays mounted calls `reset`, which aborts it too.
  useEffect(() => () => rounds.reset(), [rounds]);

  /** Run a draft/refine round against the CURRENT body text. */
  const run = useCallback(
    async (
      args: ComposeAssistArgs,
      opts: { instruction: string; quote?: string | null } = { instruction: "" },
    ) => {
      const startedAt = Date.now();
      const priorBody = args.body ?? "";
      const round = rounds.begin();

      setBusy(true);
      setSteps([]);
      setThinking("");
      setElapsedMs(null);

      // Seed revision 0 with what the user had before the AI touched it, so
      // "back to my own text" is always one click away.
      setRevisions((prev) => {
        if (prev.length > 0 || !priorBody.trim()) return prev;
        revisionSeq.current = 1;
        return [
          { id: 1, body: priorBody, label: "Yours", source: "user", instruction: "" },
        ];
      });

      try {
        const draft = await runDraftRound(args, opts, {
          controller: round.controller,
          isCurrent: round.isCurrent,
          onBody,
          setSteps,
          setThinking,
          setNotice,
          onError,
        });
        if (draft === null || !round.isCurrent()) return null;
        revisionSeq.current += 1;
        const id = revisionSeq.current;
        setRevisions((prev) => [
          ...prev,
          {
            id,
            body: draft,
            label: `v${prev.filter((r) => r.source === "ai").length + 1}`,
            source: "ai",
            instruction: opts.instruction,
          },
        ]);
        setActiveRevision(id);
        setElapsedMs(Date.now() - startedAt);
        onSettled?.(draft);
        return draft;
      } finally {
        // An ended round leaves `busy` to the reset or the newer round.
        if (round.isCurrent()) {
          rounds.finish(round);
          setBusy(false);
        }
      }
    },
    [rounds, onBody, onSettled, onError],
  );

  /** Stop the round that runs now. The member keeps their own text. */
  const stop = useCallback(() => {
    rounds.stop();
  }, [rounds]);

  /** Flip the composer back to a saved revision (non-destructive). */
  const restore = useCallback(
    (id: number, quote?: string | null) => {
      const rev = revisions.find((r) => r.id === id);
      if (!rev) return;
      onBody(quote ? `${rev.body.replace(/\s+$/, "")}\n\n${quote}` : rev.body);
      setActiveRevision(id);
    },
    [revisions, onBody],
  );

  /** Forget the session (composer closed / a new message opened). The round
   *  that runs now aborts, and it writes nothing after this. */
  const reset = useCallback(() => {
    rounds.reset();
    setBusy(false);
    setNotice(null);
    setSteps([]);
    setThinking("");
    setRevisions([]);
    setActiveRevision(null);
    setElapsedMs(null);
    revisionSeq.current = 0;
  }, [rounds]);

  return {
    busy,
    steps,
    thinking,
    revisions,
    activeRevision,
    elapsedMs,
    notice,
    run,
    stop,
    restore,
    reset,
    /** True once at least one AI round has produced a draft. */
    hasDraft: revisions.some((r) => r.source === "ai"),
  };
}
