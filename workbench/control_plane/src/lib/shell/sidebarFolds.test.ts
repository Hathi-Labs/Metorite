/**
 * The sidebar's folded categories, per account (owner, 2026-10-11).
 *
 * - The folds are per account: one email in one organization.
 * - Another account's folds never apply.
 * - A sign-out clears them, through the two functions that clear the chat
 *   caches and the shell layout.
 * - The category of the current route never stays folded.
 * - A folded heading carries its count.
 *
 * Mutations, seen to fail first: drop `clearSidebarFolds` from
 * `clearSignedOutAccount`, and "clearSignedOutAccount clears the last
 * account's folds" fails. Let `isFolded` ignore `holdsCurrent`, and "never
 * folds the category of the current route" fails.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __newPageForTests,
  bindChatScope,
  chatScope,
  clearAccountNamespaces,
  clearSignedOutAccount,
  tidyChatStorage,
} from "@/lib/sessions";

import { clearSidebarFolds, foldHeading, isFolded, readFolds, sidebarFoldsKey, writeFolds } from "./sidebarFolds";

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

let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("localStorage", storage);
  __newPageForTests();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the folds are per account", () => {
  it("keys the folds by email and organization, case-folded", () => {
    expect(sidebarFoldsKey(A)).toBe("cc-sidebar-folds:priya@acme.example|org-acme");
    expect(sidebarFoldsKey(null)).toBeNull();
    expect(sidebarFoldsKey("no-scope")).toBeNull();
  });

  it("reads back what it wrote after a reload, and nothing for another account", () => {
    writeFolds(A, { across: true, studio: false });
    expect(readFolds(A)).toEqual({ across: true });
    expect(readFolds(A_ELSEWHERE)).toEqual({});
    expect(readFolds(B)).toEqual({});
    // No account yet: nothing applies, and nothing is written.
    writeFolds(null, { admin: true });
    expect(readFolds(null)).toEqual({});
  });

  it("reads nothing from a value that is not a fold map", () => {
    storage.setItem(sidebarFoldsKey(A)!, "{not json");
    expect(readFolds(A)).toEqual({});
    storage.setItem(sidebarFoldsKey(A)!, JSON.stringify(["across"]));
    expect(readFolds(A)).toEqual({});
    storage.setItem(sidebarFoldsKey(A)!, JSON.stringify({ across: "yes", admin: true }));
    expect(readFolds(A)).toEqual({ admin: true });
  });

  it("works with storage turned off", () => {
    vi.stubGlobal("window", undefined);
    expect(() => writeFolds(A, { across: true })).not.toThrow();
    expect(readFolds(A)).toEqual({});
  });
});

describe("a sign-out clears the account's folds", () => {
  it("clears every organization of one email, and keeps another account's", () => {
    writeFolds(A, { across: true });
    writeFolds(A_ELSEWHERE, { admin: true });
    writeFolds(B, { studio: true });
    clearSidebarFolds(["Priya@Acme.example"]);
    expect(readFolds(A)).toEqual({});
    expect(readFolds(A_ELSEWHERE)).toEqual({});
    expect(readFolds(B)).toEqual({ studio: true });
  });

  it("clearSignedOutAccount clears the last account's folds", () => {
    bindChatScope(A);
    tidyChatStorage(A);
    writeFolds(A, { across: true });
    writeFolds(B, { studio: true });
    clearSignedOutAccount();
    expect(readFolds(A)).toEqual({});
    expect(readFolds(B)).toEqual({ studio: true });
  });

  it("clearAccountNamespaces clears the folds of every account it names", () => {
    writeFolds(A, { across: true });
    writeFolds(B, { studio: true });
    clearAccountNamespaces(["priya@acme.example", "ben@beta.example"]);
    expect(readFolds(A)).toEqual({});
    expect(readFolds(B)).toEqual({});
  });
});

describe("what a folded category draws", () => {
  it("never folds the category of the current route", () => {
    expect(isFolded({ across: true }, "across", false)).toBe(true);
    expect(isFolded({ across: true }, "across", true)).toBe(false);
    expect(isFolded({}, "across", false)).toBe(false);
  });

  it("carries the count of its apps when folded, and only then", () => {
    expect(foldHeading("Apps", 3, true)).toBe("Apps (3)");
    expect(foldHeading("Apps", 3, false)).toBe("Apps");
  });
});
