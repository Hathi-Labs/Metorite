/**
 * The decisions of the GitHub card and of the GitHub App connect flow
 * (WS-54 GH-3 and GH-7, `project-docs/specs/github_integration.md` §7).
 *
 * Pure functions only. `components/GitHubAppConnect.tsx` draws what these
 * decide, and the BFF routes under `app/api/integrations/github/` use the
 * URL checks. Fence: `githubConnect.test.ts`.
 *
 * The flow: Connect GitHub asks the BFF for the install link, the browser
 * goes to GitHub, and GitHub sends it back through the callback to
 * `/integrations?view=connections&github=<code>`. The code `confirm` carries
 * `&account=<login>` and opens the dialog "Connect <login> to <org>?". Only
 * that click writes the connection.
 */

/** Every code the gateway callback and the confirm can answer. It mirrors
 *  `CODES` in `gateway/routes/github/connect.py`, and a test reads both. */
export const GITHUB_RETURN_CODES = [
  "forbidden",
  "expired",
  "bad_state",
  "requested",
  "failed",
  "not_yours",
  "not_admin",
  "suspended",
  "confirm",
  "other_org",
  "connected",
  "unavailable",
  "not_configured",
] as const;

export type GitHubReturnCode = (typeof GITHUB_RETURN_CODES)[number];

export interface GitHubReturn {
  code: GitHubReturnCode;
  /** The GitHub account of the install, for `confirm` only. */
  account?: string;
}

/** A GitHub account name: letters, digits and single hyphens, 1 to 39. */
const LOGIN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/;

export function isGitHubLogin(value: unknown): value is string {
  return typeof value === "string" && LOGIN.test(value);
}

/** The return of the connect flow in a query, or null. */
export function parseGitHubReturn(params: URLSearchParams): GitHubReturn | null {
  const code = params.get("github");
  if (!code || !(GITHUB_RETURN_CODES as readonly string[]).includes(code)) return null;
  const typed = code as GitHubReturnCode;
  if (typed === "confirm") {
    const account = params.get("account");
    return isGitHubLogin(account) ? { code: typed, account } : { code: "failed" };
  }
  return { code: typed };
}

/** The relative path of one return. The callback route builds only these. */
export function githubReturnPath(r: GitHubReturn): string {
  const base = `/integrations?view=connections&github=${r.code}`;
  return r.code === "confirm" && isGitHubLogin(r.account) ? `${base}&account=${r.account}` : base;
}

export type Tone = "success" | "error" | "info";

/** One fixed sentence for each code (§7.3). `confirm` opens a dialog. */
export const GITHUB_RETURN_TEXT: Record<GitHubReturnCode, { tone: Tone; text: string }> = {
  forbidden: { tone: "error", text: "Only an admin of Integrations can connect GitHub." },
  expired: {
    tone: "error",
    text: "The connect took too long or started in another window. Press Connect GitHub again.",
  },
  bad_state: {
    tone: "error",
    text: "This link was not started by you in this organization. Press Connect GitHub again.",
  },
  requested: {
    tone: "info",
    text: "Your GitHub organization owner must approve the install. Press Connect again after they approve.",
  },
  failed: { tone: "error", text: "Metorite could not finish the GitHub connect. Try again." },
  not_yours: {
    tone: "error",
    text: "You cannot see that GitHub installation. Sign in to GitHub as a person who installed the App.",
  },
  not_admin: {
    tone: "error",
    text: "Only an owner of the GitHub account can connect it. Ask an owner of the GitHub organization.",
  },
  suspended: {
    tone: "error",
    text: "The Metorite App is suspended on that GitHub account. Unsuspend it at GitHub first.",
  },
  confirm: { tone: "info", text: "Confirm the GitHub account to connect." },
  other_org: {
    tone: "error",
    text: "This GitHub account is connected to another Metorite organization.",
  },
  connected: { tone: "success", text: "GitHub is connected." },
  unavailable: { tone: "error", text: "Metorite could not reach its store. Try again in a minute." },
  not_configured: { tone: "error", text: "The GitHub App is not set up on this server yet." },
};

export interface DialogCopy {
  title: string;
  body: string;
  note: string | null;
  confirmLabel: string;
}

/** The dialog after the return: the click that writes the connection. */
export function confirmCopy(account: string, orgName: string | null): DialogCopy {
  const org = orgName?.trim() ? orgName.trim() : "this Metorite organization";
  return {
    title: `Connect ${account} to ${org}?`,
    body:
      `Metorite will read the pull requests and the checks of the repositories that you ` +
      `picked on GitHub for ${account}. It writes nothing to GitHub.`,
    note: null,
    confirmLabel: "Connect",
  };
}

/** The dialog BEFORE the admin leaves for GitHub, when a token is connected
 *  now (§7.4): the App connection takes the place of the token. */
export function replaceTokenCopy(): DialogCopy {
  return {
    title: "Connect the Metorite GitHub App?",
    body: "GitHub opens next. Pick the account and the repositories that Metorite may read.",
    note: "This replaces the token that is connected now.",
    confirmLabel: "Continue to GitHub",
  };
}

/** The connection facts that `GET /api/integrations/github/account` sends. */
export interface GitHubAccount {
  connected: boolean;
  status: string;
  login: string | null;
  auth: "app" | "token" | null;
  account_type: string | null;
  repository_selection: string | null;
  installation_url: string | null;
  connected_by: string | null;
  organization_name?: string | null;
  app_available?: boolean;
}

export type CardMode = "app" | "token" | "none" | "attention";

export interface CardView {
  mode: CardMode;
  /** The line under the GitHub mark. */
  headline: string;
  /** The label of the main button, or null for no button. */
  connectLabel: string | null;
  /** The Connect button opens the "replaces the token" dialog first. */
  warnsOfToken: boolean;
  /** Disconnect shows. */
  canDisconnect: boolean;
}

const SELECTION: Record<string, string> = {
  all: "all repositories",
  selected: "the repositories you picked",
};

/** What the GitHub card shows (§7.1, §7.5, §7.6). */
export function githubCardView(info: GitHubAccount | null, canManage: boolean): CardView {
  const appOn = Boolean(info?.app_available);
  const connectLabel = (label: string) => (canManage && appOn ? label : null);
  if (!info || info.status === "not_connected" || info.status === "disconnected") {
    return {
      mode: "none",
      headline: "GitHub is not connected.",
      connectLabel: connectLabel("Connect GitHub"),
      warnsOfToken: false,
      canDisconnect: false,
    };
  }
  if (info.auth === "app") {
    if (!info.connected) {
      return {
        mode: "attention",
        headline: "The Metorite App is suspended or needs attention at GitHub.",
        connectLabel: connectLabel("Reconnect GitHub"),
        warnsOfToken: false,
        canDisconnect: canManage,
      };
    }
    const reach = SELECTION[info.repository_selection ?? ""] ?? "its repositories";
    return {
      mode: "app",
      headline: `Connected to ${info.login ?? "GitHub"} through the Metorite App. Metorite reads ${reach}.`,
      connectLabel: connectLabel("Change on GitHub"),
      warnsOfToken: false,
      canDisconnect: canManage,
    };
  }
  return {
    mode: "token",
    headline: info.login
      ? `Connected with a personal access token of ${info.login}.`
      : "Connected with a personal access token.",
    connectLabel: connectLabel("Connect GitHub"),
    warnsOfToken: info.connected,
    canDisconnect: canManage,
  };
}

/** An install link of a GitHub App, and nothing else (the start BFF route). */
export function isGitHubInstallUrl(raw: unknown): raw is string {
  if (typeof raw !== "string" || raw.length > 4096) return false;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== "https:" || url.hostname !== "github.com" || url.port) return false;
  if (url.username || url.password || url.hash) return false;
  if (!/^\/apps\/[A-Za-z0-9-]{1,100}\/installations\/new$/.test(url.pathname)) return false;
  const names = [...url.searchParams.keys()];
  return names.length === 1 && names[0] === "state";
}

/** An uninstall page at GitHub (§7.5), or null when the URL is not one. */
export function safeInstallationUrl(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.hostname !== "github.com") return null;
  const ok =
    /^\/organizations\/[A-Za-z0-9-]{1,39}\/settings\/installations\/[0-9]{1,19}$/.test(url.pathname) ||
    /^\/settings\/installations\/[0-9]{1,19}$/.test(url.pathname);
  return ok && !url.search && !url.hash ? url.toString() : null;
}

// ── The Advanced token (GH-7, §7.6) ─────────────────────────────────────────

/** The fold of the card that holds the token field (§7.6). */
export const TOKEN_FOLD_LABEL = "Advanced: use a personal access token";

/** What the fold says, so a member knows when to use it. */
export const TOKEN_FOLD_HELP =
  "Use a token when an agent must read a private repository, or when your GitHub account will not install the App. " +
  "The token replaces the App connection.";

/** The longest token the card sends. A GitHub token is far shorter. */
export const MAX_TOKEN_CHARS = 255;

/** Why a typed token cannot be sent, or null when it can. */
export function tokenProblem(raw: string): string | null {
  const token = raw.trim();
  if (!token) return "Paste a token first.";
  if (token.length > MAX_TOKEN_CHARS || /\s/.test(token)) return "That is not a GitHub token.";
  return null;
}

/** The words of a refusal of the confirm or the start BFF routes. */
export function refusalText(detail: unknown): string {
  if (typeof detail === "string" && (GITHUB_RETURN_CODES as readonly string[]).includes(detail)) {
    return GITHUB_RETURN_TEXT[detail as GitHubReturnCode].text;
  }
  if (detail === "not_available") return "The GitHub App is not turned on for this server.";
  if (detail === "cross_origin") return "Open Integrations in Metorite and try again.";
  return GITHUB_RETURN_TEXT.failed.text;
}
