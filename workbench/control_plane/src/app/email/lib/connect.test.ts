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
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CONNECT_PROVIDERS,
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

const EMAIL_APP = join(__dirname, "..");

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

  it("offers Microsoft as the one live choice and Gmail disabled as Coming soon", () => {
    expect(CONNECT_PROVIDERS.map((p) => p.id)).toEqual(["microsoft", "gmail"]);
    const [ms, gmail] = CONNECT_PROVIDERS;
    expect(ms.available).toBe(true);
    expect(ms.label).toMatch(/Microsoft 365/);
    expect(gmail.available).toBe(false);
    expect(gmail.note).toBe("Coming soon");
    expect(CONNECT_PROVIDERS.some((p) => (p.id as string) === "imap")).toBe(false);
  });

  it("the empty state and the add-account dialog draw the same list", () => {
    expect(codeOnly(read("components/ConnectEmptyState.tsx"))).toContain("<ConnectChoices");
    expect(PAGE).toMatch(/<Modal[\s\S]*?<ConnectChoices[\s\S]*?<\/Modal>/);
    const choices = codeOnly(read("components/ConnectChoices.tsx"));
    expect(choices).toContain("disabled={!p.available}");
    expect(choices).toContain("{p.note}");
  });

  it("the empty state replaces the panes when there is no mailbox", () => {
    expect(PAGE).toMatch(/if \(noAccounts\) \{\s*return \(\s*<ConnectEmptyState/);
    expect(read("components/ConnectEmptyState.tsx")).toContain("Connect your email");
  });
});

describe("the callback page gives guided copy (done-when 4)", () => {
  const base = { accountId: null, email: null };

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
    const v = callbackView({ error: null, accountId: "a1", email: "ravi@contoso.test" });
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
    // The banner follows the same rule as the poll, so an errored account shows no spinner.
    expect(PAGE).toMatch(
      /pendingAccount =[\s\S]*?isFirstSyncPending\(selectedAccount\)[\s\S]*?accounts\.find\(isFirstSyncPending\)/,
    );
    expect(PAGE).toContain("<FirstSyncBanner address={pendingAccount.emailAddress} />");
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
    expect(PAGE).toContain("handleConnect(provider, selectedAccount.emailAddress)");
    expect(callbackBody(PAGE, "handleConnect")).toContain("connectQuery(window.location.href, loginHint)");
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

  it("Try again for Gmail goes to the connect choices, never its OAuth leg", () => {
    expect(retryTarget("gmail", "https://app.test")).toBe("/email?connect=1");
    const ms = retryTarget("microsoft", "https://app.test");
    expect(ms.startsWith("/api/email/oauth/microsoft/authorize?")).toBe(true);
    expect(new URLSearchParams(ms.split("?")[1]).get("redirect_after")).toBe("https://app.test/email");
    expect(CALLBACK_PAGE).toContain("retryTarget(provider, window.location.origin)");
    expect(wantsConnectChoices("?connect=1")).toBe(true);
    expect(wantsConnectChoices("?connect=0")).toBe(false);
    expect(PAGE).toMatch(/wantsConnectChoices\(window\.location\.search\)[\s\S]*?setShowAddModal\(true\)/);
  });

  it("the account-menu trigger is the Button primitive", () => {
    const sidebar = codeOnly(read("components/AccountSidebar.tsx"));
    expect(sidebar).toMatch(/<Button\s+variant="ghost"\s+size="icon-sm"\s+icon="MoreHorizontal"/);
  });
});
