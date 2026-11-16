/**
 * `questionsApi.list`: which statuses mean "this door is off" (WS-55 EA-18c).
 * A 404 (`AGENT_QUESTIONS_ORGS`) and a 403 on "For me" (no Projects feature)
 * give `null`. Anything else throws, so a page never reads a 500 as "off".
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectsApiError, questionsApi } from "./api";

const answer = (status: number) =>
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "x" }), { status })));

afterEach(() => vi.unstubAllGlobals());

describe("questionsApi.list", () => {
  it("reads a 404 as off", async () => {
    answer(404);
    expect(await questionsApi.list({ scope: "mine", limit: 0 })).toBeNull();
  });

  it("reads a 403 on For me as off, so no permanent error for a member without Projects", async () => {
    answer(403);
    expect(await questionsApi.list({ scope: "mine", limit: 0 })).toBeNull();
    expect(await questionsApi.list({ limit: 0 })).toBeNull();
  });

  it("keeps the 403 of Everyone as an error, which the list handles itself", async () => {
    answer(403);
    const err = await questionsApi.list({ scope: "everyone" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ProjectsApiError);
    expect((err as ProjectsApiError).status).toBe(403);
  });

  it("throws a 500 and does not read it as off", async () => {
    answer(500);
    const err = await questionsApi.list({ scope: "mine" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ProjectsApiError);
  });
});
