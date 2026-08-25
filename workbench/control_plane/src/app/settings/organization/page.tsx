"use client";

/**
 * Organisation — the admin destination for the company (D49).
 *
 * A thin route wrapper. The surface itself is `./OrganizationAdmin`, which owns
 * the four tabs (`launch_surface.md` §6.2): Members & roles · Seat assignments ·
 * Branding · Requests.
 *
 * Separate from the component so the route stays a route: this file's only job
 * is to say what lives at `/settings/organization`, and the tab surface can be
 * rendered from somewhere else — a test, a future shell — without dragging a
 * Next page along with it.
 */

import OrganizationAdmin from "./OrganizationAdmin";

export default function OrganizationPage() {
  return <OrganizationAdmin />;
}
