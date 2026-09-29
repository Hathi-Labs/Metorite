/**
 * WS-27bn R5b-2 — the three doors into the report builder
 * (`projects_reports.md` §8 R5b, "Rules, R5b-2" and done-when (k) and (l)).
 *
 * Each door is a link, and `reportLink` builds every link. This module only
 * decides WHICH parts each door names, and whether a door shows at all. It
 * builds no address of its own, because a second builder of the address is a
 * defect (§8 R5b, the link contract).
 *
 * - "Report on this" on a node dashboard: the node as the scope.
 * - "1:1 prep" on a person: T6 with the person as the subject.
 * - "My day" in My Tasks: T2. The builder makes the reader its subject.
 *
 * Pure, because the vitest environment has no DOM.
 */
import { type Access, canSeePath } from "@/lib/access";

import type { ReportSubjects } from "./api";
import { reportLink } from "./reportBuilder";

/** The labels of record. The owner chose these words (§8 R5b-2). */
export const REPORT_ON_THIS = "Report on this";
export const ONE_ON_ONE_PREP = "1:1 prep";
export const MY_DAY = "My day";

/** The template keys the doors open. `reports.py` `TEMPLATES` owns them. */
export const MY_DAY_TEMPLATE = "my_day";
export const ONE_ON_ONE_TEMPLATE = "one_on_one";

/** "Report on this": the builder, with this node as the scope. */
export function nodeReportLink(nodeId: string): string {
  return reportLink({ node: nodeId });
}

/**
 * "My day": the builder on T2.
 *
 * ⚠️ **The link names no subject, on purpose.** A T2 is always about its
 * author, and the builder derives the reader from the subjects answer for a
 * new report (`builderSubject`). An address typed into a link could name
 * somebody else, and the server refuses a T2 about anyone but the reader.
 */
export function myDayLink(): string {
  return reportLink({ template: MY_DAY_TEMPLATE });
}

/**
 * True when My Tasks shows "My day" (repair round 1).
 *
 * The link opens Projects, so a member who cannot open Projects would reach
 * a refusal. The control is ABSENT for that member, never disabled. The check
 * is `canSeePath`, the one that `AccessGate` runs on the address, so the
 * control and the page it opens cannot disagree. While access resolves, the
 * control waits, as the nav does.
 */
export function myDayShown(access: Access, loading: boolean): boolean {
  return !loading && canSeePath(access, "/projects");
}

/**
 * "1:1 prep" on this person, or `null` when the control must not show.
 *
 * The control shows only when the subjects answer lists the person (§7.1
 * decides who may open T6 about whom). It is ABSENT, never disabled, when:
 * the read failed (a 403 for a reader without `feature:projects` is one),
 * the answer has not arrived, the person has no address, the answer omits
 * the person, or the person is the reader (the UX pass, item 17).
 */
export function oneOnOneLink(
  answer: ReportSubjects | null | undefined,
  failed: boolean,
  email: string | null | undefined
): string | null {
  if (failed || !answer) return null;
  const wanted = (email ?? "").trim().toLowerCase();
  if (!wanted) return null;
  // The UX pass, item 17. A 1:1 with yourself is not a meeting, and "My day"
  // in My Tasks covers your own work. So the reader's own page has no control.
  if (wanted === (answer.me ?? "").trim().toLowerCase()) return null;
  const listed = (answer.people ?? []).some(
    (p) => (p.email ?? "").trim().toLowerCase() === wanted
  );
  if (!listed) return null;
  return reportLink({
    template: ONE_ON_ONE_TEMPLATE,
    subject: { kind: "person", email: wanted },
  });
}
