import { describe, expect, it } from "vitest";
import { jobFields, withoutJob } from "./doJob";

/** NS-1: a job link opens its form once, and leaves a clean address. */

describe("jobFields", () => {
  it("reads only fill.<name> parameters", () => {
    const p = new URLSearchParams("do=compose&fill.to=alice%40x.test&fill.subject=Hi&account=7&fill.=x");
    expect(jobFields(p)).toEqual({ to: "alice@x.test", subject: "Hi" });
  });

  it("caps a field, so a link cannot carry a novel into a form", () => {
    const p = new URLSearchParams({ "fill.body": "x".repeat(5000) });
    expect(jobFields(p).body).toHaveLength(2000);
  });
});

describe("withoutJob", () => {
  it("takes out do and every fill, and keeps the app's own parameters", () => {
    const p = new URLSearchParams("do=compose&fill.to=a&account=7&view=inbox");
    expect(withoutJob("/email", p)).toBe("/email?account=7&view=inbox");
  });

  it("leaves a bare path when nothing else is left", () => {
    expect(withoutJob("/tasks", new URLSearchParams("do=capture"))).toBe("/tasks");
  });
});
