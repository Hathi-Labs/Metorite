/**
 * The sidebar path of an installed Studio app (`package_host.md` PKG-8a).
 *
 * A publish gives the owner one private install with one `nav` entry at
 * `/apps/<package id>` (`gateway/package_host/studio_app.py`). The path is
 * outside `/studio` on purpose: `installNavPanes` drops an install entry whose
 * href a system pane owns, and `/studio` is a system pane. The route
 * `app/apps/[id]/page.tsx` sends the member on to the app page of the package.
 *
 * The shell manifest `studio.json` gates `/apps` on the feature `studio`, as
 * the app page is. Fence: `installedApp.test.ts`, and in Python
 * `test_studio_publish_install.py`, which holds the two prefixes equal.
 */
import { previewHref } from "@/lib/studio/packagePreview";

/** The prefix of the path of an installed app. */
export const INSTALLED_APP_PREFIX = "/apps/";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The app page of the installed app `id`, or null when `id` is not a package id. */
export function installedAppTarget(id: string): string | null {
  return UUID.test(id) ? previewHref(id.toLowerCase()) : null;
}
