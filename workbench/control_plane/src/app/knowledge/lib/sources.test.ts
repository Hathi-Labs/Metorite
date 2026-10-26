/**
 * The Sources view (WS-61 KC-1b). Each case is written so the obvious wrong
 * build fails it: a host check that a lookalike host passes, a failure code
 * drawn raw, a poll that never ends, a view that reads file text.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgeError } from "./api";
import {
  connectFailure,
  connectTarget,
  disconnectCopy,
  driveLink,
  listSources,
  ownerLine,
  returnMessage,
  shouldPoll,
  sourceCount,
  stateLabel,
  truncatedNotice,
  typeWord,
  visibleSources,
  type Source,
} from "./sources";

const KNOWLEDGE = path.join(__dirname, "..");
const read = (...parts: string[]) => readFileSync(path.join(KNOWLEDGE, ...parts), "utf-8");

function source(over: Partial<Source> = {}): Source {
  return {
    id: "s1",
    provider: "google_drive",
    kind: "drive",
    owner_email: "asha@example.com",
    state: "ready",
    account_email: "asha@example.com",
    node_count: 4,
    discovered_at: null,
    last_error: null,
    created_at: null,
    updated_at: null,
    ...over,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("connectTarget", () => {
  it("accepts the Google consent address", () => {
    const url = "https://accounts.google.com/o/oauth2/v2/auth?x=1";
    expect(connectTarget(url)).toBe(url);
  });

  it.each([
    "http://accounts.google.com/o/oauth2/v2/auth",
    "https://accounts.google.com.evil.com/",
    "https://evil.com/?accounts.google.com",
    "javascript:alert(1)",
    "//accounts.google.com",
    "",
  ])("refuses %s", (url) => {
    expect(connectTarget(url)).toBeNull();
  });

  it("refuses a missing address", () => {
    expect(connectTarget(null)).toBeNull();
    expect(connectTarget(undefined)).toBeNull();
  });
});

describe("driveLink", () => {
  it("accepts https on Drive and Docs", () => {
    expect(driveLink("https://drive.google.com/file/d/1/view")).not.toBeNull();
    expect(driveLink("https://docs.google.com/spreadsheets/d/1/edit")).not.toBeNull();
  });

  it.each(["javascript:alert(1)", "http://drive.google.com/x", "https://drive.google.com.evil.com/x", "https://evil.com/"])(
    "refuses %s",
    (url) => {
      expect(driveLink(url)).toBeNull();
    },
  );

  it("refuses a missing link", () => {
    expect(driveLink(null)).toBeNull();
  });
});

describe("returnMessage", () => {
  const codes = [
    "invalid_state",
    "google_not_configured",
    "scope_missing",
    "scope_extra",
    "no_refresh_token",
    "exchange_failed",
    "account_unreadable",
    "no_code",
    "access_denied",
    "failed",
  ];

  it("gives a plain sentence for each code, and never the raw code", () => {
    for (const code of codes) {
      const text = returnMessage(code);
      expect(text.length).toBeGreaterThan(10);
      expect(text).toMatch(/[.]$/);
      expect(text).not.toContain(code);
    }
  });

  it("gives a different sentence to each code except the generic one", () => {
    const specific = codes.filter((c) => c !== "failed").map(returnMessage);
    expect(new Set(specific).size).toBe(specific.length);
  });

  it("answers an unknown code with the generic line, without the code", () => {
    const text = returnMessage("weird_thing");
    expect(text).toBe(returnMessage("failed"));
    expect(text).not.toContain("weird_thing");
    expect(returnMessage(null)).toBe(text);
  });

  it("knows a code that arrives in capitals", () => {
    expect(returnMessage("ACCESS_DENIED")).toBe(returnMessage("access_denied"));
  });
});

describe("disconnectCopy", () => {
  it("says the tree leaves, the reading stops, and Gmail stays connected", () => {
    const copy = disconnectCopy(source({ account_email: "asha@example.com" }));
    expect(copy.body).toContain("Gmail");
    expect(copy.body).toContain("stays connected");
    expect(copy.body).toContain("asha@example.com");
    expect(copy.body).toContain("folder tree");
    expect(copy.body).toContain("Your Gmail stays connected");
  });

  it("names the owner when a manager disconnects another member's drive", () => {
    const copy = disconnectCopy(source({ owner_email: "ravi@example.com", account_email: "ravi@example.com" }), false);
    expect(copy.body).toContain("ravi@example.com's Gmail stays connected");
    expect(copy.body).toContain("ravi@example.com can connect it again");
    expect(copy.body).not.toContain("Your Gmail");
    expect(copy.body).not.toContain("later");
  });
});

describe("visibleSources and shouldPoll", () => {
  it("drop a disconnected row", () => {
    const list = { off: false, sources: [source({ id: "a" }), source({ id: "b", state: "disconnected" })] };
    expect(visibleSources(list).map((s) => s.id)).toEqual(["a"]);
    expect(sourceCount(list)).toBe("1");
    expect(sourceCount({ off: false, sources: [] })).toBeNull();
  });

  it("poll only while a drive is being read", () => {
    expect(shouldPoll([source({ state: "discovering" })])).toBe(true);
    expect(shouldPoll([source({ state: "ready" }), source({ state: "failed" }), source({ state: "needs_reconnect" })])).toBe(false);
    expect(shouldPoll([])).toBe(false);
  });
});

describe("the small words", () => {
  it("name each state", () => {
    expect(stateLabel("discovering")).toBe("Reading your folders");
    expect(stateLabel("ready")).toBe("Connected");
    expect(stateLabel("failed")).toBe("Could not read the drive");
    expect(stateLabel("needs_reconnect")).toBe("Needs to reconnect");
  });

  it("name the owner", () => {
    expect(ownerLine(source(), "ASHA@example.com")).toBe("You");
    expect(ownerLine(source({ owner_email: "ravi@example.com" }), "asha@example.com")).toBe("ravi@example.com");
  });

  it("name the type of a file", () => {
    expect(typeWord("application/vnd.google-apps.document")).toBe("Doc");
    expect(typeWord("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).toBe("Sheet");
    expect(typeWord("application/vnd.google-apps.presentation")).toBe("Slide");
    expect(typeWord("application/pdf")).toBe("PDF");
    expect(typeWord("image/png")).toBe("Image");
    expect(typeWord("application/zip")).toBe("File");
    expect(typeWord(null)).toBe("File");
  });

  it("say how many items a cut tree shows", () => {
    expect(truncatedNotice(500)).toBe("Showing the first 500 items. More items exist.");
  });

  it("explain a refused connect", () => {
    expect(connectFailure(new KnowledgeError("Not Found", 404))).toBe(
      "Connecting Google Drive is not on for your organization yet.",
    );
    expect(connectFailure(new KnowledgeError("google_not_configured", 503))).toBe(returnMessage("google_not_configured"));
    expect(connectFailure(new KnowledgeError("signing_unavailable", 503))).toBe(
      "Could not start the connection. Try again.",
    );
    expect(connectFailure(new Error("boom"))).toBe("Could not start the connection. Try again.");
  });
});

describe("listSources", () => {
  function stub(status: number, body: unknown) {
    vi.stubGlobal(
      "fetch",
      async () =>
        new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
    );
  }

  it("gives off:true on a 404", async () => {
    stub(404, { detail: "Not Found" });
    await expect(listSources()).resolves.toEqual({ sources: [], off: true });
  });

  it("rethrows a 500", async () => {
    stub(500, { detail: "boom" });
    await expect(listSources()).rejects.toBeInstanceOf(KnowledgeError);
  });

  it("gives the rows of a 200", async () => {
    stub(200, { sources: [source()] });
    const out = await listSources();
    expect(out.off).toBe(false);
    expect(out.sources).toHaveLength(1);
  });
});

describe("the source fences", () => {
  const view = read("components", "SourcesView.tsx");
  const lib = read("lib", "sources.ts");
  const layout = read("layout.tsx");
  const rail = read("components", "KnowledgeRail.tsx");
  const strip = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

  it("the layout takes the Google return and replaces the address once", () => {
    expect(layout).toContain("SOURCE_CONNECTED_PARAM");
    expect(layout).toContain("SOURCE_ERROR_PARAM");
    expect(layout).toContain("returnMessage(errorParam)");
    expect(layout).toMatch(/router\.replace\(KNOWLEDGE_SOURCES\)/);
    expect(layout).toMatch(/returnHandled\.current/);
  });

  it("the rail draws a Sources row", () => {
    expect(rail).toMatch(/<RailRow\s+label="Sources"/);
  });

  it("the view and the client read no file text", () => {
    for (const code of [strip(view), strip(lib)]) {
      expect(code).not.toMatch(/node\.(text|content|body)\b/);
      expect(code).not.toMatch(/window\.confirm/);
    }
  });

  it("an external link carries noopener beside target=_blank", () => {
    expect(view).toMatch(/target="_blank"\s+rel="noopener noreferrer"/);
  });

  it("the view asks only through the client, and imports no lucide or base-ui", () => {
    const code = strip(view);
    expect(code).not.toMatch(/\bfetch\(/);
    expect(code).not.toMatch(/lucide-react|@base-ui/);
  });
});
