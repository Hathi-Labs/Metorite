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
  disconnectCopy,
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

  it("reads Connected as <address>", () => {
    expect(firstSyncCopy("ravi@contoso.test").title).toBe("Connected as ravi@contoso.test");
  });

  it("the page polls while pending and stops on unmount", () => {
    expect(PAGE).toContain("shouldPollFirstSync(accounts)");
    expect(PAGE).toMatch(/setInterval\([\s\S]*?refreshAccounts\(\)[\s\S]*?FIRST_SYNC_POLL_MS\)/);
    expect(PAGE).toMatch(/return \(\) => \{\s*cancelled = true;\s*clearInterval\(id\);/);
    expect(PAGE).toContain("<FirstSyncBanner address={pendingAccount.emailAddress} />");
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
