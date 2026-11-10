/**
 * The member's number system, on the client (owner, 2026-10-19).
 *
 * Spec: `project-docs/specs/whatsapp_assistant_channel.md` §13.10.
 *
 * The gateway resolves it (`acb_common.number_system.resolve`): the member's
 * override wins, then the org's value, then "indian". This module never
 * resolves. It reads `resolved` from `GET /api/settings/number-system`, once
 * a page, and a save here hands the new answer to every chart at once.
 *
 * A failed read is the default, "indian". A preference never costs a chart.
 *
 * Fence: `src/lib/numberSystem.test.ts`.
 */
import { useEffect, useState } from "react";

import { DEFAULT_NUMBER_SYSTEM, numberSystem } from "./numberFormat.mjs";

export type NumberSystem = "indian" | "international";

/** What `GET /api/settings/number-system` answers. */
export interface NumberSettings {
  /** The org's value. null: no admin chose, so the default applies. */
  org: NumberSystem | null;
  /** The member's override. null: "Use the organization's setting". */
  member: NumberSystem | null;
  /** What the member gets. The gateway's answer, never computed here. */
  resolved: NumberSystem;
}

export const NUMBER_SYSTEM_LABELS: Record<NumberSystem, string> = {
  indian: "Indian (lakh, crore)",
  international: "International (million, billion)",
};

/** The member select's value for "Use the organization's setting". */
export const USE_ORG = "org";

/** The member select's value: the override, or "Use the organization's setting". */
export function memberChoice(s: NumberSettings): NumberSystem | typeof USE_ORG {
  return s.member ?? USE_ORG;
}

/** The override a member select value saves. null clears it. */
export function memberValue(choice: string): NumberSystem | null {
  return choice === "indian" || choice === "international" ? choice : null;
}

/** "Use the organization's setting", with what the organization has now. */
export function orgChoiceLabel(s: NumberSettings): string {
  return `Use the organization's setting (${NUMBER_SYSTEM_LABELS[s.org ?? DEFAULT_NUMBER_SYSTEM]})`;
}

/** The member select's options: "Use the organization's setting" first. */
export function memberOptions(s: NumberSettings | null): { value: string; label: string }[] {
  return [
    { value: USE_ORG, label: s ? orgChoiceLabel(s) : "Use the organization's setting" },
    ...(["indian", "international"] as const).map((v) => ({ value: v, label: NUMBER_SYSTEM_LABELS[v] })),
  ];
}

const PATH = "/api/settings/number-system";

/** The answer of the gateway, read field by field. */
export function settingsFrom(raw: unknown): NumberSettings {
  const o = (raw ?? {}) as Record<string, unknown>;
  const pick = (v: unknown): NumberSystem | null =>
    v === "indian" || v === "international" ? v : null;
  return {
    org: pick(o.org),
    member: pick(o.member),
    resolved: numberSystem(o.resolved),
  };
}

let current: NumberSystem | null = null;
let pending: Promise<NumberSystem> | null = null;
const listeners = new Set<(s: NumberSystem) => void>();

/** Every chart on the page redraws in `s`. */
export function publishNumberSystem(s: NumberSystem): void {
  current = s;
  for (const fn of listeners) fn(s);
}

/** Hear each new answer. Returns the call that stops it. */
export function subscribeNumberSystem(fn: (s: NumberSystem) => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Read the settings. Throws on a failed read, so a form can say so. */
export async function fetchNumberSettings(): Promise<NumberSettings> {
  const r = await fetch(PATH, { cache: "no-store" });
  if (!r.ok) throw new Error(`Could not load the number format (${r.status})`);
  const s = settingsFrom(await r.json());
  publishNumberSystem(s.resolved);
  return s;
}

async function save(path: string, value: NumberSystem | null): Promise<NumberSettings> {
  const r = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : `Could not save the number format (${r.status})`);
  }
  const s = settingsFrom(body);
  publishNumberSystem(s.resolved);
  return s;
}

/** Set the org's value. The gateway refuses a member who is not an admin. */
export function saveOrgNumberSystem(value: NumberSystem): Promise<NumberSettings> {
  return save(PATH, value);
}

/** Set the member's override, or clear it with null. */
export function saveMyNumberSystem(value: NumberSystem | null): Promise<NumberSettings> {
  return save(`${PATH}/me`, value);
}

/** One read a page, shared by every chart. */
function load(): Promise<NumberSystem> {
  if (!pending) {
    pending = fetchNumberSettings()
      .then((s) => s.resolved)
      .catch(() => {
        // The default, and a later mount may ask again.
        pending = null;
        return DEFAULT_NUMBER_SYSTEM;
      });
  }
  return pending;
}

/**
 * The member's number system, for a chart. `undefined` until the first read
 * answers: the chart then draws with its spec's own `numbering`.
 */
export function useNumberSystem(): NumberSystem | undefined {
  const [s, setS] = useState<NumberSystem | undefined>(current ?? undefined);
  useEffect(() => {
    const stop = subscribeNumberSystem(setS);
    if (current === null && typeof fetch === "function") {
      void load().then((v) => setS((old) => old ?? v));
    }
    return stop;
  }, []);
  return s;
}

/** Tests only: forget the page's answer. */
export function resetNumberSystemForTests(): void {
  current = null;
  pending = null;
  listeners.clear();
}
