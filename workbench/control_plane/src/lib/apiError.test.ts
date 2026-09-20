/**
 * The message a member reads when a request failed.
 *
 * ## The report this exists for
 *
 * An owner saw this in the Projects app, intermittently:
 *
 *     Unexpected token 'I', "Internal S"... is not valid JSON
 *
 * It names the JSON parser. It does not say a server failed, which server,
 * or whether to try again. The cause was a production gateway restarting
 * every five minutes under a stuck deploy loop: requests in flight got
 * Starlette's unhandled-exception reply, which is the PLAIN TEXT
 * `Internal Server Error`, and two of the four app clients ran
 * `JSON.parse(text)` one line ABOVE their `!res.ok` branch. So the careful
 * error message each of them already had was unreachable for exactly the
 * failure a person most needs explained.
 */

import { describe, expect, it } from "vitest";

import { describeFailure, detailText, readJsonBody } from "./apiError";

describe("readJsonBody", () => {
  it("does not throw on the body that caused the report", () => {
    // ⚠️ THE case. Starlette sends this as text/plain on any unhandled
    // exception, and the proxy relays the upstream content type faithfully.
    const got = readJsonBody("Internal Server Error");
    expect(got.value).toBeNull();
    expect(got.ok).toBe(false);
  });

  it("does not throw on an HTML error page", () => {
    // What a proxy or load balancer returns when the app is not there at all.
    expect(readJsonBody("<html><body>502 Bad Gateway</body></html>").ok).toBe(
      false,
    );
  });

  it("tells an EMPTY body from an unreadable one", () => {
    // A 204 is empty and fine. An HTML page is neither, and they need
    // different messages — so `ok` cannot just mean "produced a value".
    expect(readJsonBody("")).toEqual({ value: null, ok: true });
    expect(readJsonBody("nope").ok).toBe(false);
  });

  it("still parses ordinary JSON", () => {
    expect(readJsonBody('{"detail":"no"}').value).toEqual({ detail: "no" });
  });
});

describe("describeFailure", () => {
  it("tells a member to wait when the server is restarting", () => {
    // Every deploy bounces the gateway, so this is the COMMON failure, not
    // an exotic one. "Try again" is the whole correct answer.
    for (const status of [502, 503, 504]) {
      expect(describeFailure(status)).toMatch(/restarting/i);
      expect(describeFailure(status)).toMatch(/try again/i);
    }
  });

  it("says nothing was saved on a 500", () => {
    // The question a person asks after a failed write. It is true: these
    // routes commit one transaction at the end, so a 500 rolled back.
    expect(describeFailure(500)).toMatch(/nothing was saved/i);
  });

  it("quotes the status, because that is the token worth reporting", () => {
    expect(describeFailure(500)).toContain("500");
    expect(describeFailure(418)).toContain("418");
  });

  it("never renders the raw body", () => {
    // Boilerplate or an HTML page. Either one tells a member nothing and
    // looks alarming in a toast.
    const body = "Internal Server Error";
    expect(describeFailure(500, body)).not.toContain(body);
    expect(describeFailure(502, "<html>...</html>")).not.toContain("<html>");
  });

  it("does not claim a 404 means the thing does not exist", () => {
    // R5: the API answers 404 for "not yours" as well as "no such thing",
    // and the UI must not invent a distinction the server refuses to make.
    expect(describeFailure(404)).toMatch(/not yours/i);
  });

  it("distinguishes no reply from an unreadable one", () => {
    expect(describeFailure(400, "")).toMatch(/no reply/i);
    expect(describeFailure(400, "something")).not.toMatch(/no reply/i);
  });
});

describe("detailText", () => {
  it("passes a plain FastAPI detail through", () => {
    expect(detailText("Project is stopped")).toBe("Project is stopped");
  });

  it("flattens a Pydantic validation array instead of [object Object]", () => {
    // ⚠️ `detail` is a STRING for our own HTTPException and an ARRAY OF
    // OBJECTS for a validation failure. "[object Object]" has reached a
    // member's screen in this repo before.
    const got = detailText([
      { loc: ["body", "name"], msg: "field required" },
      { loc: ["body", "due"], msg: "invalid date" },
    ]);
    expect(got).toBe("field required. invalid date");
    expect(got).not.toContain("object Object");
  });

  it("answers empty for anything it cannot read, so the caller falls back", () => {
    // Empty rather than a guess: `describeFailure` is a better answer than
    // a stringified object, and `||` is what routes to it.
    expect(detailText(undefined)).toBe("");
    expect(detailText(null)).toBe("");
    expect(detailText({ unexpected: true })).toBe("");
    expect(detailText("   ")).toBe("");
  });
});
