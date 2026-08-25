// ── Who is the viewer looking at, and what may they do to them ────────────
//
// Spec: project-docs/specs/colleague_onboarding.md §2 Step 5 (N7).
//
// Nothing here is a boundary. It is a courtesy — the same one `lib/access.ts`
// describes: hiding a control stops an owner clicking a button that would
// fail, and the gateway's `_common.assert_not_self_lockout` is what actually
// refuses. Do NOT "simplify" the server by trusting this file; a browser is
// not where an access rule can live.
//
// It exists because the roster had no idea who the viewer was, so it drew
// **Suspend** on the owner's own row exactly as on anybody else's.

import type { Member } from "@/app/settings/members/types";

/**
 * Whether `memberEmail` is the viewer's own address.
 *
 * Case-insensitive and whitespace-tolerant, matching the gateway: the row's
 * address and the signed-in identity come from the same directory but not
 * necessarily with the same casing, and an IdP that re-cases a UPN between
 * sessions must not quietly turn the guard off.
 *
 * An empty address on either side matches nothing. The signed-out shell
 * resolves to `NO_ACCESS` (`email: ""`), and "nobody" must not read as
 * "everybody".
 */
export function isSelf(viewerEmail: string, memberEmail: string): boolean {
  const viewer = (viewerEmail ?? "").trim().toLowerCase();
  const member = (memberEmail ?? "").trim().toLowerCase();
  return viewer !== "" && member !== "" && viewer === member;
}

export type RowActions = {
  /** This row is the viewer's own — label it, do not offer to end it. */
  isSelf: boolean;
  canActivate: boolean;
  canSuspend: boolean;
  canRemove: boolean;
  /**
   * Hard delete — `DELETE /admin/members/{email}/purge` (N8). Offered on
   * anybody else's row **including an already-removed one**: "remove, then
   * delete once you are sure" is the ordinary sequence, and a control that
   * vanished at the moment it became safest to use would be the wrong shape.
   */
  canPurge: boolean;
};

/**
 * The controls one roster row may offer this viewer.
 *
 * Activation is not destructive, so it is offered on any dormant row —
 * including, in principle, the viewer's own (the gateway allows it too: a
 * status of `active` is the one that gives access rather than taking it).
 * Suspend, Remove and Delete permanently are never offered on the viewer's
 * own row — the gateway refuses all three through the one shared guard
 * (`_common.assert_not_self_lockout`), and this file only spares the viewer
 * the click.
 */
export function rowActions(viewerEmail: string, member: Member): RowActions {
  const self = isSelf(viewerEmail, member.email);
  const dormant = member.status === "suspended" || member.status === "invited";
  return {
    isSelf: self,
    canActivate: dormant,
    canSuspend: !self && !dormant && member.status !== "removed",
    canRemove: !self && member.status !== "removed",
    canPurge: !self,
  };
}
