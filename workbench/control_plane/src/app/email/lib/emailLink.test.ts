/**
 * The in-app link to one email (owner report, 2026-10-09).
 *
 * Mutations caught: the helper links an id that is not a UUID; the account
 * goes missing or unchecked; the chat renderer stops opening the link in this
 * tab; the "Open in inbox" act goes back to a bare `/email` with no id; the
 * deep-link reader stops reading the shared name.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { isInAppPath } from "@/components/ui/EntityPill";
import { emailIdFromSearch, emailLink } from "./emailLink";

const MAIL = "0f8fad5b-d9cb-469f-a165-70867728950e";
const BOX = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

describe("emailLink", () => {
  it("builds /email?email=<id>&account=<id>", () => {
    expect(emailLink(MAIL, BOX)).toBe(`/email?email=${MAIL}&account=${BOX}`);
    expect(emailLink(MAIL)).toBe(`/email?email=${MAIL}`);
  });

  it("lower-cases the ids and trims them", () => {
    expect(emailLink(` ${MAIL.toUpperCase()} `)).toBe(`/email?email=${MAIL}`);
  });

  it("refuses an id that is not a UUID, so a link cannot name another page", () => {
    for (const bad of ["", "../admin", "javascript:alert(1)", `${MAIL}x`, 7, null, undefined]) {
      expect(emailLink(bad)).toBeNull();
    }
  });

  it("drops an account that is not a UUID, and keeps the mail", () => {
    expect(emailLink(MAIL, "box-a&email=evil")).toBe(`/email?email=${MAIL}`);
  });

  it("is an in-app path, so the chat opens it in this tab", () => {
    expect(isInAppPath(emailLink(MAIL, BOX)!)).toBe(true);
    expect(isInAppPath(emailLink(MAIL)!)).toBe(true);
  });

  it("reads the id back from a query string", () => {
    expect(emailIdFromSearch(new URLSearchParams(`email=${MAIL}&account=${BOX}`))).toBe(MAIL);
    expect(emailIdFromSearch(new URLSearchParams("email=nope"))).toBeNull();
    expect(emailIdFromSearch(null)).toBeNull();
  });
});

describe("the callers build the link here", () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf-8");

  it("the chat card's Open in inbox pushes the link, not a bare /email", () => {
    const src = read("../../../components/email/EmailToolCards.tsx");
    const open = src.slice(src.indexOf("function useOpenEmail()"));
    const body = open.slice(0, open.indexOf("\n}\n"));
    expect(body).toMatch(/router\.push\(emailLink\(id, accountId\)/);
    expect(body).not.toMatch(/router\.push\("\/email"\)/);
  });

  it("the page reads the id through the shared reader", () => {
    expect(read("../components/EmailDeepLink.tsx")).toMatch(/emailIdFromSearch\(/);
    expect(read("../page.tsx")).toMatch(/<EmailDeepLink /);
  });
});
