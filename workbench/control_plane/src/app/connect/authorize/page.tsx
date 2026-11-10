/**
 * /connect/authorize — the consent page an outside agent opens (WS-55 EA-4a).
 *
 * Spec: `project-docs/specs/external_agent_access.md` §10.5. The
 * `authorization_endpoint` of the agent metadata points here
 * (`agent_edge/metadata.py`).
 *
 * NOT public. A signed-out member reaches `proxy.ts`, which sends them to
 * `/signin?callbackUrl=<path and query>`, and the sign-in brings them back
 * here with the same query. The page carries no chrome (`CHROMELESS_ROUTES`
 * in `lib/nav.ts`). `next.config.ts` puts `frame-ancestors 'none'` and
 * `X-Frame-Options: DENY` on it, so no other site can frame it.
 *
 * The server reads the query once, and `ConsentScreen` does the rest in the
 * browser, through the two same-origin BFF routes.
 */
import type { Metadata } from "next";

import ConsentScreen from "./ConsentScreen";
import { parseAuthorizeRequest } from "./consent";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Connect an agent · Metorite",
  // The agent's state and PKCE challenge are in this page's URL. The page
  // sends the browser to the agent's address, so it sends no Referer.
  referrer: "no-referrer",
};

export default async function ConnectAuthorizePage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  return <ConsentScreen parsed={parseAuthorizeRequest(await searchParams)} />;
}
