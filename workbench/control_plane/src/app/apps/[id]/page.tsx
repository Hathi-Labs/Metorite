import { notFound, redirect } from "next/navigation";

import { installedAppTarget } from "@/lib/shell/installedApp";

/**
 * `/apps/<package id>` — the sidebar entry of an installed Studio app
 * (`package_host.md` PKG-8a). It sends the member on to the app page of the
 * package, `/studio/package-preview?pkg=<id>`.
 *
 * A server-side redirect, as `app/access/page.tsx` does: it costs no
 * JavaScript and draws no frame inside the shell. An id that is not a package
 * id is 404. The app page reads the package as the member, so a package of
 * another member shows its own empty state there.
 */
export default async function InstalledApp({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const target = installedAppTarget(id);
  if (!target) notFound();
  redirect(target);
}
