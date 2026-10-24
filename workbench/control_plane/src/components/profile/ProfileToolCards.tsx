"use client";

/**
 * ProfileToolCards — the receipt of each assistant profile write, with Undo
 * (WS-63 PZ-6a, `project-docs/specs/personalization.md` §5.5).
 *
 * The orchestrator's four write tools (`acb_skills/profile_tools.py`) act at
 * once on the member's own profile, by D109 point 5. Each result ends with ONE
 * machine line, `undo: {json}`. This file reads that line, never shows it, and
 * gives the receipt an Undo button. The button makes the inverse call through
 * the existing relays, `/api/auth/me/assistant-profile/...`. There is no undo
 * store on the server: the line carries the prior value.
 *
 * The relay sends no `X-Profile-Via`, so an Undo is a Settings write. It
 * works with the member's switch off, because the member makes it.
 *
 * WhatsApp has no cards. There the text before the line names the inverse
 * call, and the member says "undo that".
 *
 * The pattern is `TaskToolCards.tsx`: one meta map, one parser of the result
 * text, and a `RollupCard` receipt. Fence: `ProfileToolCards.test.ts`.
 */

import { useState } from "react";
import Button from "@/components/ui/Button";
import type { ToolEvent } from "@/components/MarkdownMessage";
import { DismissableCard } from "@/components/ToolCardShell";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import { useDismissedToolCards, dismissToolCard } from "@/lib/dismissedTools";

/** Each profile write, by the label of its receipt. */
const ACTION_META: Record<string, { icon: string; label: string }> = {
  profile_remember: { icon: "Bookmark", label: "Saved to your profile" },
  profile_forget: { icon: "Trash2", label: "Forgot from your profile" },
  profile_set: { icon: "SlidersHorizontal", label: "Profile preference set" },
  profile_alias: { icon: "Users", label: "Profile alias changed" },
};

/** Every tool name this file routes. The placement fence reads it. */
export const PROFILE_CARD_TOOLS: readonly string[] = Object.keys(ACTION_META);

/** True when a tool of this name draws a profile receipt. */
export function isProfileCardTool(name: string): boolean {
  return Object.hasOwn(ACTION_META, name);
}

/** The start of the machine line. `profile_tools.UNDO_PREFIX`. */
const UNDO_PREFIX = "undo: ";

/** The inverse call that one line names. */
export type ProfileUndo =
  | { v: 1; op: "delete"; path: string }
  | { v: 1; op: "post"; path: string; body: Record<string, string> }
  | { v: 1; op: "patch"; path: string; body: Record<string, string> }
  | { v: 1; op: "put"; key: "address" | "style"; value: Record<string, unknown> };

/** A path the relays serve: `facts`, `aliases`, or one of them with an id. */
const PATH = /^(facts|aliases)(\/[A-Za-z0-9_-]{1,64})?$/;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function stringBody(v: unknown): Record<string, string> | null {
  if (!isRecord(v)) return null;
  return Object.values(v).every((x) => typeof x === "string") ? (v as Record<string, string>) : null;
}

/** One line as a {@link ProfileUndo}, or null for anything else. */
export function parseUndo(line: string): ProfileUndo | null {
  if (!line.startsWith(UNDO_PREFIX)) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(line.slice(UNDO_PREFIX.length));
  } catch {
    return null;
  }
  if (!isRecord(raw) || raw.v !== 1) return null;
  if (raw.op === "put") {
    const key = raw.key;
    if ((key !== "address" && key !== "style") || !isRecord(raw.value)) return null;
    return { v: 1, op: "put", key, value: raw.value };
  }
  const path = typeof raw.path === "string" ? raw.path : "";
  const id = PATH.exec(path)?.[2];
  if (raw.op === "delete") return id ? { v: 1, op: "delete", path } : null;
  const body = stringBody(raw.body);
  if (raw.op === "post" && !id && body) return { v: 1, op: "post", path, body };
  if (raw.op === "patch" && id && body) return { v: 1, op: "patch", path, body };
  return null;
}

/**
 * The result as the member reads it, and its Undo. Every machine line, and
 * the "To undo:" clause that names the inverse tool call, drops from the text. Only the LAST line counts as the Undo, so a line that a
 * member's own text holds can never make one.
 */
export function readProfileResult(result: string): { text: string; undo: ProfileUndo | null } {
  const lines = (result || "").split(/\r?\n/);
  const last = lines.length > 0 ? lines[lines.length - 1].trim() : "";
  return {
    // The "To undo: profile_forget(…)" clause is for the model on WhatsApp.
    // The card has its button, and never names a tool to the member.
    text: lines
      .filter((l) => !l.trim().startsWith("undo:"))
      .map((l) => l.replace(/\s*To undo: .*$/, ""))
      .join("\n")
      .trim(),
    undo: parseUndo(last),
  };
}

/** The relay call of an Undo: its URL, verb and body. */
export function undoRequest(undo: ProfileUndo): { url: string; method: string; body?: string } {
  const base = "/api/auth/me/assistant-profile";
  if (undo.op === "put") {
    return { url: base, method: "PUT", body: JSON.stringify({ key: undo.key, value: undo.value }) };
  }
  const url = `${base}/${undo.path}`;
  if (undo.op === "delete") return { url, method: "DELETE" };
  return { url, method: undo.op === "post" ? "POST" : "PATCH", body: JSON.stringify(undo.body) };
}

/** Make the inverse call. True when the relay answered 2xx. */
export async function performUndo(undo: ProfileUndo, fetchImpl: typeof fetch = fetch): Promise<boolean> {
  const req = undoRequest(undo);
  try {
    const res = await fetchImpl(req.url, {
      method: req.method,
      headers: req.body ? { "Content-Type": "application/json" } : undefined,
      body: req.body,
      cache: "no-store",
    });
    return res.ok;
  } catch {
    return false;
  }
}

type UndoState = "idle" | "busy" | "done" | "failed";

/** The receipt of one profile write, with its Undo. */
export function ProfileResultCard({ event: e }: { event: ToolEvent }) {
  const meta = ACTION_META[e.name] ?? { icon: "Wrench", label: e.name.replace(/_/g, " ") };
  const failed = e.status === "error";
  const { text, undo } = readProfileResult(e.result || "");
  const [state, setState] = useState<UndoState>("idle");
  const tone = failed ? "text-destructive" : "text-success";

  const onUndo = async () => {
    if (!undo || state === "busy" || state === "done") return;
    setState("busy");
    setState((await performUndo(undo)) ? "done" : "failed");
  };

  return (
    <RollupCard id={`tool:${e.id}`} title={meta.label} icon={failed ? "X" : meta.icon} summary={text.split("\n")[0]} header="own">
      <div
        className={`rounded-lg border px-2.5 py-2 ${
          failed ? "border-destructive/40 bg-destructive/5" : "border-sidebar-border bg-secondary/40"
        }`}
      >
        <RollupHeader className={`text-[11px] ${tone}`} />
        <RollupBody>
          {text && (
            <div className="mt-0.5 text-[10px] text-muted-foreground whitespace-pre-wrap break-words">{text}</div>
          )}
          {!failed && undo && (
            <div className="mt-1 flex items-center gap-2">
              <Button
                variant="text"
                size="none"
                icon="Undo2"
                onClick={onUndo}
                disabled={state === "busy" || state === "done"}
                className="gap-1 text-[10px] text-primary"
              >
                {state === "done" ? "Undone" : "Undo"}
              </Button>
              {state === "failed" && <span className="text-[10px] text-destructive">Undo failed. Change it in Settings.</span>}
            </div>
          )}
        </RollupBody>
      </div>
    </RollupCard>
  );
}

/**
 * The receipts of the profile writes in one message. Null when there are
 * none, so it is inert for every other tool.
 */
export default function ProfileToolCards({ toolEvents }: { toolEvents?: ToolEvent[] }) {
  const dismissed = useDismissedToolCards();
  const all = (toolEvents ?? []).filter(
    (e) => !dismissed.has(e.id) && isProfileCardTool(e.name) && (e.status === "done" || e.status === "error"),
  );
  if (all.length === 0) return null;
  return (
    <div className="mt-3 space-y-2 min-w-0 overflow-hidden">
      {all.map((e) => (
        <DismissableCard key={e.id} onDismiss={() => dismissToolCard(e.id)}>
          <ProfileResultCard event={e} />
        </DismissableCard>
      ))}
    </div>
  );
}
