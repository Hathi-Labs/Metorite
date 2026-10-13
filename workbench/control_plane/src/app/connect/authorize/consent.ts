/**
 * The consent page of an outside agent, as pure decisions (WS-55 EA-4a).
 *
 * Spec: `project-docs/specs/external_agent_access.md` §10.5. The page
 * (`ConsentScreen.tsx`) only draws what this module decides, so
 * `consent.test.ts` can run every rule in the node-env vitest.
 *
 * ⚠️ The approval must send the SAME request that `check` saw. The consent
 * ticket binds `client_id`, `redirect_uri`, `code_challenge`, `state`,
 * `resource` and `scope`, and the gateway refuses an approval that differs.
 * So `approveBody` spreads the one parsed request, and adds only the
 * member's choices and the ticket.
 */

/** The OAuth parameters of an authorize request that the page passes on. */
export const AUTHORIZE_KEYS = [
  "client_id",
  "redirect_uri",
  "response_type",
  "code_challenge",
  "code_challenge_method",
  "resource",
  "scope",
  "state",
] as const;

export type AuthorizeKey = (typeof AUTHORIZE_KEYS)[number];
export type AuthorizeRequest = Partial<Record<AuthorizeKey, string>>;

export type SearchParams = Record<string, string | string[] | undefined>;

export type ParsedRequest =
  | { ok: true; request: AuthorizeRequest }
  | { ok: false; message: string };

/** The longest value the page reads. The gateway caps each one lower. */
const MAX_VALUE = 4096;

/**
 * The authorize request in the URL, or why the page cannot use it.
 *
 * A key sent twice is refused (RFC 6749 §3.1), because the page cannot know
 * which value the agent meant. A key outside `AUTHORIZE_KEYS` is dropped.
 */
export function parseAuthorizeRequest(params: SearchParams): ParsedRequest {
  const request: AuthorizeRequest = {};
  for (const name of AUTHORIZE_KEYS) {
    const value = params[name];
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      return { ok: false, message: `The agent sent "${name}" more than once.` };
    }
    if (value.length > MAX_VALUE) {
      return { ok: false, message: `The agent sent a "${name}" that is too long.` };
    }
    request[name] = value;
  }
  if (!request.client_id || !request.redirect_uri) {
    return {
      ok: false,
      message: "This link from the agent is not complete. Start again from the agent.",
    };
  }
  return { ok: true, request };
}

/** What the gateway's `check` answers (`AuthorizeView`). */
export interface CheckView {
  client: { client_id: string; name: string; origin: string };
  redirect_uri: string;
  redirect_display: string;
  redirect_app: boolean;
  consent_ticket: string;
  scopes: string[];
  resource: string;
  state: string | null;
  issuer: string;
  deny_redirect_to: string;
}

export function isCheckView(value: unknown): value is CheckView {
  const v = value as Partial<CheckView> | null;
  return (
    !!v &&
    typeof v.consent_ticket === "string" &&
    typeof v.deny_redirect_to === "string" &&
    typeof v.redirect_display === "string" &&
    typeof v.redirect_app === "boolean" &&
    Array.isArray(v.scopes) &&
    !!v.client &&
    typeof v.client.name === "string" &&
    typeof v.client.origin === "string"
  );
}

export const OFFLINE_ACCESS = "offline_access";

/** The words of each scope. A scope the page does not know shows its name. */
export const SCOPE_COPY: Record<string, { label: string; detail: string }> = {
  "projects:read": {
    label: "Read your projects and tasks",
    detail: "Only the projects and tasks you can see in Metorite.",
  },
  "projects:write": {
    label: "Change your projects and tasks",
    detail: "Create and update tasks as you, where you may change them.",
  },
  "context:read": {
    label: "Read company context",
    detail: "The organization name, its branding and the memory you may read.",
  },
  [OFFLINE_ACCESS]: {
    label: "Stay connected",
    detail: "The agent keeps access until you or an admin revoke it.",
  },
};

export interface ScopeRow {
  scope: string;
  label: string;
  detail: string;
  /** `offline_access` has no box. The member can narrow the others only. */
  fixed: boolean;
}

export function scopeRows(scopes: readonly string[]): ScopeRow[] {
  return scopes.map((scope) => {
    const copy = SCOPE_COPY[scope] ?? { label: scope, detail: "" };
    return { scope, ...copy, fixed: scope === OFFLINE_ACCESS };
  });
}

/** The scopes a new screen starts with: every requested scope. */
export function initialChoice(view: CheckView): Set<string> {
  return new Set(view.scopes.filter((s) => s !== OFFLINE_ACCESS));
}

/**
 * The scopes the approval sends: the chosen ones that the agent asked for,
 * and `offline_access` when the agent asked for it. Never a scope the agent
 * did not ask for, so a narrowing can never widen.
 */
export function grantedScopes(view: CheckView, chosen: ReadonlySet<string>): string[] {
  return view.scopes.filter((s) => s === OFFLINE_ACCESS || chosen.has(s));
}

/** Approve needs one resource scope at least. `offline_access` alone grants nothing. */
export function canApprove(view: CheckView, chosen: ReadonlySet<string>): boolean {
  return grantedScopes(view, chosen).some((s) => s !== OFFLINE_ACCESS);
}

/** The most projects the picker lists. The gateway caps pins at 50 too. */
export const MAX_PIN_CHOICES = 50;

export interface PinChoice {
  id: string;
  name: string;
  depth: number;
}

interface TreeNode {
  id?: unknown;
  name?: unknown;
  archived_at?: unknown;
  children?: unknown;
}

/**
 * The projects a member may pin, in tree order, at most 50.
 *
 * `GET /projects/tree` never holds a personal project (`tree.py` reads
 * `personal_owner IS NULL`), so every row here is a team project. An archived
 * node and its subtree stay out. A pin covers its subtree at the gateway
 * (`external_agent_access.md` §7.3), so a space or a folder is a fair pin.
 */
export function pinChoices(tree: unknown, limit = MAX_PIN_CHOICES): PinChoice[] {
  const rows = (tree as { rows?: unknown } | null)?.rows;
  const out: PinChoice[] = [];
  const walk = (nodes: unknown, depth: number) => {
    if (!Array.isArray(nodes)) return;
    for (const node of nodes as TreeNode[]) {
      if (out.length >= limit) return;
      if (!node || typeof node.id !== "string" || typeof node.name !== "string") continue;
      if (node.archived_at) continue;
      out.push({ id: node.id, name: node.name, depth });
      walk(node.children, depth + 1);
    }
  };
  walk(rows, 0);
  return out;
}

export interface Choice {
  scopes: ReadonlySet<string>;
  pins: readonly string[];
  includePersonal: boolean;
}

/** The body of `approve`: the SAME request, the ticket, and the choices. */
export function approveBody(request: AuthorizeRequest, view: CheckView, choice: Choice) {
  return {
    ...request,
    consent_ticket: view.consent_ticket,
    scopes: grantedScopes(view, choice.scopes),
    pinned_projects: [...choice.pins],
    include_personal: choice.includePersonal,
  };
}

/** The name of the organization that approve binds (EA3-25), or null. */
export function organizationName(org: { display_name?: string; slug?: string } | undefined): string | null {
  const name = (org?.display_name || org?.slug || "").trim();
  return name || null;
}

export interface Refusal {
  message: string;
  /** Where "Return to the agent" goes: the agent's own error redirect. */
  redirectTo: string | null;
}

/** What the page says when `check` or `approve` does not answer 200. */
export function refusalFor(status: number, body: unknown): Refusal {
  const detail = (body as { detail?: unknown } | null)?.detail;
  const d = detail && typeof detail === "object" ? (detail as Record<string, unknown>) : null;
  const redirectTo = d && typeof d.redirect_to === "string" ? d.redirect_to : null;
  const description = d && typeof d.error_description === "string" ? d.error_description : null;
  if (status === 400 && d?.error === "invalid_request" && !redirectTo &&
      description?.startsWith("The consent ticket")) {
    return {
      message: "This approval has expired or was already used. Start again from the agent.",
      redirectTo: null,
    };
  }
  if (status === 400) {
    return {
      message: `Metorite refused the agent's request. ${description ?? ""}`.trim(),
      redirectTo,
    };
  }
  if (status === 401) return { message: "Sign in again to continue.", redirectTo: null };
  if (status === 403) {
    return { message: "Only an active member of an organization can approve an agent.", redirectTo: null };
  }
  if (status === 404) {
    return { message: "Outside agents cannot connect to Metorite yet.", redirectTo: null };
  }
  if (status === 422) {
    return { message: "The agent's request is not valid. Start again from the agent.", redirectTo: null };
  }
  if (status === 503) {
    return { message: "Metorite is busy. Try again in a moment.", redirectTo: null };
  }
  return { message: "Metorite could not answer. Try again in a moment.", redirectTo: null };
}
