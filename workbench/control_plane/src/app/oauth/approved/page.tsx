/**
 * /oauth/approved — where an IT admin lands after approving Metorite in
 * Microsoft (WS-17 EM-T3c).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.3, "EM-T3c".
 *
 * PUBLIC on purpose. The admin who approves the mail app for a company often
 * has no Metorite session, and may never have one. So `proxy.ts` lists this
 * path in `PUBLIC_PAGES`, and `nav.ts` lists it in `CHROMELESS_ROUTES`.
 *
 * What keeps a public page safe here:
 *
 * - It holds no session, reads no cookie and makes no fetch.
 * - It reads ONE query value, `result`, and `approvedResult()` maps it onto a
 *   fixed set. Any other value reads as `failed`.
 * - It renders fixed copy only. It never shows the tenant or the error text.
 *
 * ⚠️ Do not move this page under `/email/`. `featureForPath()` in
 * `lib/access.ts` matches by prefix, and the `email` feature gate would then
 * refuse the admin. Fence: `approved.test.ts`.
 */
import ApprovedCard from "./ApprovedCard";
import { approvedResult } from "./view";

export default async function OAuthApprovedPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { result } = await searchParams;
  return <ApprovedCard result={approvedResult(result)} />;
}
