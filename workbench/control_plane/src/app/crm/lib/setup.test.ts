// The words of the setup hub (WS-53 CRM-X0). The actions of each state and
// the dialog are in `components/crmX0.test.ts`.
import { describe, expect, it } from "vitest";

import { firstOpen, type ConnectionInfo } from "./connect";
import { connectionLabel, countLines, hubNote, hubState, modeLabel } from "./setup";

const conn = (status: string): ConnectionInfo =>
  ({ connected: status === "connected", status, mode: "mirror" }) as ConnectionInfo;

describe("the setup hub's words", () => {
  it("names the mode", () => {
    expect(modeLabel("native")).toBe("Metorite is your CRM");
    expect(modeLabel("mirror")).toBe("Synced from Zoho");
    expect(modeLabel(null)).toBe("Not set up");
  });

  it("names the connection of each state", () => {
    const say = (status: string) => connectionLabel(hubState("mirror", conn(status)), conn(status));
    expect(say("connected")).toBe("Connected");
    expect(say("disconnected")).toBe("Disconnected");
    expect(say("needs_reconnect")).toBe("Zoho needs you to connect again");
    expect(say("needs_reset")).toContain("Another Zoho account");
    expect(connectionLabel("native", null)).toBe("No Zoho connection");
  });

  it("says that a disconnected CRM keeps its records", () => {
    expect(hubNote("mirror-disconnected")).toContain("The records stay here");
    expect(hubNote("mirror-needs-reset")).toContain("never mix");
  });

  it("lists every entity, zero included, in one order", () => {
    expect(countLines({ deals: 3 }).map((l) => [l.key, l.count])).toEqual([
      ["leads", 0],
      ["contacts", 0],
      ["organizations", 0],
      ["deals", 3],
      ["activities", 0],
      ["module_records", 0],
    ]);
  });

  it("lands on the first-open choice once the start over leaves no mode", () => {
    const base = { loading: false, settingsMode: null, isAdmin: true } as const;
    expect(firstOpen({ ...base, connection: null })).toBe("native-only");
    expect(firstOpen({ ...base, connection: { ...conn("disconnected"), mode: null } })).toBe(
      "choose",
    );
  });
});
