/**
 * The untrusted-HTML policy fails closed, and its shared lists cannot move.
 *
 * Vitest runs in `node`, where DOMPurify has no DOM and `isSupported` is
 * false. In that state `DOMPurify.sanitize` returns its input unchanged. So
 * this run IS the fail-open case, and each sanitiser must return nothing.
 * `e2e/untrusted-html.spec.ts` proves the browser behaviour.
 */
import DOMPurify from "dompurify";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { sanitizeEmailHtml } from "@/app/email/components/MessageContent";
import {
  sanitizeDocxHtml,
  UNTRUSTED_FORBID_ATTR,
  UNTRUSTED_FORBID_TAGS,
} from "@/lib/untrustedHtml";

const ATTACK = '<p>hi</p><script>alert(1)</script><a href="javascript:alert(2)">x</a>';

describe("with no DOM, each sanitiser returns nothing", () => {
  it("the run really has no DOMPurify support", () => {
    // Guards the two tests below against going vacuous.
    expect(DOMPurify.isSupported).toBe(false);
  });

  it("sanitizeDocxHtml fails closed", () => {
    expect(sanitizeDocxHtml(ATTACK)).toEqual({ html: "", blockedImages: 0 });
  });

  it("sanitizeEmailHtml fails closed", () => {
    expect(sanitizeEmailHtml(ATTACK, false)).toEqual({ clean: "", hasRemote: false });
    expect(sanitizeEmailHtml(ATTACK, true)).toEqual({ clean: "", hasRemote: false });
  });
});

describe("the shared lists are frozen", () => {
  it("no importer can push onto them", () => {
    expect(Object.isFrozen(UNTRUSTED_FORBID_TAGS)).toBe(true);
    expect(Object.isFrozen(UNTRUSTED_FORBID_ATTR)).toBe(true);
    expect(() => (UNTRUSTED_FORBID_TAGS as string[]).push("p")).toThrow();
    expect(UNTRUSTED_FORBID_TAGS).toContain("form");
    expect(UNTRUSTED_FORBID_ATTR).toEqual(["ping"]);
  });
});
