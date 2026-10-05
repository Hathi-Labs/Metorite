// WS-17 EM-T3b — the connect UI of the Email app.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.3, "Done when".
// vitest here runs in the node environment, so it cannot render a page. Each
// done-when is held in one of two ways:
//
// - a DECISION lives in `connect.ts` and these cases run it;
// - a WIRING (which page calls which decision) is held by a source scan of
//   code with the comments stripped, so a comment cannot satisfy it.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { ConnectChoices } from "../components/ConnectChoices";
import { ConnectEmptyState } from "../components/ConnectEmptyState";
import { ImportRangeStep } from "../components/ImportRangeStep";
import { WorkspaceAdminSteps } from "../components/WorkspaceAdminHelp";
import {
  ADMIN_APPROVAL_AFTER_DECLINE,
  CONNECT_PROVIDERS,
  DEFAULT_IMPORT_MONTHS,
  FAILED_READ_AVAILABILITY,
  PROVIDER_NAME,
  RECONNECT_LABEL,
  UNAVAILABLE_NOTE,
  WORKSPACE_ADMIN_HELP,
  alreadyConnectedCopy,
  callbackProvider,
  connectChoices,
  liveProviders,
  mapProviderAvailability,
  offersConnectAgain,
  offersTryAgain,
  showsWorkspaceAdminHelp,
  signInLine,
  type ProviderAvailability,
  adminApprovalHelp,
  IMPORT_MONTHS_STORAGE_KEY,
  IMPORT_RANGE_CHOICES,
  IMPORT_RANGE_COPY,
  MAX_IMPORT_MONTHS,
  isImportMonths,
  rangeStepProviderFrom,
  rememberImportMonths,
  storedImportMonths,
  adminConsentMailto,
  adminConsentUrl,
  callbackView,
  connectQuery,
  DISCONNECT_FALLBACK,
  disconnectCopy,
  disconnectFailureText,
  emailSurface,
  firstSyncTick,
  retryTarget,
  wantsConnectChoices,
  finishedFirstSync,
  firstSyncCopy,
  isFirstSyncPending,
  mapMailAppInfo,
  reconnectProvider,
  shouldPollFirstSync,
} from "./connect";
import { firstSyncPanels } from "./onboarding";

const EMAIL_APP = join(__dirname, "..");

/** The live sets of two answers of the capability read (WS-17 EM-G8). */
const MS_ONLY = liveProviders({ microsoft: true, gmail: false });
const BOTH = liveProviders({ microsoft: true, gmail: true });

function read(rel: string): string {
  return readFileSync(join(EMAIL_APP, rel), { encoding: "utf-8" });
}

/** Source with `//` and block comments removed, so prose cannot pass a scan. */
function codeOnly(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
}

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return walk(full);
    // A test file names the banned path in order to ban it.
    return /\.(tsx?|jsx?)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

/** The body of `const <name> = useCallback(...)`, brace-matched. */
function callbackBody(src: string, name: string): string {
  const start = src.indexOf(`const ${name} = useCallback`);
  expect(start, `${name} not found`).toBeGreaterThan(-1);
  const open = src.indexOf("{", src.indexOf("=>", start));
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(open, i + 1);
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

const PAGE = codeOnly(read("page.tsx"));
const CALLBACK_PAGE = codeOnly(read("oauth/callback/page.tsx"));

describe("no path in app/email reaches Integrations (done-when 1)", () => {
  it("no file under app/email names /integrations", () => {
    const offenders = walk(EMAIL_APP).filter((f) =>
      /\/integrations\b/.test(readFileSync(f, { encoding: "utf-8" })),
    );
    expect(offenders).toEqual([]);
  });

  it("page.tsx has no Configure OAuth text and no integrations status fetch", () => {
    const raw = read("page.tsx");
    expect(raw).not.toMatch(/Configure OAuth/i);
    expect(raw).not.toContain("/api/integrations/status");
  });
});

describe("the choices (done-when 3)", () => {
  it("handleConnect has no IMAP branch and one navigation to the BFF", () => {
    const body = callbackBody(PAGE, "handleConnect");
    expect(body.toLowerCase()).not.toContain("imap");
    expect(body).toContain("window.location.href = `/api/email/oauth/${provider}/authorize?");
    expect(body.match(/\/oauth\//g)).toHaveLength(1);
    expect(PAGE).not.toContain('handleConnect("imap")');
  });

  // Inverted by WS-17 EM-G8 (GM-26): the list no longer fixes `available`.
  // The capability read decides, and the EM-G8 cases below hold each answer.
  it("lists Microsoft then Gmail, fixes no availability, and offers no IMAP", () => {
    expect(CONNECT_PROVIDERS.map((p) => p.id)).toEqual(["microsoft", "gmail"]);
    const [ms] = CONNECT_PROVIDERS;
    expect(ms.label).toMatch(/Microsoft 365/);
    for (const p of CONNECT_PROVIDERS) {
      expect(Object.keys(p), p.id).not.toContain("available");
      expect(Object.keys(p), p.id).not.toContain("note");
    }
    expect(CONNECT_PROVIDERS.some((p) => (p.id as string) === "imap")).toBe(false);
    expect(connectChoices({ microsoft: true, gmail: true }).map((p) => p.id)).toEqual(["microsoft", "gmail"]);
  });

  it("the empty state and the add-account dialog draw the same list", () => {
    expect(codeOnly(read("components/ConnectEmptyState.tsx"))).toContain("<ConnectChoices");
    expect(PAGE).toMatch(/<Modal[\s\S]*?<ConnectChoices[\s\S]*?<\/Modal>/);
    const choices = codeOnly(read("components/ConnectChoices.tsx"));
    expect(choices).toContain("disabled={!p.available}");
    expect(choices).toContain("{p.note}");
    expect(choices).toContain("choices={connectChoices(availability)}");
  });

  it("the empty state replaces the panes when there is no mailbox", () => {
    expect(PAGE).toMatch(/if \(noAccounts\) \{\s*return \(\s*<ConnectEmptyState/);
    expect(read("components/ConnectEmptyState.tsx")).toContain("Connect your email");
  });
});

describe("the callback page gives guided copy (done-when 4)", () => {
  const base = { accountId: null, email: null, provider: "microsoft" as const };

  it("admin_consent_required explains IT approval, never 'unexpected error'", () => {
    const v = callbackView({ ...base, error: "admin_consent_required" });
    expect(v.kind).toBe("admin_consent_required");
    expect(v.title).toMatch(/organization needs to approve/i);
    expect(v.body).toMatch(/IT admin/);
    expect(`${v.title} ${v.body}`).not.toMatch(/unexpected error/i);
  });

  it("consent_declined says the member cancelled, never 'unexpected error'", () => {
    const v = callbackView({ ...base, error: "consent_declined" });
    expect(v.kind).toBe("consent_declined");
    expect(v.title).toMatch(/cancelled/i);
    expect(`${v.title} ${v.body}`).not.toMatch(/unexpected error/i);
  });

  it("an unknown code is generic and helpful, and never echoes text", () => {
    const sentence = "Microsoft OAuth is not configured. Go to Integrations → APIs";
    const v = callbackView({ ...base, error: sentence });
    expect(v.kind).toBe("unknown");
    expect(v.reference).toBeUndefined();
    expect(`${v.title} ${v.body}`).not.toContain("Integrations");
    expect(`${v.title} ${v.body}`).not.toMatch(/unexpected error/i);

    const coded = callbackView({ ...base, error: "provider_error" });
    expect(coded.reference).toBe("provider_error");
  });

  it("a code that a retry fixes says so", () => {
    expect(callbackView({ ...base, error: "invalid_state" }).kind).toBe("retry");
    expect(callbackView({ ...base, error: "token_exchange_failed" }).kind).toBe("retry");
  });

  it("success reads Connected as <address>", () => {
    const v = callbackView({ error: null, accountId: "a1", email: "ravi@contoso.test", provider: "microsoft" });
    expect(v.kind).toBe("connected");
    expect(v.title).toBe("Connected as ravi@contoso.test");
  });

  it("the page draws the view and the admin steps, with status tokens only", () => {
    expect(CALLBACK_PAGE).toContain("callbackView(");
    expect(CALLBACK_PAGE).toContain("<AdminConsentSteps");
    expect(CALLBACK_PAGE).toContain("adminConsentMailto(link)");
    expect(CALLBACK_PAGE).toContain("navigator.clipboard.writeText(link)");
    expect(CALLBACK_PAGE).toContain("getMailAppInfo()");
    expect(read("oauth/callback/page.tsx")).not.toMatch(/emerald-/);
    expect(CALLBACK_PAGE).not.toContain("An unexpected error occurred");
  });
});

describe("the admin-consent link comes from the server (done-when 4)", () => {
  const app = {
    clientId: "11111111-aaaa-bbbb-cccc-222222222222",
    redirectUri: "https://app.metorite.com/api/email/oauth/microsoft/callback",
  };

  it("builds the organizations admin-consent URL from the server's values", () => {
    const u = new URL(adminConsentUrl(app));
    expect(`${u.origin}${u.pathname}`).toBe(
      "https://login.microsoftonline.com/organizations/v2.0/adminconsent",
    );
    expect(u.searchParams.get("client_id")).toBe(app.clientId);
    expect(u.searchParams.get("scope")).toBe("https://graph.microsoft.com/.default");
    expect(u.searchParams.get("redirect_uri")).toBe(app.redirectUri);
  });

  it("holds no client id constant: an app info read is required", () => {
    expect(mapMailAppInfo({ client_id: "x", redirect_uri: "https://r" })).toEqual({
      clientId: "x",
      redirectUri: "https://r",
    });
    expect(mapMailAppInfo({ client_id: "", redirect_uri: "https://r" })).toBeNull();
    expect(mapMailAppInfo(null)).toBeNull();
    // No GUID-shaped literal in the module or the page.
    const guid = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
    expect(read("lib/connect.ts")).not.toMatch(guid);
    expect(read("oauth/callback/page.tsx")).not.toMatch(guid);
  });

  it("prefills an email whose body carries the link, decoded with spaces", () => {
    const link = adminConsentUrl(app);
    const m = adminConsentMailto(link);
    expect(m.startsWith("mailto:?subject=")).toBe(true);
    const qs = m.slice("mailto:?".length);
    const subject = decodeURIComponent(qs.split("&body=")[0].replace("subject=", ""));
    const body = decodeURIComponent(qs.split("&body=")[1]);
    expect(subject).toMatch(/approve Metorite/i);
    expect(body).toContain(link);
    expect(m).not.toContain("+");
  });

  it("the email no longer warns of a failed page (EM-T3c)", () => {
    // EM-T3c gives the admin a landing page, so the warning is now false.
    const m = adminConsentMailto(adminConsentUrl(app));
    const body = decodeURIComponent(m.split("&body=")[1]);
    expect(body).not.toContain("did not finish");
  });

  it("the guided page offers 'I am the admin', opening adminConsentUrl(app) (EM-T3c)", () => {
    const page = codeOnly(CALLBACK_PAGE);
    expect(page).toContain("I am the admin");
    // The href is the link the admin steps build from the server's app info,
    // and `link` is `adminConsentUrl(app)`.
    expect(page).toMatch(/setLink\(app \? adminConsentUrl\(app\) : null\)/);
    expect(page).toMatch(/<a\b[^>]*\bhref=\{link\}[^>]*>[\s\S]*?I am the admin/);
    // Same tab: no new window for the admin.
    const anchor = page.match(/<a\b[^>]*\bhref=\{link\}[^>]*>/)?.[0] ?? "";
    expect(anchor).not.toContain("_blank");
  });
});

describe("first sync shows progress (done-when 5)", () => {
  it("only an explicit false is pending", () => {
    expect(isFirstSyncPending({ initialSyncDone: false })).toBe(true);
    expect(isFirstSyncPending({ initialSyncDone: true })).toBe(false);
    expect(isFirstSyncPending({})).toBe(false);
    expect(shouldPollFirstSync([{ initialSyncDone: true }, { initialSyncDone: false }])).toBe(true);
    expect(shouldPollFirstSync([{ initialSyncDone: true }, {}])).toBe(false);
  });

  it("names the accounts whose first sync finished between two reads", () => {
    const before = [
      { id: "a", initialSyncDone: false },
      { id: "b", initialSyncDone: false },
      { id: "c", initialSyncDone: true },
    ];
    const after = [
      { id: "a", initialSyncDone: true },
      { id: "b", initialSyncDone: false },
      { id: "c", initialSyncDone: true },
    ];
    expect(finishedFirstSync(before, after)).toEqual(["a"]);
  });

  it("reads Connected as <address>, and promises no progressive inbox", () => {
    const c = firstSyncCopy("ravi@contoso.test");
    expect(c.title).toBe("Connected as ravi@contoso.test");
    // The scheduler commits a sync in one transaction, so nothing shows first.
    expect(c.body).not.toMatch(/come first|fills/i);
    expect(c.body).toMatch(/a few minutes/);
  });

  it("the page polls while pending, ticks on visibility, and stops on unmount", () => {
    expect(PAGE).toContain("const firstSyncPending = shouldPollFirstSync(accounts);");
    expect(PAGE).toMatch(/if \(!firstSyncPending\) return;/);
    expect(PAGE).toMatch(/firstSyncTick\(\{[\s\S]*?hidden: \(\) => cancelled \|\| document\.hidden/);
    expect(PAGE).toContain("setInterval(tick, FIRST_SYNC_POLL_MS)");
    expect(PAGE).toContain('document.addEventListener("visibilitychange", onVisible)');
    expect(PAGE).toMatch(
      /return \(\) => \{\s*cancelled = true;\s*clearInterval\(id\);\s*document\.removeEventListener\("visibilitychange", onVisible\);/,
    );
    // The banner follows the same rule as the poll, so an errored account shows
    // no spinner. Since EM-T8f-3 the page draws one surface for each pending
    // mailbox, and `firstSyncPanels` picks them by `isFirstSyncPending`.
    expect(PAGE).toContain(
      "const importPanels = firstSyncPanels(accounts, viewAll ? null : selectedAccountId);",
    );
    expect(PAGE).toMatch(/<FirstSyncBanner\s+key=\{account\.id\}\s+address=\{account\.emailAddress\}/);
    expect(firstSyncPanels([{ id: "a", initialSyncDone: false, syncStatus: "error" }], null)).toEqual([]);
  });

  it("an errored first sync is not pending, shows no spinner and stops the poll (fix round 1, P1)", () => {
    const errored = { id: "a", initialSyncDone: false, syncStatus: "error" };
    expect(isFirstSyncPending(errored)).toBe(false);
    expect(shouldPollFirstSync([errored])).toBe(false);
    expect(shouldPollFirstSync([errored, { initialSyncDone: true, syncStatus: "idle" }])).toBe(false);
    // Still syncing (or idle between ticks) is pending.
    expect(isFirstSyncPending({ initialSyncDone: false, syncStatus: "syncing" })).toBe(true);
    expect(isFirstSyncPending({ initialSyncDone: false, syncStatus: "idle" })).toBe(true);
  });

  it("a hidden tab makes no request", async () => {
    const refresh = vi.fn(async () => []);
    const r = await firstSyncTick({
      hidden: () => true,
      before: () => [{ id: "a", initialSyncDone: false }],
      refresh,
      selected: () => "a",
      onFinished: () => {},
    });
    expect(r).toBe("hidden");
    expect(refresh).not.toHaveBeenCalled();
  });

  it("a visible tick re-reads, and loads the inbox when the selected account finishes", async () => {
    const onFinished = vi.fn();
    const pending = await firstSyncTick({
      hidden: () => false,
      before: () => [{ id: "a", initialSyncDone: false }],
      refresh: async () => [{ id: "a", initialSyncDone: false }],
      selected: () => "a",
      onFinished,
    });
    expect(pending).toBe("pending");
    expect(onFinished).not.toHaveBeenCalled();

    const done = await firstSyncTick({
      hidden: () => false,
      before: () => [{ id: "a", initialSyncDone: false }],
      refresh: async () => [{ id: "a", initialSyncDone: true }],
      selected: () => "a",
      onFinished,
    });
    expect(done).toBe("finished");
    expect(onFinished).toHaveBeenCalledWith("a");

    const failed = await firstSyncTick({
      hidden: () => false,
      before: () => [],
      refresh: async () => null,
      selected: () => "a",
      onFinished,
    });
    expect(failed).toBe("failed");
  });

  it("maps initial_sync_done from the accounts API", () => {
    expect(codeOnly(read("lib/api.ts"))).toMatch(/initialSyncDone:\s*\n?\s*typeof raw\.initial_sync_done === "boolean"/);
  });
});

describe("the reconnect banner sends the mailbox as login_hint (done-when 6)", () => {
  it("adds login_hint to the authorize query only when there is one", () => {
    const q = new URLSearchParams(connectQuery("https://app.test/email", "ravi@contoso.test"));
    expect(q.get("redirect_after")).toBe("https://app.test/email");
    expect(q.get("login_hint")).toBe("ravi@contoso.test");
    expect(new URLSearchParams(connectQuery("https://app.test/email")).has("login_hint")).toBe(false);
    expect(new URLSearchParams(connectQuery("https://app.test/email", "  ")).has("login_hint")).toBe(false);
  });

  it("the banner passes the address of the account", () => {
    expect(PAGE).toContain("handleConnect(provider, attentionAccount.emailAddress)");
    expect(callbackBody(PAGE, "handleConnect")).toContain(
      "connectQuery(window.location.href, loginHint, importMonths)",
    );
  });

  it("only an OAuth account can reconnect", () => {
    expect(reconnectProvider({ provider: "microsoft" })).toBe("microsoft");
    expect(reconnectProvider({ provider: "gmail" })).toBe("gmail");
    expect(reconnectProvider({ provider: "imap" })).toBeNull();
  });
});

describe("disconnect from the account menu (done-when 7)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("deleteEmailAccount sends DELETE /email/accounts/{id} through the BFF", async () => {
    const calls: Array<{ url: string; method?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), method: init?.method });
        return new Response(null, { status: 204 });
      }),
    );
    const api = await import("./api");
    await api.deleteEmailAccount("acc-1");
    expect(calls).toEqual([{ url: "/api/email/accounts/acc-1", method: "DELETE" }]);
  });

  it("the store, the menu and the dialog are wired to it", () => {
    const store = codeOnly(read("lib/emailStore.ts"));
    expect(store).toMatch(/deleteAccount: async \(id\) => \{[\s\S]*?await api\.deleteEmailAccount\(id\)/);
    const sidebar = codeOnly(read("components/AccountSidebar.tsx"));
    expect(sidebar).toContain('label: "Disconnect mailbox"');
    expect(sidebar).toContain("onSelect: () => onDisconnect(account)");
    expect(PAGE).toContain("onDisconnect={handleDisconnectRequest}");
    expect(PAGE).toContain("onDisconnect={deleteAccount}");
    expect(codeOnly(read("components/DisconnectDialog.tsx"))).toContain("<ConfirmDialog");
  });

  it("the dialog says that the synced data is deleted and the mailbox stays", () => {
    const c = disconnectCopy("ravi@contoso.test");
    expect(c.body).toContain("ravi@contoso.test");
    expect(c.body).toMatch(/deleted from Metorite/);
    expect(c.note).toMatch(/stays in your Microsoft or Google mailbox/);
  });
});

// WS-17 EM-T4f, fix round 1: the gateway answers 409 while a sync still holds
// the row. The member must read that reason, and the mailbox must stay.
describe("a refused disconnect shows the reason of the gateway (EM-T4f)", () => {
  const BUSY = "A sync is still writing mail for this mailbox. Try again in a moment.";

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function gatewayError(message: string, status: number): Error {
    return Object.assign(new Error(message), { status });
  }

  it("disconnectFailureText keeps a gateway detail and hides the rest", () => {
    expect(disconnectFailureText(gatewayError(BUSY, 409))).toBe(BUSY);
    expect(disconnectFailureText(gatewayError("Account not found", 404))).toBe("Account not found");
    expect(disconnectFailureText(gatewayError("Gateway error 500", 500))).toBe(DISCONNECT_FALLBACK);
    expect(disconnectFailureText(gatewayError("   ", 502))).toBe(DISCONNECT_FALLBACK);
    // No status: the network failed, and its text is not for a member.
    expect(disconnectFailureText(new TypeError("Failed to fetch"))).toBe(DISCONNECT_FALLBACK);
    expect(disconnectFailureText(null)).toBe(DISCONNECT_FALLBACK);
  });

  it("deleteEmailAccount throws the 409 detail, and a 500 throws with no detail", async () => {
    const api = await import("./api");
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: BUSY }), { status: 409 })));
    const busy = await api.deleteEmailAccount("acc-1").then(() => null, (e: unknown) => e);
    expect(disconnectFailureText(busy)).toBe(BUSY);

    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response("Internal Server Error", { status: 500 })));
    const broken = await api.deleteEmailAccount("acc-1").then(() => null, (e: unknown) => e);
    expect(disconnectFailureText(broken)).toBe(DISCONNECT_FALLBACK);
  });

  it("the store keeps the mailbox on a 409 and gives back the detail", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method ?? "GET"} ${String(url)}`);
        if (init?.method === "DELETE") {
          return new Response(JSON.stringify({ detail: BUSY }), { status: 409 });
        }
        return new Response("[]", { status: 200 });
      }),
    );
    const { useEmailStore } = await import("./emailStore");
    const kept = { id: "acc-1", emailAddress: "ravi@contoso.test" };
    useEmailStore.setState({ accounts: [kept] as never, selectedAccountId: null });
    const outcome = await useEmailStore.getState().deleteAccount("acc-1");
    expect(outcome).toEqual({ ok: false, detail: BUSY });
    expect(calls[0]).toBe("DELETE /api/email/accounts/acc-1");
    expect(useEmailStore.getState().error).toBe(BUSY);
  });

  it("the store answers ok on a 204 and drops the mailbox", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    const { useEmailStore } = await import("./emailStore");
    useEmailStore.setState({
      accounts: [{ id: "acc-1" }, { id: "acc-2" }] as never,
      selectedAccountId: null,
    });
    expect(await useEmailStore.getState().deleteAccount("acc-1")).toEqual({ ok: true });
    expect(useEmailStore.getState().accounts.map((a) => a.id)).toEqual(["acc-2"]);
  });

  it("a manual sync refused with 409 shows the detail and is not an error", async () => {
    // EM-T4f fix round 2: the gateway answers 409 at once while a sync runs.
    const SYNC_BUSY = "A sync is already running for this mailbox. New mail appears when it finishes.";
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: SYNC_BUSY }), { status: 409 })));
    const { useEmailStore } = await import("./emailStore");
    useEmailStore.setState({ syncStatus: {}, error: null } as never);
    await useEmailStore.getState().triggerSync("acc-1");
    expect(useEmailStore.getState().error).toBe(SYNC_BUSY);
    expect(useEmailStore.getState().syncStatus["acc-1"]).toBe("idle");
  });

  it("a manual sync that fails another way still marks the mailbox", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "Sync failed: boom" }), { status: 500 })));
    const { useEmailStore } = await import("./emailStore");
    useEmailStore.setState({ syncStatus: {}, error: null } as never);
    await useEmailStore.getState().triggerSync("acc-1");
    expect(useEmailStore.getState().syncStatus["acc-1"]).toBe("error");
  });

  it("the dialog shows the text of the outcome, not one fixed sentence", () => {
    const dialog = codeOnly(read("components/DisconnectDialog.tsx"));
    expect(dialog).toContain("onDisconnect: (id: string) => Promise<DisconnectOutcome>");
    expect(dialog).toContain("else setFailure(outcome.detail);");
    expect(dialog).toMatch(/role="alert"[^>]*>\s*\{failure\}/);
    expect(dialog).not.toContain("could not disconnect the mailbox. Try again.");
  });
});

describe("no flash of the empty state on a hard load (fix round 1, P2)", () => {
  it("draws the empty state only after the first account read settles", () => {
    expect(emailSurface({ loaded: false, loading: false, count: 0 })).toBe("loading");
    expect(emailSurface({ loaded: false, loading: true, count: 0 })).toBe("loading");
    expect(emailSurface({ loaded: true, loading: true, count: 0 })).toBe("loading");
    expect(emailSurface({ loaded: true, loading: false, count: 0 })).toBe("empty");
    expect(emailSurface({ loaded: true, loading: true, count: 2 })).toBe("mailbox");
    expect(emailSurface({ loaded: false, loading: false, count: 1 })).toBe("mailbox");
  });

  it("the store starts not loaded and sets loaded on success and on failure", () => {
    const store = codeOnly(read("lib/emailStore.ts"));
    expect(store).toMatch(/accountsLoading: false,\s*accountsLoaded: false,/);
    expect(store).toContain("set({ accounts, accountsLoading: false, accountsLoaded: true });");
    expect(store).toMatch(/set\(\{ accountsLoading: false, accountsLoaded: true, error:/);
    expect(PAGE).toMatch(/emailSurface\(\{\s*loaded: accountsLoaded,/);
    expect(PAGE).toContain('const noAccounts = surface === "empty";');
  });
});

describe("the mobile bottom bar over the empty state (fix round 1, P2)", () => {
  it("the page announces the empty state, and AppShell hides the email tabs", () => {
    expect(PAGE).toContain('new CustomEvent("cc-email-empty", { detail: noAccounts })');
    expect(PAGE).toContain('new CustomEvent("cc-email-empty", { detail: false })');
    const shell = codeOnly(
      readFileSync(join(EMAIL_APP, "..", "..", "components", "AppShell.tsx"), { encoding: "utf-8" }),
    );
    expect(shell).toContain('window.addEventListener("cc-email-empty", h)');
    expect(shell).toContain("{isEmailPage && !emailEmpty && (");
  });
});

describe("the admin email and the retry (fix round 1)", () => {
  it("no longer warns the admin of a did-not-finish page (EM-T3c removed it)", () => {
    const body = decodeURIComponent(adminConsentMailto("https://x.test").split("&body=")[1]);
    expect(body).not.toMatch(/did not finish/);
    expect(body).not.toMatch(/approval still counts/);
  });

  // Inverted by WS-17 EM-G8: the live set of the capability read decides.
  it("Try again for Gmail opens its range step when the read offers Gmail, else the choices", () => {
    expect(retryTarget("gmail", liveProviders({ microsoft: true, gmail: true }))).toBe(
      "/email?connect=1&provider=gmail",
    );
    expect(retryTarget("gmail", liveProviders({ microsoft: true, gmail: false }))).toBe("/email?connect=1");
    expect(retryTarget("gmail", liveProviders(null))).toBe("/email?connect=1");
    for (const live of [liveProviders(null), liveProviders({ microsoft: true, gmail: true })]) {
      expect(retryTarget("gmail", live)).not.toContain("/api/email/oauth/");
    }
    expect(rangeStepProviderFrom("?connect=1", liveProviders({ microsoft: true, gmail: true }))).toBeNull();
    expect(CALLBACK_PAGE).toContain("retryTarget(provider, live)");
    expect(wantsConnectChoices("?connect=1")).toBe(true);
    expect(wantsConnectChoices("?connect=0")).toBe(false);
    expect(PAGE).toMatch(/wantsConnectChoices\(window\.location\.search\)[\s\S]*?setShowAddModal\(true\)/);
  });

  it("the account-menu trigger is the Button primitive", () => {
    const sidebar = codeOnly(read("components/AccountSidebar.tsx"));
    expect(sidebar).toMatch(/<Button\s+variant="ghost"\s+size="icon-sm"\s+icon="MoreHorizontal"/);
  });
});

// ── WS-17 EM-T6d, part 1: the range of the first import ────────────────────
// Spec: `email_app_master_plan.md` §10.4.7, "EM-T6d", items 2 and 3.

/** Every `role="radio"` button's attributes and label, in order. */
function radios(markup: string): Array<{ checked: string; label: string; pressed?: string }> {
  return [...markup.matchAll(/<button\b([^>]*role="radio"[^>]*)>([\s\S]*?)<\/button>/g)].map((m) => ({
    checked: m[1].match(/aria-checked="([^"]*)"/)?.[1] ?? "",
    pressed: m[1].match(/aria-pressed="([^"]*)"/)?.[1],
    label: m[2].replace(/<[^>]*>/g, "").trim(),
  }));
}

function rangeStep(months: number): string {
  return renderToStaticMarkup(
    createElement(ImportRangeStep, {
      provider: "microsoft",
      months,
      onChange: () => {},
      onBack: () => {},
      onContinue: () => {},
    }),
  );
}

describe("the range step (EM-T6d item 2)", () => {
  it("offers seven choices, 0 to 6 months, and 0 reads Only new mail", () => {
    expect(IMPORT_RANGE_CHOICES.map((c) => c.months)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(MAX_IMPORT_MONTHS).toBe(6);
    expect(IMPORT_RANGE_CHOICES[0].label).toBe("Only new mail");
    expect(IMPORT_RANGE_CHOICES[1].label).toBe("1 month");
    expect(IMPORT_RANGE_CHOICES[6].label).toBe("6 months");
  });

  it("its default is 1 month, and the step draws that choice as checked", () => {
    expect(DEFAULT_IMPORT_MONTHS).toBe(1);
    const r = radios(rangeStep(DEFAULT_IMPORT_MONTHS));
    expect(r.map((x) => x.label)).toEqual(IMPORT_RANGE_CHOICES.map((c) => c.label));
    expect(r.filter((x) => x.checked === "true").map((x) => x.label)).toEqual(["1 month"]);
    // A radio keeps its own role: no aria-pressed beside aria-checked.
    expect(r.every((x) => x.pressed === undefined)).toBe(true);
  });

  it("draws the title, the 6-month limit and the button to Microsoft", () => {
    const out = rangeStep(3);
    expect(out).toContain(IMPORT_RANGE_COPY.title);
    expect(IMPORT_RANGE_COPY.title).toBe("How much of your mail should Metorite import?");
    expect(IMPORT_RANGE_COPY.body).toMatch(/never imports mail older than 6 months/);
    expect(out).toContain("never imports mail older than 6 months");
    expect(out).toContain("Continue to Microsoft");
    expect(out).toContain(">Back<");
    expect(out).toMatch(/role="radiogroup"/);
  });

  it("a click on a provider opens the step with the stored range, and Continue keeps it and starts the sign-in", () => {
    const src = codeOnly(read("components/ConnectChoices.tsx"));
    expect(src).toContain("onClick={() => p.available && setStep({ provider: p.id, months: storedImportMonths() })}");
    expect(src).toMatch(/if \(step\) \{\s*return \(\s*<ImportRangeStep/);
    expect(src).toMatch(
      /onContinue=\{\(\) => \{\s*rememberImportMonths\(step\.months\);\s*onConnect\(step\.provider, step\.months\);\s*\}\}/,
    );
    expect(src).toMatch(/onBack=\{\(\) => \{\s*backFrom\.current = step\.provider;\s*setStep\(null\);\s*\}\}/);
    // The list click no longer starts the sign-in itself.
    expect(src).not.toMatch(/p\.available && onConnect\(/);
  });

  it("both places pass the range to handleConnect, as its third argument", () => {
    expect(PAGE).toContain(
      "onConnect={(provider, importMonths) => handleConnect(provider, undefined, importMonths)}",
    );
    expect(PAGE).toMatch(
      /<ConnectChoices\s+initialProvider=\{rangeStepProvider\}\s*onConnect=\{\(provider, importMonths\) => \{\s*setShowAddModal\(false\);\s*handleConnect\(provider, undefined, importMonths\);/,
    );
  });

  it("names only icons that exist, so none falls back to Zap", () => {
    const src = read("components/ImportRangeStep.tsx");
    const names = [...src.matchAll(/(?:name|icon)="([A-Za-z0-9]+)"/g)].map((m) => m[1]);
    names.push(...CONNECT_PROVIDERS.map((p) => p.icon));
    expect(names.length).toBeGreaterThan(1);
    for (const n of names) expect(isKnownIcon(n), n).toBe(true);
  });
});

describe("the range goes to the gateway (EM-T6d item 3)", () => {
  it("connectQuery with importMonths 3 holds import_months=3", () => {
    const q = new URLSearchParams(connectQuery("https://app.test/email", null, 3));
    expect(q.get("import_months")).toBe("3");
    expect(q.get("redirect_after")).toBe("https://app.test/email");
    expect(q.has("login_hint")).toBe(false);
    expect(new URLSearchParams(connectQuery("https://app.test/email", undefined, 0)).get("import_months")).toBe("0");
  });

  it("sends no import_months for a value outside 0 to 6", () => {
    for (const bad of [7, -1, 1.5, Number.NaN, null, undefined]) {
      const q = new URLSearchParams(connectQuery("https://app.test/email", null, bad as number));
      expect(q.has("import_months"), String(bad)).toBe(false);
      expect(isImportMonths(bad)).toBe(false);
    }
  });

  it("the reconnect target holds no import_months", () => {
    // The banner passes the address and nothing else, so handleConnect gets
    // no range, and connectQuery then sends none (D-EM-13).
    expect(PAGE).toContain("onClick={() => handleConnect(provider, attentionAccount.emailAddress)}");
    const q = new URLSearchParams(connectQuery("https://app.test/email", "ravi@contoso.test"));
    expect(q.get("login_hint")).toBe("ravi@contoso.test");
    expect(q.has("import_months")).toBe(false);
  });
});

// ── EM-T6d fix round 1 ─────────────────────────────────────────────────────

/** A sessionStorage stand-in. `throws` makes every call throw. */
function memoryStore(initial: Record<string, string> = {}, throws = false) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (k: string) => {
      if (throws) throw new Error("SecurityError");
      return data.has(k) ? data.get(k)! : null;
    },
    setItem: (k: string, v: string) => {
      if (throws) throw new Error("QuotaExceededError");
      data.set(k, v);
    },
  };
}

describe("a retry is a first connect, so it goes back to the range step (fix round 1, P1)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("the retry target of a live provider opens its range step, never the OAuth leg", () => {
    // The callback page shows "Try again" and "Approved? Connect again" before
    // any mailbox row exists. A direct authorize call would carry no range,
    // and the gateway default of 1 month would replace the member's choice.
    const retry = retryTarget("microsoft", MS_ONLY);
    expect(retry).toBe("/email?connect=1&provider=microsoft");
    expect(retry).not.toContain("/api/email/oauth/");
    expect(rangeStepProviderFrom(retry.slice(retry.indexOf("?")), MS_ONLY)).toBe("microsoft");
  });

  it("the URL opens a step for a live provider only", () => {
    expect(rangeStepProviderFrom("?connect=1&provider=microsoft", MS_ONLY)).toBe("microsoft");
    expect(rangeStepProviderFrom("?provider=microsoft", MS_ONLY)).toBeNull();
    // Inverted by WS-17 EM-G8 (`:722` before): Gmail opens when the read offers it.
    expect(rangeStepProviderFrom("?connect=1&provider=gmail", MS_ONLY)).toBeNull();
    expect(rangeStepProviderFrom("?connect=1&provider=gmail", BOTH)).toBe("gmail");
    expect(rangeStepProviderFrom("?connect=1&provider=imap", BOTH)).toBeNull();
    expect(rangeStepProviderFrom("?connect=1&provider=%3Cscript%3E", BOTH)).toBeNull();
  });

  it("both buttons of the callback page use the retry target, and no other connect path", () => {
    const connectAgain = CALLBACK_PAGE.match(/function connectAgain\([^)]*\): void \{([\s\S]*?)\n\}/)?.[1] ?? "";
    expect(connectAgain).toContain("window.location.href = retryTarget(provider, live);");
    expect(CALLBACK_PAGE).not.toContain("/api/email/oauth/");
    expect(CALLBACK_PAGE.match(/onClick=\{\(\) => connectAgain\(provider, live\)\}/g)).toHaveLength(2);
    // The live set is the read's, never a constant.
    expect(CALLBACK_PAGE).toContain("const live = liveProviders(providerRead);");
    expect(CALLBACK_PAGE).toMatch(/getConnectProviders\(\)\.then\(\(read\) => \{\s*if \(current\) setProviderRead\(read\);/);
  });

  it("the page reads the query once, and the read decides the step in both places", () => {
    expect(PAGE).toMatch(
      /useState<string>\(\(\) =>\s*typeof window === "undefined" \? "" : window\.location\.search\s*\)/,
    );
    expect(PAGE).toMatch(
      /const rangeStepProvider: ConnectProviderId \| null = rangeStepProviderFrom\(\s*rangeStepQuery,\s*liveProviders\(connectProviders\),\s*\);/,
    );
    expect(PAGE).toMatch(/<ConnectEmptyState[\s\S]*?initialProvider=\{rangeStepProvider\}\s*availability=\{connectProviders\}/);
    expect(PAGE).toMatch(/<ConnectChoices\s+initialProvider=\{rangeStepProvider\}/);
    expect(PAGE).toMatch(/<ConnectChoices[\s\S]*?availability=\{connectProviders\}\s*\/>/);
    expect(PAGE).toMatch(/onClose=\{\(\) => \{\s*setShowAddModal\(false\);\s*setRangeStepQuery\(""\);/);
    expect(codeOnly(read("components/ConnectEmptyState.tsx"))).toContain(
      "<ConnectChoices onConnect={onConnect} initialProvider={initialProvider} availability={availability} />",
    );
  });

  function choicesWith(stored: Record<string, string>, initialProvider: "microsoft" | null = "microsoft") {
    vi.stubGlobal("window", { sessionStorage: memoryStore(stored) });
    return radios(
      renderToStaticMarkup(createElement(ConnectChoices, { onConnect: () => {}, initialProvider, availability: null })),
    );
  }

  it("a stored 6 opens the step with 6 months checked", () => {
    const r = choicesWith({ [IMPORT_MONTHS_STORAGE_KEY]: "6" });
    expect(r).toHaveLength(7);
    expect(r.filter((x) => x.checked === "true").map((x) => x.label)).toEqual(["6 months"]);
  });

  it("a stored x opens the step with 1 month checked", () => {
    const r = choicesWith({ [IMPORT_MONTHS_STORAGE_KEY]: "x" });
    expect(r.filter((x) => x.checked === "true").map((x) => x.label)).toEqual(["1 month"]);
  });

  it("with no initial provider the list draws, not the step", () => {
    expect(choicesWith({ [IMPORT_MONTHS_STORAGE_KEY]: "6" }, null)).toEqual([]);
  });
});

describe("the stored range (fix round 1, P1)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads one digit from 0 to 6, and gives 1 for anything else", () => {
    const at = (v: string) => storedImportMonths(memoryStore({ [IMPORT_MONTHS_STORAGE_KEY]: v }));
    expect(at("0")).toBe(0);
    expect(at("6")).toBe(6);
    for (const bad of ["x", "7", "-1", "03", "3.0", " 3", ""]) expect(at(bad), bad).toBe(DEFAULT_IMPORT_MONTHS);
    expect(storedImportMonths(memoryStore())).toBe(DEFAULT_IMPORT_MONTHS);
  });

  it("gives 1 when storage is absent or throws", () => {
    expect(storedImportMonths(null)).toBe(DEFAULT_IMPORT_MONTHS);
    expect(storedImportMonths(memoryStore({ [IMPORT_MONTHS_STORAGE_KEY]: "4" }, true))).toBe(DEFAULT_IMPORT_MONTHS);
    // Node has no window: the default store is absent.
    expect(storedImportMonths()).toBe(DEFAULT_IMPORT_MONTHS);
    // A window whose sessionStorage getter throws.
    vi.stubGlobal("window", {
      get sessionStorage() {
        throw new Error("SecurityError");
      },
    });
    expect(storedImportMonths()).toBe(DEFAULT_IMPORT_MONTHS);
    expect(() => rememberImportMonths(3)).not.toThrow();
  });

  it("keeps a valid range, and never throws", () => {
    const store = memoryStore();
    rememberImportMonths(3, store);
    expect(store.data.get(IMPORT_MONTHS_STORAGE_KEY)).toBe("3");
    rememberImportMonths(9, store);
    rememberImportMonths(1.5, store);
    expect(store.data.get(IMPORT_MONTHS_STORAGE_KEY)).toBe("3");
    expect(() => rememberImportMonths(2, memoryStore({}, true))).not.toThrow();
    expect(() => rememberImportMonths(2, null)).not.toThrow();
  });

  it("wraps each storage read and write in try/catch", () => {
    // LF only, so the body slice below ends at the closing brace in any checkout.
    const src = codeOnly(read("lib/connect.ts")).replace(/\r\n/g, "\n");
    for (const fn of ["sessionStore", "storedImportMonths", "rememberImportMonths"]) {
      const at = src.indexOf(`function ${fn}(`);
      expect(at, fn).toBeGreaterThan(-1);
      const body = src.slice(at, src.indexOf("\n}\n", at));
      expect(body, fn).toMatch(/try \{[\s\S]*\} catch \{/);
    }
  });
});

describe("focus moves with the step (fix round 1, P3)", () => {
  it("the step puts focus on the checked choice when it opens", () => {
    const src = codeOnly(read("components/ImportRangeStep.tsx"));
    expect(src).toMatch(
      /useEffect\(\(\) => \{\s*groupRef\.current\?\.querySelector<HTMLButtonElement>\('\[role="radio"\]\[aria-checked="true"\]'\)\?\.focus\(\);\s*\}, \[\]\);/,
    );
    expect(src).toMatch(/<div ref=\{groupRef\} role="radiogroup"/);
  });

  it("after Back, focus goes back to the provider that opened the step", () => {
    const src = codeOnly(read("components/ConnectChoices.tsx"));
    expect(src).toMatch(/onBack=\{\(\) => \{\s*backFrom\.current = step\.provider;/);
    expect(src).toMatch(
      /useEffect\(\(\) => \{\s*if \(step !== null \|\| backFrom\.current === null\) return;\s*listRef\.current\s*\?\.querySelector<HTMLButtonElement>\(`\[data-provider="\$\{backFrom\.current\}"\]`\)\s*\?\.focus\(\);\s*backFrom\.current = null;\s*\}, \[step\]\);/,
    );
    expect(src).toContain("<ul ref={listRef}");
    expect(src).toContain("data-provider={p.id}");
  });
});

describe("the handlers of the range step (fix round 1, P3)", () => {
  /** The source of each `<Button …>…</Button>` element, comments stripped. */
  function buttonBlocks(): string[] {
    const src = codeOnly(read("components/ImportRangeStep.tsx"));
    return src
      .split("<Button")
      .slice(1)
      .map((b) => b.slice(0, b.indexOf("</Button>")));
  }

  it("draws three Buttons: the choice in the map, Back and Continue", () => {
    expect(buttonBlocks()).toHaveLength(3);
  });

  it("a choice calls onChange with its own months", () => {
    const radio = buttonBlocks().filter((b) => b.includes('role="radio"'));
    expect(radio).toHaveLength(1);
    expect(radio[0]).toContain("onClick={() => onChange(c.months)}");
    expect(radio[0]).toContain("aria-checked={c.months === months}");
    expect(radio[0]).toContain("selected={c.months === months}");
  });

  it("Continue calls onContinue, and Back calls onBack", () => {
    const blocks = buttonBlocks();
    const cont = blocks.filter((b) => b.includes("IMPORT_RANGE_COPY.continueTo(provider)"));
    const back = blocks.filter((b) => b.includes("IMPORT_RANGE_COPY.back"));
    expect(cont).toHaveLength(1);
    expect(back).toHaveLength(1);
    expect(cont[0]).toMatch(/\bonClick=\{onContinue\}/);
    expect(cont[0]).not.toMatch(/onBack|onChange/);
    expect(back[0]).toMatch(/\bonClick=\{onBack\}/);
    expect(back[0]).not.toMatch(/onContinue|onChange/);
  });
});

// ── A Microsoft decline also offers admin approval (2026-10-02) ────────────
// A test customer met Microsoft's "Need admin approval" screen and chose
// "Return to the application without granting consent". Microsoft sends a
// bare `access_denied` with no AADSTS code, and the gateway reads it as
// `consent_declined`. The callback page cannot render in this node-env
// vitest (it reads `useSearchParams`), so the decision runs here and the
// page's wiring is held by source scans with comments stripped.

describe("a Microsoft decline also shows the admin-approval help", () => {
  it("consent_declined for Microsoft gets the help, after the declined words", () => {
    expect(adminApprovalHelp("consent_declined", "microsoft")).toEqual({ lead: ADMIN_APPROVAL_AFTER_DECLINE });
    expect(ADMIN_APPROVAL_AFTER_DECLINE).toContain('"Need admin approval"');
    expect(ADMIN_APPROVAL_AFTER_DECLINE).toMatch(/IT admin approves Metorite once for your company/);
  });

  it("admin_consent_required is unchanged: the help, with no extra line", () => {
    expect(adminApprovalHelp("admin_consent_required", "microsoft")).toEqual({ lead: null });
    expect(adminApprovalHelp("admin_consent_required", "gmail")).toEqual({ lead: null });
  });

  it("a Gmail decline shows no Microsoft admin text", () => {
    expect(adminApprovalHelp("consent_declined", "gmail")).toBeNull();
  });

  it("no other result shows the help", () => {
    for (const kind of ["loading", "connected", "duplicate", "retry", "unknown"] as const) {
      expect(adminApprovalHelp(kind, "microsoft"), kind).toBeNull();
    }
  });

  it("the page draws the declined words first, then the lead, then the one AdminConsentSteps", () => {
    expect(CALLBACK_PAGE).toContain("const approvalHelp = adminApprovalHelp(view.kind, provider);");
    expect(CALLBACK_PAGE).toMatch(
      /\{approvalHelp && \(\s*<div[^>]*>\s*\{approvalHelp\.lead && \(\s*<p[^>]*>\{approvalHelp\.lead\}<\/p>\s*\)\}\s*<AdminConsentSteps \/>/,
    );
    // Reused, not copied: one component, drawn in one place.
    expect(CALLBACK_PAGE.match(/function AdminConsentSteps\(/g)).toHaveLength(1);
    expect(CALLBACK_PAGE.match(/<AdminConsentSteps \/>/g)).toHaveLength(1);
    expect(CALLBACK_PAGE.indexOf("{view.body}")).toBeLessThan(CALLBACK_PAGE.indexOf("{approvalHelp && ("));
  });

  it("the help holds the approval link, the mail to the admin and the copy action", () => {
    const steps = CALLBACK_PAGE.slice(
      CALLBACK_PAGE.indexOf("function AdminConsentSteps("),
      CALLBACK_PAGE.indexOf("function CallbackContent("),
    );
    expect(steps).toContain("setLink(app ? adminConsentUrl(app) : null)");
    expect(steps).toContain("adminConsentMailto(link)");
    expect(steps).toContain("Email your IT admin");
    expect(steps).toContain("navigator.clipboard.writeText(link)");
    expect(steps).toContain('"Copy approval link"');
    expect(steps).toMatch(/<a\b[^>]*\bhref=\{link\}/);
  });

  it("a decline keeps Try again, which reopens the range step", () => {
    expect(offersTryAgain("consent_declined")).toBe(true);
    expect(CALLBACK_PAGE).toMatch(
      /\{offersTryAgain\(view\.kind\) && \(\s*<Button[^>]*onClick=\{\(\) => connectAgain\(provider, live\)\}>\s*Try again/,
    );
  });
});

// ── WS-17 EM-G8: the connect UI (§12.3.10) ─────────────────────────────────
// The capability read `GET /email/oauth/providers` (EM-G7 item 8) answers
// `{"microsoft": bool, "gmail": bool}`. Gmail is true only with the Google
// app on the box AND `EMAIL_GMAIL_CONNECT` on (D-EM-36), so production reads
// `gmail: false` and Gmail stays "Coming soon". Each describe name below is a
// fence name of §12.3.10, and mutations M1 to M4 of the spec turn one red.

/** The SSR markup of the connect choices for one answer of the read. */
function choicesMarkup(
  availability: ProviderAvailability | null | undefined,
  initialProvider: "microsoft" | "gmail" | null = null,
): string {
  return renderToStaticMarkup(createElement(ConnectChoices, { onConnect: () => {}, initialProvider, availability }));
}

/**
 * Whether the `<button>` that carries `data-provider="<id>"` is disabled.
 * The class string holds `disabled:` variants, so only the attribute counts.
 */
function providerDisabled(markup: string, id: string): boolean {
  const button = markup.match(new RegExp(`<button\\b[^>]*data-provider="${id}"[^>]*>`))?.[0];
  expect(button, `no button for ${id}`).toBeDefined();
  return /\sdisabled=""/.test(button ?? "");
}

type FetchReply = Response | Error | Promise<never>;

/** Stubs `fetch` with one reply for each path, and records each path asked. */
function stubFetch(replies: Record<string, () => FetchReply>): string[] {
  const asked: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = String(url);
      asked.push(path);
      const reply = replies[path]?.() ?? new Response("{}", { status: 404 });
      if (reply instanceof Error) throw reply;
      return reply;
    }),
  );
  return asked;
}

const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status });

describe("gmail-available-from-capability", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("an answer with gmail true makes Gmail a live choice", () => {
    expect(mapProviderAvailability({ microsoft: true, gmail: true })).toEqual({ microsoft: true, gmail: true });
    const gmail = connectChoices({ microsoft: true, gmail: true }).find((p) => p.id === "gmail");
    expect(gmail?.available).toBe(true);
    expect(gmail?.note).toBeUndefined();
    const markup = choicesMarkup({ microsoft: true, gmail: true });
    expect(providerDisabled(markup, "gmail")).toBe(false);
    expect(providerDisabled(markup, "microsoft")).toBe(false);
  });

  it("the range step and the retry take Gmail from the live set (M2)", () => {
    expect(rangeStepProviderFrom("?connect=1&provider=gmail", BOTH)).toBe("gmail");
    expect(retryTarget("gmail", BOTH)).toBe("/email?connect=1&provider=gmail");
    const step = choicesMarkup({ microsoft: true, gmail: true }, "gmail");
    expect(step).toContain("Continue to Google");
    expect(step).toMatch(/role="radiogroup"/);
  });

  it("the store reads GET /email/oauth/providers through the BFF and keeps the answer", async () => {
    const asked = stubFetch({ "/api/email/oauth/providers": json({ microsoft: true, gmail: true }) });
    const { useEmailStore } = await import("./emailStore");
    useEmailStore.setState({ connectProviders: undefined });
    await useEmailStore.getState().fetchConnectProviders();
    expect(asked).toEqual(["/api/email/oauth/providers"]);
    expect(useEmailStore.getState().connectProviders).toEqual({ microsoft: true, gmail: true });
  });

  it("the page reads it on mount and hands it to both places", () => {
    expect(PAGE).toMatch(/useEffect\(\(\) => \{\s*void fetchConnectProviders\(\);\s*\}, \[fetchConnectProviders\]\);/);
    expect(PAGE.match(/availability=\{connectProviders\}/g)).toHaveLength(2);
  });
});

describe("gmail-coming-soon-when-the-read-fails", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("a failed or unsettled read keeps Microsoft live and Gmail Coming soon (M1)", () => {
    expect(FAILED_READ_AVAILABILITY).toEqual({ microsoft: true, gmail: false });
    for (const read of [null, undefined]) {
      expect([...liveProviders(read)]).toEqual(["microsoft"]);
      const [ms, gmail] = connectChoices(read);
      expect(ms.available).toBe(true);
      expect(gmail.available).toBe(false);
      expect(gmail.note).toBe("Coming soon");
    }
  });

  it("the flag off answers gmail false, and Gmail stays Coming soon", () => {
    const gmail = connectChoices({ microsoft: true, gmail: false }).find((p) => p.id === "gmail");
    expect(gmail).toMatchObject({ available: false, note: "Coming soon" });
    const markup = choicesMarkup({ microsoft: true, gmail: false });
    expect(providerDisabled(markup, "gmail")).toBe(true);
    expect(providerDisabled(markup, "microsoft")).toBe(false);
    expect(markup).toContain("Coming soon");
    expect(markup).not.toContain(WORKSPACE_ADMIN_HELP.line);
  });

  it("an answer of another shape is a failed read", () => {
    for (const raw of [null, undefined, [], "gmail", 1, {}, { microsoft: true }, { gmail: true },
      { microsoft: "true", gmail: true }, { microsoft: true, gmail: 1 }]) {
      expect(mapProviderAvailability(raw), JSON.stringify(raw)).toBeNull();
    }
  });

  it("a network error, a 404, a 403, a 500 or a bad body reads as null", async () => {
    const api = await import("./api");
    const replies: Array<[string, () => FetchReply]> = [
      ["network", () => new TypeError("Failed to fetch")],
      ["404", json({ detail: "Not Found" }, 404)],
      ["403", json({ detail: "Forbidden" }, 403)],
      ["500", () => new Response("Internal Server Error", { status: 500 })],
      ["list", json([true, true])],
      ["strings", json({ microsoft: "yes", gmail: "yes" })],
      ["html", () => new Response("<html></html>", { status: 200 })],
    ];
    for (const [label, reply] of replies) {
      stubFetch({ "/api/email/oauth/providers": reply });
      expect(await api.getConnectProviders(), label).toBeNull();
    }
  });

  it("the store keeps null on a 404, so the choices keep Gmail Coming soon", async () => {
    stubFetch({ "/api/email/oauth/providers": json({ detail: "Not Found" }, 404) });
    const { useEmailStore } = await import("./emailStore");
    useEmailStore.setState({ connectProviders: undefined });
    await useEmailStore.getState().fetchConnectProviders();
    const read = useEmailStore.getState().connectProviders;
    expect(read).toBeNull();
    expect(connectChoices(read).find((p) => p.id === "gmail")?.note).toBe("Coming soon");
  });

  it("a read that never answers counts as failed after PROVIDERS_READ_TIMEOUT_MS", async () => {
    vi.useFakeTimers();
    stubFetch({ "/api/email/oauth/providers": () => new Promise<never>(() => {}) });
    const { useEmailStore, PROVIDERS_READ_TIMEOUT_MS } = await import("./emailStore");
    useEmailStore.setState({ connectProviders: undefined });
    const done = useEmailStore.getState().fetchConnectProviders();
    await vi.advanceTimersByTimeAsync(PROVIDERS_READ_TIMEOUT_MS);
    await done;
    expect(useEmailStore.getState().connectProviders).toBeNull();
  });

  it("the choices draw a skeleton until the read settles, and no provider", () => {
    const markup = choicesMarkup(undefined);
    expect(markup).toContain('role="status"');
    expect(markup).not.toContain("data-provider");
    expect(markup).not.toContain("Coming soon");
  });

  it("a URL that names Gmail opens no step while the read refuses it", () => {
    expect(rangeStepProviderFrom("?connect=1&provider=gmail", MS_ONLY)).toBeNull();
    expect(rangeStepProviderFrom("?connect=1&provider=gmail", liveProviders(null))).toBeNull();
    // The list itself refuses a forced step for a provider that is not live.
    expect(choicesMarkup(null, "gmail")).not.toMatch(/role="radiogroup"/);
  });
});

describe("microsoft-unavailable-when-the-read-says-no", () => {
  it("microsoft false shows Microsoft as not available, with the not-configured words (E-D4)", () => {
    expect(UNAVAILABLE_NOTE.microsoft).toBe("Not available yet");
    const [ms, gmail] = connectChoices({ microsoft: false, gmail: true });
    expect(ms).toMatchObject({ id: "microsoft", available: false, note: "Not available yet" });
    expect(gmail.available).toBe(true);
    const markup = choicesMarkup({ microsoft: false, gmail: false });
    expect(providerDisabled(markup, "microsoft")).toBe(true);
    expect(providerDisabled(markup, "gmail")).toBe(true);
    expect(markup).toContain("Not available yet");
  });

  it("no path offers a provider whose app is missing", () => {
    const none = liveProviders({ microsoft: false, gmail: false });
    expect(none.size).toBe(0);
    expect(rangeStepProviderFrom("?connect=1&provider=microsoft", none)).toBeNull();
    expect(retryTarget("microsoft", none)).toBe("/email?connect=1");
    expect(choicesMarkup({ microsoft: false, gmail: true }, "microsoft")).not.toMatch(/role="radiogroup"/);
  });
});

describe("empty-state-names-each-live-provider", () => {
  function emptyState(availability: ProviderAvailability | null | undefined): string {
    return renderToStaticMarkup(createElement(ConnectEmptyState, { onConnect: () => {}, availability }));
  }

  it("names each provider that the read offers (E-D3)", () => {
    expect(signInLine(MS_ONLY)).toBe("You sign in with Microsoft. Metorite never sees your password.");
    expect(signInLine(BOTH)).toBe("You sign in with Microsoft or Google. Metorite never sees your password.");
    expect(signInLine(liveProviders({ microsoft: false, gmail: true }))).toBe(
      "You sign in with Google. Metorite never sees your password.",
    );
    expect(signInLine(new Set())).toBe("Metorite never sees your password.");
  });

  it("the empty state draws the line from the read, and reads as today with Microsoft only", () => {
    expect(emptyState(null)).toContain("You sign in with Microsoft. Metorite never sees your password.");
    expect(emptyState({ microsoft: true, gmail: false })).toContain("You sign in with Microsoft. Metorite");
    expect(emptyState({ microsoft: true, gmail: true })).toContain("You sign in with Microsoft or Google.");
    // While the read runs, the line names no provider.
    const pending = emptyState(undefined);
    expect(pending).toContain("Metorite never sees your password.");
    expect(pending).not.toContain("You sign in with");
  });
});

describe("callback-copy-names-google", () => {
  const gmail = { accountId: null, email: null, provider: "gmail" as const };
  const ms = { accountId: null, email: null, provider: "microsoft" as const };

  it("the provider comes from the URL, and only gmail names Google", () => {
    expect(callbackProvider("gmail")).toBe("gmail");
    expect(callbackProvider("microsoft")).toBe("microsoft");
    for (const other of [null, "", "GMAIL", "imap", "<b>x</b>"]) expect(callbackProvider(other)).toBe("microsoft");
    expect(PROVIDER_NAME).toEqual({ microsoft: "Microsoft", gmail: "Google" });
  });

  it("the decline text and the generic failure name Google for a Gmail try", () => {
    const declined = callbackView({ ...gmail, error: "consent_declined" });
    expect(declined.body).toContain(
      "Google did not give Metorite access to your mailbox, so nothing was connected.",
    );
    const unknown = callbackView({ ...gmail, error: "provider_error" });
    expect(unknown.body).toContain("Google or Metorite stopped the connection.");
    for (const v of [declined, unknown]) expect(`${v.title} ${v.body}`).not.toContain("Microsoft");
    // A Microsoft try reads as before.
    expect(callbackView({ ...ms, error: "consent_declined" }).body).toContain("Microsoft did not give Metorite");
    expect(callbackView({ ...ms, error: "provider_error" }).body).toContain("Microsoft or Metorite stopped");
  });

  it("a mailbox that was connected before names the sign-in page of the try", () => {
    expect(alreadyConnectedCopy("ravi@gmail.test", "gmail")).toBe(
      "ravi@gmail.test was already connected. Metorite signed it in again. " +
        "To add a different mailbox, choose another account at Google.",
    );
    expect(alreadyConnectedCopy(null, "microsoft")).toMatch(/^This mailbox was already connected\..*at Microsoft\.$/);
  });

  it("the callback page reads provider from its URL and passes it to each copy (M3)", () => {
    expect(CALLBACK_PAGE).toContain('const provider = callbackProvider(searchParams.get("provider"));');
    expect(CALLBACK_PAGE).toContain("callbackView({ error, accountId, email, provider })");
    expect(CALLBACK_PAGE).toContain("{alreadyConnectedCopy(email, provider)}");
    expect(CALLBACK_PAGE).toContain("adminApprovalHelp(view.kind, provider)");
    expect(CALLBACK_PAGE).not.toMatch(/at Microsoft\./);
  });
});

describe("scope-missing-copy", () => {
  it("scope_missing says that Metorite needs both permissions, and offers a retry", () => {
    const v = callbackView({ error: "scope_missing", accountId: null, email: null, provider: "gmail" });
    expect(v.kind).toBe("scope_missing");
    expect(v.title).toBe("Metorite needs both permissions");
    expect(v.body).toMatch(/^Google showed two permissions/);
    expect(v.body).toContain("Metorite needs both");
    expect(v.body).toContain("Try again");
    expect(`${v.title} ${v.body}`).not.toMatch(/unexpected error/i);
    expect(offersTryAgain("scope_missing")).toBe(true);
    expect(offersConnectAgain("scope_missing")).toBe(false);
    expect(CALLBACK_PAGE).toMatch(/scope_missing: \{ icon: "ShieldAlert", className: "bg-warning\/10 text-warning" \}/);
  });
});

describe("workspace-admin-help-shows-the-client-id", () => {
  const CLIENT_ID = "123456789012-abcdefg.apps.googleusercontent.com";

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("the help draws the client ID and the Admin console path of §12.4", () => {
    const markup = renderToStaticMarkup(createElement(WorkspaceAdminSteps, { clientId: CLIENT_ID }));
    expect(markup).toContain(CLIENT_ID);
    for (const part of [
      "Security → Access and data control → API controls",
      "Manage third-party app access",
      "Configure new app",
      "Trusted",
      WORKSPACE_ADMIN_HELP.copy,
    ]) {
      expect(markup, part).toContain(part);
    }
    const none = renderToStaticMarkup(createElement(WorkspaceAdminSteps, { clientId: null }));
    expect(none).toContain(WORKSPACE_ADMIN_HELP.unavailable);
    expect(none).not.toContain(WORKSPACE_ADMIN_HELP.copy);
    expect(renderToStaticMarkup(createElement(WorkspaceAdminSteps, { clientId: undefined }))).toContain(
      WORKSPACE_ADMIN_HELP.loading,
    );
  });

  it("the client ID comes from GET /email/oauth/gmail/app, never a constant", async () => {
    const asked = stubFetch({
      "/api/email/oauth/gmail/app": json({
        client_id: CLIENT_ID,
        redirect_uri: "https://app.metorite.com/api/email/oauth/gmail/callback",
      }),
      "/api/email/oauth/microsoft/app": json({ client_id: "ms", redirect_uri: "https://r" }),
    });
    const api = await import("./api");
    expect((await api.getMailAppInfo("gmail"))?.clientId).toBe(CLIENT_ID);
    expect((await api.getMailAppInfo())?.clientId).toBe("ms");
    expect(asked).toEqual(["/api/email/oauth/gmail/app", "/api/email/oauth/microsoft/app"]);
    // While the flag is off, Gmail answers 503, and the help says so.
    stubFetch({ "/api/email/oauth/gmail/app": json({ detail: "Gmail is not set up" }, 503) });
    expect(await api.getMailAppInfo("gmail")).toBeNull();
    const help = codeOnly(read("components/WorkspaceAdminHelp.tsx"));
    expect(help).toContain('getMailAppInfo("gmail")');
    expect(help).not.toMatch(/apps\.googleusercontent\.com/);
  });

  it("one line under a live Gmail choice opens the help, and nothing else does", () => {
    expect(WORKSPACE_ADMIN_HELP.line).toBe("Company Google account? Your admin can trust Metorite once for everyone.");
    expect(choicesMarkup({ microsoft: true, gmail: true })).toContain(WORKSPACE_ADMIN_HELP.line);
    expect(choicesMarkup({ microsoft: true, gmail: false })).not.toContain(WORKSPACE_ADMIN_HELP.line);
    const choices = codeOnly(read("components/ConnectChoices.tsx"));
    expect(choices).toContain('{p.id === "gmail" && p.available && <WorkspaceAdminLine />}');
    expect(choices).toMatch(/aria-expanded=\{open\}/);
  });

  it("workspace_admin_blocked shows the help and offers Connect again", () => {
    const v = callbackView({ error: "workspace_admin_blocked", accountId: null, email: null, provider: "gmail" });
    expect(v.kind).toBe("workspace_admin_blocked");
    expect(v.title).toBe("Your Google Workspace admin needs to trust Metorite");
    expect(showsWorkspaceAdminHelp(v.kind)).toBe(true);
    expect(offersConnectAgain(v.kind)).toBe(true);
    expect(offersTryAgain(v.kind)).toBe(false);
    for (const kind of ["admin_consent_required", "consent_declined", "scope_missing", "unknown"] as const) {
      expect(showsWorkspaceAdminHelp(kind), kind).toBe(false);
    }
    expect(CALLBACK_PAGE).toMatch(/\{showsWorkspaceAdminHelp\(view\.kind\) && \(\s*<div[^>]*>\s*<WorkspaceAdminHelp \/>/);
    expect(CALLBACK_PAGE).toMatch(
      /\{offersConnectAgain\(view\.kind\) && \(\s*<Button[\s\S]*?onClick=\{\(\) => connectAgain\(provider, live\)\}>\s*Approved\? Connect again/,
    );
  });

  it("names only icons that exist", () => {
    const names = ["components/WorkspaceAdminHelp.tsx", "components/ConnectChoices.tsx", "oauth/callback/page.tsx"]
      .flatMap((f) => [...read(f).matchAll(/(?:name|icon)[=:] ?"([A-Za-z0-9]+)"/g)].map((m) => m[1]));
    // The copy button swaps two names in one expression.
    expect(read("components/WorkspaceAdminHelp.tsx")).toContain('icon={copied ? "Check" : "Copy"}');
    names.push("Check", "Copy");
    expect(names).toEqual(expect.arrayContaining(["ShieldAlert", "ShieldCheck"]));
    for (const n of names) expect(isKnownIcon(n), n).toBe(true);
  });
});

describe("gmail-folder-tree-shows-well-known-folders", () => {
  const WELL_KNOWN = ["all", "inbox", "starred", "snoozed", "sent", "drafts", "archive", "junk", "trash"];
  const label = (name: string, type: string, message_count = 0) => ({
    provider_folder_id: name, name, type, message_count, unread_count: 0,
  });
  const GMAIL_LABELS = [
    label("INBOX", "system", 5), label("SENT", "system", 3), label("DRAFT", "system", 1),
    label("TRASH", "system", 2), label("SPAM", "system", 4), label("STARRED", "system"),
    label("IMPORTANT", "system"), label("CATEGORY_SOCIAL", "system"),
    label("Projects", "user", 7), label("Receipts", "user", 2), label("Archive", "user", 99),
  ];

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("a Gmail tree holds the well-known folders and Archive, and no user label (M4)", async () => {
    const { mergeFolders } = await import("./emailStore");
    const tree = mergeFolders(GMAIL_LABELS, {}, "gmail");
    expect(tree.map((f) => f.key)).toEqual(WELL_KNOWN);
    expect(tree.some((f) => f.type === "user")).toBe(false);
    const count = (key: string) => tree.find((f) => f.key === key)?.count;
    expect([count("inbox"), count("sent"), count("drafts"), count("junk"), count("trash")]).toEqual([5, 3, 1, 4, 2]);
    // A user label named "Archive" never takes the place of the folder.
    expect(count("archive")).toBe(0);
  });

  it("an Outlook tree keeps its user folders", async () => {
    const { mergeFolders } = await import("./emailStore");
    const outlook = [label("Inbox", "system", 5), label("Projects", "user", 7)];
    expect(mergeFolders(outlook, {}, "microsoft").map((f) => f.key)).toEqual([...WELL_KNOWN, "projects"]);
  });

  it("the store passes the provider of the mailbox, and its user labels reach the label filter", async () => {
    stubFetch({
      "/api/email/accounts/g1/folders": json(GMAIL_LABELS),
      "/api/email/accounts/g1/labels": json([{ name: "Projects", color: null }, { name: "Receipts", color: null }]),
    });
    const { useEmailStore } = await import("./emailStore");
    useEmailStore.setState({ accounts: [{ id: "g1", provider: "gmail" }] as never, selectedAccountId: "g1", emails: [] });
    await useEmailStore.getState().fetchFolders("g1");
    expect(useEmailStore.getState().folders.map((f) => f.key)).toEqual(WELL_KNOWN);
    await useEmailStore.getState().fetchLabels("g1");
    expect(useEmailStore.getState().availableLabels).toEqual(expect.arrayContaining(["Projects", "Receipts"]));
    const store = codeOnly(read("lib/emailStore.ts"));
    expect(store).toContain("mergeFolders(rawFolders, emailCounts, provider)");
    expect(store).toContain("mergeFolders(raw, {}, a.provider)");
  });
});

describe("reconnect-banner-names-the-provider", () => {
  it("the reconnect button names Outlook or Gmail (E-D3)", () => {
    expect(RECONNECT_LABEL).toEqual({ microsoft: "Reconnect Outlook", gmail: "Reconnect Gmail" });
    expect(PAGE).toMatch(
      /const provider = reconnectProvider\(attentionAccount\);[\s\S]*?onClick=\{\(\) => handleConnect\(provider, attentionAccount\.emailAddress\)\}\s*>\s*\{RECONNECT_LABEL\[provider\]\}/,
    );
  });

  it("the words of the banner name no provider, so they hold for both", () => {
    expect(PAGE).toContain("reach the provider — message bodies, folders and statuses may be stale.");
    expect(PAGE).toContain('"The connection may have expired. Reconnect to restore full access."');
  });
});
