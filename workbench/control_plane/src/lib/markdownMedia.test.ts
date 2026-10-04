/**
 * The one decision behind the click-to-load gate: would this src make the
 * browser contact a host other than the app? `remoteHost` answers it, and
 * `markdownImage.test.ts` draws every renderer that asks.
 */
import { describe, expect, it } from "vitest";

import {
  markdownUrlTransform,
  remoteHost,
  resolveMediaSrc,
  UNKNOWN_HOST,
} from "@/lib/markdownMedia";

const APP = "https://app.metorite.com";

describe("remoteHost", () => {
  it("names the host of a URL on another origin", () => {
    expect(remoteHost("https://attacker.example/p.png?d=1", APP)).toBe("attacker.example");
    expect(remoteHost("http://attacker.example:8080/p.png", APP)).toBe("attacker.example:8080");
  });

  it("reads the URL the way the browser fetches it", () => {
    // A protocol-relative src, a backslash, a leading space and an upper-case
    // scheme all reach attacker.example in a browser.
    expect(remoteHost("//attacker.example/p.png", APP)).toBe("attacker.example");
    expect(remoteHost("/\\attacker.example/p.png", APP)).toBe("attacker.example");
    expect(remoteHost("  https://attacker.example/p.png", APP)).toBe("attacker.example");
    expect(remoteHost("HTTPS://attacker.example/p.png", APP)).toBe("attacker.example");
  });

  it("lets a data: URI, a blob: URL and a same-origin path through", () => {
    expect(remoteHost("data:image/png;base64,AAAA", APP)).toBeNull();
    expect(remoteHost(`blob:${APP}/0b8c`, APP)).toBeNull();
    expect(remoteHost("/api/agent/workspace/s1/file?path=outputs%2Fa.png", APP)).toBeNull();
    expect(remoteHost("chart.png", APP)).toBeNull();
    expect(remoteHost(`${APP}/brand/logo.png`, APP)).toBeNull();
  });

  it("treats a same-origin proxy that names another URL as remote", () => {
    const target = encodeURIComponent("https://attacker.example/p.png?d=1");
    expect(remoteHost(`/api/email/image-proxy?url=${target}`, APP)).toBe("attacker.example");
    expect(remoteHost("/api/email/image-proxy?url=//attacker.example/p.png", APP)).toBe(
      "attacker.example",
    );
  });

  it("treats an escaped ? in the path of a same-origin URL as remote", () => {
    // A catch-all proxy decodes the segment and joins it into the gateway URL,
    // so `%3F` becomes a real query there (the PR's review, finding 1).
    const hidden = "/api/email/image-proxy%3Furl=https%253A%252F%252Fattacker.example%252Fp.png%253Fd%253D1";
    expect(remoteHost(hidden, APP)).toBe("attacker.example");
    expect(remoteHost(`${APP}${hidden}`, APP)).toBe("attacker.example");
    expect(remoteHost("/%61pi/email/image-proxy%3Furl=x", APP)).toBe("app.metorite.com");
    expect(remoteHost("/brand/logo.png%3Fx", APP)).toBe("app.metorite.com");
  });

  it("gates every API path but the workspace file proxy", () => {
    expect(remoteHost("/api/projects/attachments/a1/download", APP)).toBe("app.metorite.com");
    expect(remoteHost("/api/agent/workspace/s%3Fx/file?path=a.png", APP)).toBe("app.metorite.com");
    expect(remoteHost("/api/agent/workspace/0b8c-41/file?path=outputs%2Fa.png", APP)).toBeNull();
  });

  it("gates a scheme the browser would not load, at the cost of one click", () => {
    expect(remoteHost("ftp://attacker.example/p.png", APP)).toBe("attacker.example");
    expect(remoteHost("http://[::1", APP)).toBe(UNKNOWN_HOST);
  });
});

describe("resolveMediaSrc", () => {
  it("routes a workspace path through the file proxy", () => {
    expect(resolveMediaSrc("../img/a.png", "s1", "outputs/sub/r.md")).toBe(
      "/api/agent/workspace/s1/file?path=outputs%2Fimg%2Fa.png",
    );
    expect(resolveMediaSrc("/img/a.png", "s1", "outputs/r.md")).toBe(
      "/api/agent/workspace/s1/file?path=img%2Fa.png",
    );
  });

  it("returns a full URL unchanged, for remoteHost to gate", () => {
    expect(resolveMediaSrc("https://attacker.example/p.png", "s1", "r.md")).toBe(
      "https://attacker.example/p.png",
    );
  });

  it("returns a path unchanged when there is no session", () => {
    expect(resolveMediaSrc("/brand/logo.png", undefined, undefined)).toBe("/brand/logo.png");
    expect(resolveMediaSrc("/brand/logo.png", "", "r.md")).toBe("/brand/logo.png");
  });
});

describe("markdownUrlTransform", () => {
  it("keeps a data:image src on an img", () => {
    const uri = "data:image/png;base64,AAAA";
    expect(markdownUrlTransform(uri, "src", { tagName: "img" })).toBe(uri);
  });

  it("blanks a data: URL anywhere else, and javascript: everywhere", () => {
    expect(markdownUrlTransform("data:text/html,<b>x</b>", "href", { tagName: "a" })).toBe("");
    expect(markdownUrlTransform("data:text/html,x", "src", { tagName: "img" })).toBe("");
    expect(markdownUrlTransform("javascript:alert(1)", "href", { tagName: "a" })).toBe("");
  });

  it("leaves an https URL to the gate", () => {
    const url = "https://attacker.example/p.png";
    expect(markdownUrlTransform(url, "src", { tagName: "img" })).toBe(url);
  });
});
