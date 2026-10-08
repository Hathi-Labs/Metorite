import { redirect } from "next/navigation";

/**
 * `/access` moved to `/people/access` on 2026-10-05, when My Access became a
 * tab of the People app (owner directive). This page keeps old links and
 * bookmarks landing. It stays in `ALWAYS_ALLOWED`, so a member with no grants
 * reaches the redirect, and not an access-denied screen.
 *
 * A server-side redirect, as `app/settings/members/page.tsx` does: it costs no
 * JavaScript and draws no "Redirecting…" frame inside the shell.
 */
export default function AccessRedirect() {
  redirect("/people/access");
}
