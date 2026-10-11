import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import SetupPanel from "../components/SetupPanel";
import { CrmError } from "./api";
import { firstOpen, type ConnectionInfo } from "./connect";
import {
  CONNECTED_REASON,
  FAILED_TOAST,
  changeSetup,
  setupBlock,
  setupFailure,
  setupLine,
} from "./setup";

const conn = (status: string): ConnectionInfo =>
  ({ connected: status === "connected", status, mode: "mirror" }) as ConnectionInfo;

const draw = (mode: "native" | "mirror" | null, blocked: string | null) =>
  renderToStaticMarkup(
    createElement(SetupPanel, { mode, blocked, busy: false, onChange: () => undefined }),
  );

describe("the CRM setup card (CRM-U5b)", () => {
  it("names the mode", () => {
    expect(setupLine("native")).toBe("Metorite is your CRM");
    expect(setupLine("mirror")).toBe("Synced from Zoho");
    expect(setupLine(null)).toBe("Not set up");
    expect(draw("native", null)).toContain("Metorite is your CRM");
    expect(draw("native", null)).toContain("Change CRM setup");
  });

  it("is open with no live connection", () => {
    expect(setupBlock("native", null)).toBeNull();
    expect(setupBlock("mirror", conn("needs_reconnect"))).toBeNull();
    expect(setupBlock("mirror", conn("disconnected"))).toBeNull();
    expect(draw("native", null)).not.toContain('disabled=""');
  });

  it("is off, with the reason, while Zoho is connected", () => {
    expect(setupBlock("mirror", conn("connected"))).toBe(CONNECTED_REASON);
    const html = draw("mirror", CONNECTED_REASON);
    expect(html).toContain("Disconnect Zoho first, then change the setup.");
    expect(html).toContain('disabled=""');
  });
});

describe("the confirm", () => {
  it("sends the reset, then wakes the CRM caches", async () => {
    const save = vi.fn().mockResolvedValue({});
    const invalidate = vi.fn();
    expect(await changeSetup({ save, invalidate })).toBeNull();
    expect(save).toHaveBeenCalledOnce();
    expect(invalidate).toHaveBeenCalledOnce();
  });

  it("returns the 409 message for a toast and wakes nothing", async () => {
    const message = "This CRM has records, so its setup cannot change. Remove the records first.";
    const save = vi.fn().mockRejectedValue(new CrmError(message, 409));
    const invalidate = vi.fn();
    expect(await changeSetup({ save, invalidate })).toBe(message);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("uses a plain line for an error with no message", () => {
    expect(setupFailure(new Error("network"))).toBe(FAILED_TOAST);
  });

  it("lands on the first-open choice once the mode is NULL", () => {
    const base = { loading: false, settingsMode: null, isAdmin: true } as const;
    expect(firstOpen({ ...base, connection: null })).toBe("native-only");
    expect(firstOpen({ ...base, connection: { ...conn("disconnected"), mode: null } })).toBe(
      "choose",
    );
  });
});
