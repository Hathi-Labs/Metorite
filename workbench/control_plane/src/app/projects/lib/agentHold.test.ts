/**
 * WS-55 EA-14a — one AI for each task: "Held by", stale, and Release.
 *
 * Mutations caught, by test:
 * - the card line loses the client ("Held by Priya via Claude Code");
 * - the card line ignores a display name from `labels` or `member_name`;
 * - `canReleaseRun` offers Release to a stranger, hides it from the run's
 *   member, an assignee or an admin, or offers it on a closed run;
 * - the confirm text promises that the task is finished, or loses what stays;
 * - `AgentRunPanel` stops drawing the held-by line, the stale word, or the
 *   Release control behind a confirm (the source read at the end).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  OPEN_RUN_STATUSES,
  RELEASE_COPY,
  STALE_HOLD_TEXT,
  canReleaseRun,
  heldByLine,
  releaseBody,
} from "./agentRuns";

const run = {
  member_email: "Priya@Example.test",
  member_name: "Priya Rao",
  client_via: "mcp:claude.ai:claude-code",
  client_name: null,
  client_host: null,
};

describe("heldByLine", () => {
  it("names the member and the client", () => {
    expect(heldByLine(run)).toBe("Held by Priya Rao via Claude Code");
  });

  it("prefers the name from the labels, then member_name, then the address", () => {
    const labels = new Map([["priya@example.test", "Priya R."]]);
    expect(heldByLine(run, labels)).toBe("Held by Priya R. via Claude Code");
    expect(heldByLine({ ...run, member_name: null })).toBe(
      "Held by Priya@Example.test via Claude Code",
    );
  });

  it("uses the curated client name of the gateway when it sends one", () => {
    expect(heldByLine({ ...run, client_name: "Cursor" })).toContain("via Cursor");
  });
});

describe("canReleaseRun", () => {
  const open = { member_email: "priya@example.test", status: "in_progress" as const };

  it("allows the run's member, an assignee and an admin", () => {
    expect(canReleaseRun(open, { me: "PRIYA@example.test" })).toBe(true);
    expect(canReleaseRun(open, { me: "lee@example.test", assignees: ["Lee@example.test"] })).toBe(
      true,
    );
    expect(canReleaseRun(open, { me: "root@example.test", isAdmin: true })).toBe(true);
  });

  it("refuses a stranger and a caller with no address", () => {
    expect(canReleaseRun(open, { me: "eve@example.test", assignees: ["lee@example.test"] })).toBe(
      false,
    );
    expect(canReleaseRun(open, { me: "" })).toBe(false);
    expect(canReleaseRun(open, {})).toBe(false);
  });

  it("offers nothing on a run that is closed", () => {
    for (const status of ["completed", "stopped", "failed", "abandoned", "released"] as const) {
      expect(canReleaseRun({ ...open, status }, { me: "priya@example.test", isAdmin: true })).toBe(
        false,
      );
    }
    for (const status of OPEN_RUN_STATUSES) {
      expect(canReleaseRun({ ...open, status }, { me: "priya@example.test" })).toBe(true);
    }
  });
});

describe("the words", () => {
  it("say what a release keeps and what it allows", () => {
    const text = releaseBody("Claude Code");
    expect(text).toContain("Claude Code gives this task up");
    expect(text).toContain("keeps its status and its assignees");
    expect(text).toContain("Another AI session can then claim it");
    expect(RELEASE_COPY.confirm).toBe("Release task");
    expect(STALE_HOLD_TEXT).toBe("stale");
  });
});

describe("AgentRunPanel", () => {
  const source = readFileSync(
    fileURLToPath(new URL("../components/AgentRunPanel.tsx", import.meta.url)),
    "utf8",
  );

  it("draws the held-by line, the stale word and a Release behind a confirm", () => {
    expect(source).toContain("data-agent-held-by");
    expect(source).toContain("heldByLine(run, personLabels)");
    expect(source).toContain("run.lease_expired");
    expect(source).toContain("STALE_HOLD_TEXT");
    expect(source).toContain("data-agent-release");
    expect(source).toContain("agentRunsApi.release(");
    // The control opens the confirm. It never calls the route itself.
    expect(source).toMatch(/onClick=\{\(\) => setConfirmRelease\(true\)\}/);
    expect(source).toMatch(/onConfirm=\{\(\) => void release\(\)\}/);
  });
});
