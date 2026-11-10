import { redirect } from "next/navigation";

import { appConnection, ConsoleUnconfigured } from "@/lib/console";
import { readConnection, type ArohConnection } from "@/lib/aroh";
import type { Origin } from "@/lib/source";
import { staffSession } from "@/lib/session";
import Shell, { Unconfigured } from "../Shell";
import ArohConnect from "./ArohConnect";

export const dynamic = "force-dynamic";

// "Connect Aroh" (owner request, 2026-10-30; CP-APP, task 1105).
//
// The Aroh hub needs ONE router key: the Aroh box's deployment key, with
// {resolve, seat_admin, serve}. This page lets the owner paste the hub's
// operator token once, and then Connect, Rotate or Disconnect. The Console
// mints the key and pushes it to the hub, so the key never reaches this app.
// Which organizations HAVE Aroh, and how many seats, lives on each customer
// page, on its "Aroh" tab.

export default async function ArohPage() {
  const gate = await staffSession();
  if (!gate.configured) return <Unconfigured />;
  if (!gate.ok) redirect("/login");

  let conn: ArohConnection | null = null;
  let origin: Origin = "live";
  let note: string | undefined;
  try {
    // `probe` asks the hub whether it holds a key, so a dead hub shows here.
    const res = await appConnection("aroh", true, { authToken: gate.authToken });
    if (res.status === 200) {
      conn = readConnection(JSON.parse(res.body));
      if (!conn) {
        origin = "error";
        note = "The Console answered, but not with an Aroh connection.";
      }
    } else {
      origin = res.status === 404 ? "missing" : "error";
      note =
        res.status === 404
          ? "This Console build cannot connect Aroh yet (it predates the Aroh app)."
          : `The Console returned ${res.status}.`;
    }
  } catch (e) {
    origin = "error";
    note =
      e instanceof ConsoleUnconfigured
        ? "Customer Console is not configured."
        : `Could not reach the Customer Console: ${String(e)}`;
  }

  return (
    <Shell
      title="Aroh"
      lede="Connect the Aroh hub to the Router. Give Aroh to an organization on its customer page."
      origin={origin}
      note={note}
    >
      {conn && <ArohConnect conn={conn} />}
    </Shell>
  );
}
