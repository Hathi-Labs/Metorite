// ── What /knowledge shows before it shows a space (WS-61 KB-2a) ────────────
//
// Pure. The first-open phase comes from the first `GET /knowledge/spaces`
// and from nothing else (`knowledge_base.md` §17, KB-2a):
//
//   * 404 — the app is off for the organization. `KNOWLEDGE_ENABLED` or
//     `KNOWLEDGE_ORGS` keeps it dark, and the gateway answers 404 so that a
//     dark feature does not announce itself (`routes/knowledge/flags.py`).
//   * 403 — the member has no `feature:knowledge`.
//   * 401 — the session ended. The member signs in again.
//
// ⚠️ A 404 on ONE page never means "off". A page the member cannot see, or a
// page in another organization, answers 404 too (`core.not_found`). So the
// page view reads `pageRefusal`, which has no "off" in it.

import { signInUrl } from "@/lib/orgLink";

import { KnowledgeError } from "./api";
import type { Space } from "./types";

/** What the spaces read gave: the spaces, or a refusal the page explains. */
export type SpacesRead =
  | { kind: "spaces"; spaces: Space[] }
  | { kind: "off" }
  | { kind: "no-feature" }
  | { kind: "signed-out" };

/** The phase of the whole app. */
export type KnowledgePhase = "loading" | "ready" | "off" | "no-feature" | "signed-out" | "error";

/** The words of the off state. */
export const KNOWLEDGE_OFF_TITLE = "Knowledge is not turned on for your organization yet.";

/** The words for a member with no `feature:knowledge`. */
export const NO_FEATURE_TITLE = "You do not have access to Knowledge";
export const NO_FEATURE_BODY = "Ask an admin for the Knowledge permission.";

/**
 * The words for a session that ended. The BFF answers 401 when nobody is
 * signed in (`requireIdentity`). It is not a server that did not answer.
 */
export const SIGNED_OUT_TITLE = "Your session ended.";
export const SIGNED_OUT_BODY = "Sign in again to open Knowledge.";

/**
 * The sign-in page, with the way back to this page. The one builder is
 * `signInUrl` in `lib/orgLink.ts`, which refuses an address off this site.
 */
export function signInAgainHref(here: string): string {
  return signInUrl(here, false);
}

/** The words when the spaces read failed for another reason. */
export const LOAD_FAILED_TITLE = "Knowledge did not load.";
export const LOAD_FAILED_BODY = "The server did not answer. Try again in a moment.";

/**
 * Turn a refusal of `GET /knowledge/spaces` into a phase, or `null` when the
 * error is not one the page explains as a state (the caller rethrows it).
 */
export function spacesRefusal(err: unknown): SpacesRead | null {
  if (!(err instanceof KnowledgeError)) return null;
  if (err.status === 404) return { kind: "off" };
  if (err.status === 403) return { kind: "no-feature" };
  if (err.status === 401) return { kind: "signed-out" };
  return null;
}

/** The phase from the state of the spaces read. */
export function spacesPhase(read: {
  data: SpacesRead | undefined;
  loading: boolean;
  error: string | null;
}): KnowledgePhase {
  if (read.data) {
    if (read.data.kind === "off") return "off";
    if (read.data.kind === "no-feature") return "no-feature";
    if (read.data.kind === "signed-out") return "signed-out";
    return "ready";
  }
  if (read.error) return "error";
  return "loading";
}

/** What one page read gave: the page, or "not found". Never "off". */
export type PageRead<T> = { kind: "page"; page: T } | { kind: "not-found" };

/**
 * Turn a refusal of `GET /knowledge/pages/{id}` into "not found", or `null`
 * when the error is not a 404 (the caller rethrows it, and the view shows
 * an error with a retry).
 */
export function pageRefusal<T>(err: unknown): PageRead<T> | null {
  if (err instanceof KnowledgeError && err.status === 404) return { kind: "not-found" };
  return null;
}

/** The words of a page that is not there for this member. */
export const PAGE_NOT_FOUND_TITLE = "This page is not here.";
export const PAGE_NOT_FOUND_BODY =
  "It may have moved, or you may not have access to it. Pick a space to find it.";
