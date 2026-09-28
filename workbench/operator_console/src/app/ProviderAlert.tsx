// The site-wide vendor alert, drawn by `Shell` on every page.
// Owner request, 2026-09-28.
//
// 🔴 **Every page, because nobody opens Providers to check.** DeepSeek held
// -0.05 USD for two days and all AI failed. An alert that lives on one screen
// is an alert somebody has to go looking for.
//
// ⚠️ **A SERVER component, and it can never break the page.** It reads with
// the caller's own token, like every page read. Any failure — no session, a
// Console that predates the route, a network error — draws NOTHING. The
// Providers page itself says why a read failed. A banner that errored here
// would take down every screen for a problem on one route.
//
// ⚠️ `unknown` alone draws nothing. See `healthAlert`.

import { alertBannerClass, alertHeadline, healthAlert } from "@/lib/providerHealth";
import { readProviderHealth } from "@/lib/read";
import { staffSession } from "@/lib/session";

export default async function ProviderAlert() {
  let alert = null;
  try {
    const session = await staffSession();
    if (!session.ok) return null;
    const health = await readProviderHealth({ authToken: session.authToken });
    if (health.origin !== "live") return null;
    alert = healthAlert(health.data);
  } catch {
    return null;
  }
  if (!alert) return null;
  return (
    // `.wrap` gives it the page's own column, clear of the fixed sidebar, so it
    // sits directly above each page's `main.wrap`.
    <div className="wrap alertbar">
      <div className={alertBannerClass(alert)} role="alert">
        <strong>{alertHeadline(alert)}</strong> {alert.text}{" "}
        <a href="/providers#balance">Open Balance &amp; health</a>
      </div>
    </div>
  );
}
