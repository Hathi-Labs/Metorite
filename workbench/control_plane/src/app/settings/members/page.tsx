import { redirect } from "next/navigation";

/**
 * `/settings/members` → `/settings/organization` (D49, `launch_surface.md` §6.2).
 *
 * The roster moved into Organisation as its first tab. This route stays as a
 * redirect rather than being deleted, because it is linked from places a
 * grep does not reach — the access-denied copy people have already read, an
 * admin's bookmark, an onboarding message sent months ago — and a 404 there
 * reads as "the roster was removed", which is precisely the wrong conclusion.
 *
 * ⚠️ **The sibling route `./[email]` is NOT redirected and must not be.** The
 * per-person access editor still lives at `/settings/members/<email>`, the
 * roster links to it, and Next matches the more specific segment first — so
 * this file redirects the index only. Moving the editor is a separate change
 * with its own link sweep.
 *
 * A server-side redirect, not a client one: it costs no JavaScript and no
 * flash of an empty page, and the destination is admin-gated either way
 * (`canSeePath` treats `/settings/organization` as an admin surface).
 */
export default function MembersRedirect() {
  redirect("/settings/organization");
}
