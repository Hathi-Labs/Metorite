import { describe, expect, it } from "vitest";

import { reachView, sendResult } from "./reach";

describe("reachView", () => {
  it("hides on anything but a 200", () => {
    expect(reachView(null).kind).toBe("hidden");
    expect(reachView({ status: 404, body: {} }).kind).toBe("hidden");
    expect(reachView({ status: 502, body: {} }).kind).toBe("hidden");
  });

  it("asks for a link when the phone is not linked", () => {
    const v = reachView({
      status: 200,
      body: { brief: "x", interrupts: null, window_open: false, linked: false },
    });
    expect(v.kind === "ready" && v.blocked).toMatch(/Link your phone/);
  });

  it("explains the 24-hour window when it is closed", () => {
    const v = reachView({
      status: 200,
      body: { brief: "x", interrupts: null, window_open: false, linked: true },
    });
    expect(v.kind === "ready" && v.blocked).toMatch(/24 hours/);
  });

  it("is ready with no block when linked and the window is open", () => {
    const v = reachView({
      status: 200,
      body: { brief: "*Your day*", interrupts: " ", window_open: true, linked: true },
    });
    expect(v).toEqual({ kind: "ready", brief: "*Your day*", interrupts: null, blocked: null });
  });
});

describe("sendResult", () => {
  it("reads a sent brief", () => {
    expect(sendResult({ status: 200, body: { state: "sent" } }).ok).toBe(true);
  });
  it("reads nothing to say as fine", () => {
    const r = sendResult({ status: 200, body: { state: "skipped", reason: "nothing_to_say" } });
    expect(r.ok).toBe(true);
  });
  it("reads a closed window as not sent", () => {
    const r = sendResult({ status: 200, body: { state: "skipped", reason: "window_closed" } });
    expect(r.ok).toBe(false);
  });
  it("names any other reason in words", () => {
    expect(sendResult({ status: 404, body: { detail: "not_found" } }).message).toBe(
      "Not sent: not found."
    );
  });
});
