// A sign-out clears the kept composer height of that account, with its other
// per-account keys (owner rule, 2026-10-11; review round 1 of the composer
// that grows). The same shape as `src/lib/shell/sidebarFolds.test.ts`.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __newPageForTests,
  bindChatScope,
  chatScope,
  clearAccountNamespaces,
  clearSignedOutAccount,
  tidyChatStorage,
} from "@/lib/sessions";

import { clearComposerHeights, readComposerHeight, writeComposerHeight } from "./composerSize";

class MemoryStorage {
  private map = new Map<string, string>();
  get length(): number {
    return this.map.size;
  }
  key(i: number): string | null {
    return [...this.map.keys()][i] ?? null;
  }
  getItem(k: string): string | null {
    return this.map.has(k) ? (this.map.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.map.set(k, String(v));
  }
  removeItem(k: string): void {
    this.map.delete(k);
  }
}

const A = chatScope("Priya@Acme.example", "org-acme") as string;
const A_ELSEWHERE = chatScope("priya@acme.example", "org-beta") as string;
const B = chatScope("ben@beta.example", "org-beta") as string;

beforeEach(() => {
  const storage = new MemoryStorage();
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("localStorage", storage);
  __newPageForTests();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("composer-size: a sign-out clears the kept height", () => {
  it("clears every organization of one email, and keeps another account's", () => {
    writeComposerHeight(A, 400);
    writeComposerHeight(A_ELSEWHERE, 420);
    writeComposerHeight(B, 440);
    clearComposerHeights(["Priya@Acme.example"]);
    expect(readComposerHeight(A)).toBeNull();
    expect(readComposerHeight(A_ELSEWHERE)).toBeNull();
    expect(readComposerHeight(B)).toBe(440);
  });

  it("clearSignedOutAccount clears the last account's height", () => {
    bindChatScope(A);
    tidyChatStorage(A);
    writeComposerHeight(A, 400);
    writeComposerHeight(B, 440);
    clearSignedOutAccount();
    expect(readComposerHeight(A)).toBeNull();
    expect(readComposerHeight(B)).toBe(440);
  });

  it("clearAccountNamespaces clears the height of every account it names", () => {
    writeComposerHeight(A, 400);
    writeComposerHeight(B, 440);
    clearAccountNamespaces(["priya@acme.example", "ben@beta.example"]);
    expect(readComposerHeight(A)).toBeNull();
    expect(readComposerHeight(B)).toBeNull();
  });
});
