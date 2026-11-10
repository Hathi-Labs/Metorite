/**
 * WS-67 OA-7d: the surface of a chat turn (`lib/shell/surface.ts`).
 *
 * The dock sends the app slug of its pane as the `surface` field of the chat
 * request. The gateway reads it as a hint for the tool packs. Each case names
 * the mutation that turns it red (R7). The hook cannot run in this node-env
 * suite, so its two body sites and the route are pinned by source, and the
 * field is proven on the pure helpers.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { PANES } from "@/lib/nav";

import { SURFACE_MAX, cleanSurface, surfaceField, surfaceOfPane } from "./surface";

const read = (rel: string): string =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8").replace(/\r\n/g, "\n");

describe("surfaceOfPane", () => {
  it("gives the app slug of the pane the member is in", () => {
    // Mutation: return the whole href, or the pane label -> "/email" -> red.
    const email = PANES.find((p) => p.href === "/email");
    expect(email).toBeTruthy();
    expect(surfaceOfPane(email)).toBe("email");
    expect(surfaceOfPane({ href: "/projects/board?x=1" })).toBe("projects");
  });

  it("gives the app slug of the Projects, Tasks and Calendar panes", () => {
    // WS-67 OA-8b: the three apps read one task store, and the server loads the
    // Projects core pack for each slug. Mutation: map "tasks" or "calendar" to
    // "projects" here -> the slug the server expects never arrives -> red.
    for (const slug of ["projects", "tasks", "calendar"]) {
      const pane = PANES.find((p) => p.href === `/${slug}`);
      expect(pane).toBeTruthy();
      expect(surfaceOfPane(pane)).toBe(slug);
      expect(surfaceField(surfaceOfPane(pane))).toEqual({ surface: slug });
    }
    expect(surfaceOfPane({ href: "/tasks?filter=today" })).toBe("tasks");
    expect(surfaceOfPane({ href: "/calendar/week" })).toBe("calendar");
  });

  it("gives nothing off every app", () => {
    // Mutation: send "" or "/" for a page that belongs to no app -> red.
    expect(surfaceOfPane(null)).toBeNull();
    expect(surfaceOfPane(undefined)).toBeNull();
    expect(surfaceOfPane({ href: "/" })).toBeNull();
  });
});

describe("cleanSurface", () => {
  it("lets a slug through, and drops what is no slug", () => {
    // Mutation: return the input untouched -> "Email/../" and 40 characters
    // reach the wire -> red.
    expect(cleanSurface("email")).toBe("email");
    expect(cleanSurface(" Email ")).toBe("email");
    expect(cleanSurface("email/../x")).toBeNull();
    expect(cleanSurface("a b")).toBeNull();
    expect(cleanSurface("1email")).toBeNull();
    expect(cleanSurface(42)).toBeNull();
    expect(cleanSurface(null)).toBeNull();
  });

  it("bounds the length", () => {
    // Mutation: drop the length bound -> a 400-character slug passes -> red.
    expect(cleanSurface("a".repeat(SURFACE_MAX))).toBe("a".repeat(SURFACE_MAX));
    expect(cleanSurface("a".repeat(SURFACE_MAX + 1))).toBeNull();
  });
});

describe("the request body", () => {
  it("carries `surface` for an app, and no key for a page with none", () => {
    // Mutation: always send the key (even null), or drop it -> red.
    expect(surfaceField("email")).toEqual({ surface: "email" });
    expect(surfaceField(null)).toEqual({});
    expect(surfaceField(undefined)).toEqual({});
    expect("surface" in surfaceField("bad slug!")).toBe(false);
    expect(JSON.stringify({ message: "hi", ...surfaceField(null) })).toBe('{"message":"hi"}');
  });

  it("is on BOTH send bodies of the hook, and not on the reconnect", () => {
    // Mutation: drop the spread from the live send or the buildBody send ->
    // a turn from /email carries no surface -> red. The reconnect replays a
    // run that already began, so it carries none.
    const hook = read("../../hooks/useAgentChat.ts");
    expect(hook.match(/\.\.\.surfaceField\(surfaceRef\.current\)/g)?.length).toBe(2);
    expect(hook).toContain("const surfaceRef = useRef(surface);");
    expect(hook).toContain("surfaceRef.current = surface;");
  });

  it("goes from the dock through AgentChat to the hook, and from no other app", () => {
    // Mutation: the dock drops the prop -> no turn carries a surface -> red.
    // Mutation: another app passes `surface=` -> the field is no longer the
    // dock's alone -> red.
    const dock = read("./AssistantDock.tsx");
    expect(dock).toContain("const surface = surfaceOfPane(here);");
    expect(dock).toContain("surface={surface}");
    const chat = read("../../components/AgentChat.tsx");
    expect(chat).toMatch(/useAgentChat\(\{[\s\S]*?systemContext,\s*surface,/);
  });

  it("is read by the route as a hint, cleaned, and never as an identity", () => {
    // Mutation: the route drops it from the gateway payload, or forwards the
    // raw value -> red.
    const route = read("../../app/api/agent/chat/route.ts");
    expect(route).toContain("...surfaceField(cleanSurface(surface)),");
    expect(route).not.toMatch(/surface[^\n]*(user|org|tenant|member)Id/i);
    const send = route.slice(route.indexOf("payload: {"));
    expect(send.indexOf("system_context")).toBeLessThan(send.indexOf("surfaceField(cleanSurface"));
  });
});
