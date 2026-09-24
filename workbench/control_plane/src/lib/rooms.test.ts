/**
 * S8 fix round 5 (advisory) — a malformed room payload renders solo.
 *
 * `isShared` runs on every render of the chat header, and `RoomHeader` reads
 * `room.cap.capped` and `room.you.role`. A payload such as `[]` crashed the
 * page. `fetchRoom` now refuses a payload of the wrong shape, so `useRoom`
 * falls back to a room of one, and `isShared` answers false for anything it
 * cannot read.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchRoom, isRoomState, isShared, soloRoom } from "./rooms";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("isShared", () => {
  it("is false for a malformed room, and never throws", () => {
    for (const bad of [[], {}, { participants: [] }, { you: null }, "room", 3, null, undefined]) {
      expect(isShared(bad as never), JSON.stringify(bad)).toBe(false);
    }
  });

  it("still answers for a real room", () => {
    const solo = soloRoom("s1", "a@x.test");
    expect(isShared(solo)).toBe(false);
    const shared = {
      ...solo,
      participants: [
        { subject: "a@x.test", kind: "person" },
        { subject: "b@x.test", kind: "person" },
      ],
    } as never;
    expect(isShared(shared)).toBe(true);
  });
});

describe("isRoomState", () => {
  it("accepts a room and refuses anything else", () => {
    expect(isRoomState(soloRoom("s1", "a@x.test"))).toBe(true);
    expect(isRoomState([])).toBe(false);
    expect(isRoomState({ ...soloRoom("s1", "a@x.test"), cap: [] })).toBe(false);
    expect(isRoomState({ ...soloRoom("s1", "a@x.test"), participants: {} })).toBe(false);
  });
});

describe("fetchRoom", () => {
  it("refuses a malformed payload, so the caller falls back to solo", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    await expect(fetchRoom("s1")).rejects.toThrow("malformed");
  });

  it("returns a well-formed room", async () => {
    const room = soloRoom("s1", "a@x.test");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(room), { status: 200 })));
    await expect(fetchRoom("s1")).resolves.toEqual(room);
  });
});
