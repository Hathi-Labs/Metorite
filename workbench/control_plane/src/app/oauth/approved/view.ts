/**
 * The words of the admin-consent landing page (WS-17 EM-T3c).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.3, "EM-T3c".
 *
 * The mail callback sends an IT admin here after Microsoft returns from the
 * admin-consent link. The callback writes only `result`, from a fixed set.
 * Anyone can also open this page by hand, so the copy claims no more than
 * "Microsoft reported" a result. Microsoft holds the approval, not Metorite.
 */

export type ApprovedResult = "approved" | "declined" | "failed";

export interface ApprovedCopy {
  title: string;
  body: string;
  note: string;
}

/**
 * The one token the page reads. No value means approved, because the callback
 * sends a plain `/oauth/approved` for that result. Every value outside the
 * set, and a repeated parameter, reads as failed.
 */
export function approvedResult(raw: string | string[] | undefined): ApprovedResult {
  if (raw === undefined) return "approved";
  if (raw === "approved" || raw === "declined" || raw === "failed") return raw;
  return "failed";
}

/** Fixed copy only. Nothing from the request appears on the page. */
export const APPROVED_COPY: Record<ApprovedResult, ApprovedCopy> = {
  approved: {
    title: "Approved",
    body:
      "Microsoft reported that you approved Metorite for your organization. " +
      "Members of your organization can now connect their mailbox to Metorite.",
    note: "You can close this page.",
  },
  declined: {
    title: "Approval declined",
    body:
      "Microsoft reported that the approval was declined. " +
      "Members of your organization cannot connect their Microsoft 365 mailbox until an admin approves Metorite.",
    note: "To approve, open the approval link again and select Accept.",
  },
  failed: {
    title: "The approval did not finish",
    body:
      "Microsoft did not report an approval. " +
      "Open the approval link again, and sign in with an admin account of your organization.",
    note: "If it fails again, tell the member who sent you the link.",
  },
};
