/**
 * What a report says when a part needs the HR grant: the ONE set of lines.
 *
 * WS-27bn R5f round 2 (owner, 2026-09-30: "easier to use and
 * non-threatening"). The old words named a permission ("HR read access").
 * A member does not know that word, and it read like a refusal. These lines
 * say what the part holds, who sees it, and what to do.
 *
 * The panels, the email, the download and the chat read these lines. The
 * chat's copy in `skill_projects` must equal `REBALANCE_HR_HINT`. Fence:
 * `reportsRedesign.test.ts`.
 */

/** Rebalancing names skills and hours, so it needs `admin:members:read`. */
export const REBALANCE_HR_HINT =
  "This part shows people's skills and hours, so only admins see it. Ask an admin if you need it.";

/** Four kinds of conflict read hours and leave, so they need the grant. */
export const CONFLICTS_HR_HINT =
  "Some conflicts use people's hours and leave, so only admins see them. Ask an admin if you need them.";

/** Capacity shows hours, leave and skills only with the grant. */
export const CAPACITY_HR_HINT =
  "People's hours, leave and skills are private, so only admins see them. Ask an admin if you need them.";
