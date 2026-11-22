import { describe, expect, it } from "vitest";

import { agentMapQuery } from "./api";

describe("agentMapQuery (SW-112)", () => {
  it("is the fleet map with no project", () => {
    expect(agentMapQuery()).toBe("shape=map");
    expect(agentMapQuery({ showQuiet: true })).toBe("shape=map&show_quiet=true");
  });

  it("adds project_id, encoded, for a project", () => {
    const id = "0b1c2d3e-0000-4000-8000-000000000001";
    expect(agentMapQuery({ projectId: id })).toBe(`shape=map&project_id=${id}`);
    expect(agentMapQuery({ projectId: id, showQuiet: true })).toBe(
      `shape=map&project_id=${id}&show_quiet=true`,
    );
    expect(agentMapQuery({ projectId: "a b&c" })).toBe("shape=map&project_id=a%20b%26c");
  });

  it("leaves project_id out for an empty id", () => {
    expect(agentMapQuery({ projectId: "" })).toBe("shape=map");
  });
});
