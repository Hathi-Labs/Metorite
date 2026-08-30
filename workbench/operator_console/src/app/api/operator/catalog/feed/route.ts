import { syncVendorFeed } from "@/lib/console";
import { proxyToConsole } from "@/lib/route";

export const dynamic = "force-dynamic";

// POST → fetch litellm's price map and land it in `vendor_price_feed`.
// Reference data only — nothing billing reads moves. The Console falls back
// to the offline snapshot bundled in litellm when the network refuses, and
// the response names which source answered.
export async function POST(): Promise<Response> {
  return proxyToConsole((d) => syncVendorFeed(d));
}
