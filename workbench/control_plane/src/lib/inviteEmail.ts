/**
 * WS-30 SC-2c — the member-invite NOTIFICATION email.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2c ·
 * `work_plan.md` §3 **D50.1** · `customer_console.md` CP-2f (the Console
 * membership this mail merely announces).
 *
 * ## It is a notification, not an acceptance flow — and that is a decision
 *
 * **There is no token in this email, and adding one is a design change.** D50.1,
 * owner-ratified 2026-08-24: identity is proven at sign-in by the IdP (Google,
 * or CP-2d's email OTP), so a mail-borne bearer would be a **second identity
 * system** beside the one `user_management_contract.md` binds — living in a
 * mailbox we do not control, with its own expiry, revocation and replay
 * questions — to authenticate somebody the IdP is about to authenticate anyway.
 * It would also be redundant: the membership rows on both planes are already
 * written by the time this is sent, so the mail carries no authority at all.
 * Losing it costs the recipient a nudge and nothing else.
 *
 * A consequence worth stating because it is what makes the whole thing safe:
 * the link carries **no token**. Its one query parameter, `email`, is the
 * recipient's own address, so the sign-in form opens with the address filled
 * (OB-0c, task 1340). The sign-in page treats it as a prefill and nothing more.
 * The code round-trip proves ownership, so a changed value costs a retype and
 * an intercepted message gains an attacker nothing.
 *
 * ## The transport is IMPORTED, never re-implemented
 *
 * `resendSender` and `emailOtpFrom` come from `lib/emailOtp.ts`. They are
 * **not moved** — that file is on the live auth path and refactoring it is a
 * sign-in outage — and they are **not copied**: a second Resend transport is
 * root `CLAUDE.md` §5's defect by name, and it would put a second
 * `Authorization: Bearer` mint into the route tree, which
 * `src/lib/gateway.test.ts`'s three-name allow-list exists to refuse.
 *
 * ## No colours, deliberately
 *
 * The HTML carries no palette, exactly as `otpEmail` carries none: an email
 * renders outside the theme system, so a colour here would be a value that can
 * never follow the org's theme. Layout only.
 */
import {
  type EmailOtpEnv,
  type ResendSender,
  emailOtpFrom,
} from "@/lib/emailOtp";

/** Where the sign-in page lives when the deployment declares no public URL. */
export const INVITE_SIGNIN_FALLBACK_ORIGIN = "https://app.metorite.com";

/** The path the invitee is sent to. */
export const INVITE_SIGNIN_PATH = "/signin";

/** The one query parameter of the link: the recipient's own address. */
export const INVITE_SIGNIN_EMAIL_PARAM = "email";

/**
 * The subset of the environment this feature reads.
 *
 * `MEMBER_INVITE_EMAIL_ENABLED` is the only new variable. The sender address is
 * `EMAIL_OTP_FROM` — **deliberately not a second one** (D49 item 4): two
 * `from` settings is two verified senders to keep in step, and the second one
 * to drift is the one that starts bouncing.
 */
export interface InviteEmailEnv extends EmailOtpEnv {
  MEMBER_INVITE_EMAIL_ENABLED?: string;
  /** The deployment's own public origin, as `deploy.sh` writes it. */
  WORKBENCH_PUBLIC_URL?: string;
}

/**
 * Whether this deployment sends invite notifications.
 *
 * Ships **DARK**, in `isEmailOtpConfigured`'s idiom rather than a new one: the
 * flag is compared to the exact string `"true"` — never truthiness, so an
 * operator who writes `MEMBER_INVITE_EMAIL_ENABLED=false` while debugging gets
 * OFF — and the Resend key must ALSO be present, or the gate would be open with
 * nothing to send through.
 *
 * ⚠️ **This gates the EMAIL and nothing else.** CP-2f's Console member write is
 * the structural half of invites and ships ungated (dark by reach: it fires on
 * an invite, which is already `admin:members:invite`-gated, and no live
 * deployment key carries the `member_admin` capability). Gating it here would
 * mean the fix for the `console-empty` funnel only worked on boxes that also
 * send mail.
 */
export function isInviteEmailConfigured(env: InviteEmailEnv): boolean {
  return (
    env.MEMBER_INVITE_EMAIL_ENABLED === "true" && Boolean(env.RESEND_API_KEY)
  );
}

/**
 * The sign-in URL an invitee is pointed at. It carries no token (D50.1). With
 * `email`, the form opens with that address filled (OB-0c).
 */
export function inviteSignInUrl(env: InviteEmailEnv, email?: string): string {
  const origin = (env.WORKBENCH_PUBLIC_URL || "").trim().replace(/\/+$/, "");
  const base = `${origin || INVITE_SIGNIN_FALLBACK_ORIGIN}${INVITE_SIGNIN_PATH}`;
  const address = (email || "").trim();
  if (!address) return base;
  return `${base}?${INVITE_SIGNIN_EMAIL_PARAM}=${encodeURIComponent(address)}`;
}

/**
 * Escape the one thing in this mail that is not ours: the organization's name.
 *
 * It is server-derived (the route reads it from the gateway's `/auth/me`, never
 * from the request body) so it is not attacker-chosen — but it is
 * customer-chosen, and a display name containing `<` would otherwise break the
 * markup of every invite that organization ever sends.
 */
function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Subject/text/html for one invite notification.
 *
 * One button, "Join {org}", opens the sign-in form with the address filled. The
 * HTML carries layout and no colour. The link is also written out as text, for a
 * client that blocks buttons.
 */
export function inviteEmail(
  orgName: string,
  signInUrl: string,
): { subject: string; text: string; html: string } {
  const subject = `You've been added to ${orgName} on Metorite`;
  const text =
    `You've been added to ${orgName} on Metorite.\n\n` +
    `Join ${orgName}: ${signInUrl}\n\n` +
    `Sign in with this email address. ` +
    `There is nothing to accept — your access is already set up. ` +
    `If you were not expecting this, you can ignore this email.`;
  const safeUrl = escapeHtml(signInUrl);
  const html =
    `<div><p>You've been added to <strong>${escapeHtml(orgName)}</strong> ` +
    `on Metorite.</p>` +
    `<p><a href="${safeUrl}" ` +
    `style="display:inline-block;padding:10px 20px;border:1px solid;` +
    `border-radius:6px;text-decoration:none;font-weight:600">` +
    `Join ${escapeHtml(orgName)}</a></p>` +
    `<p>Sign in with this email address. If the button does not work, open ` +
    `<a href="${safeUrl}">${safeUrl}</a>.</p>` +
    `<p>There is nothing to accept — your access is already set up. ` +
    `If you were not expecting this, you can ignore this email.</p></div>`;
  return { subject, text, html };
}

/**
 * Build the invite notification and hand it to the injected transport.
 *
 * `to` is the address the admin invited and the gateway accepted; `orgName` is
 * the caller's organization as the GATEWAY reported it. Neither is taken from
 * the request body — an org name from the browser would let an authenticated
 * admin put arbitrary text into a message we send from our own verified sender.
 *
 * Fails **CLOSED** in the sense that matters here: `resendSender` throws on a
 * non-2xx, and the route turns that into `email_sent: false` on an otherwise
 * successful invite. It never retries — the membership is already written, so a
 * lost notification is a nudge to repeat by hand, not a state to reconcile.
 */
export async function sendInviteEmail(
  send: ResendSender,
  opts: { to: string; orgName: string; env: InviteEmailEnv },
): Promise<void> {
  const { subject, text, html } = inviteEmail(
    opts.orgName,
    inviteSignInUrl(opts.env, opts.to),
  );
  await send({
    to: opts.to,
    from: emailOtpFrom(opts.env),
    subject,
    text,
    html,
  });
}
