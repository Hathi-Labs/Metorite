"use client";

/**
 * Your access — what you can reach, what you cannot, and why.
 *
 * Exists because a hidden nav pane is the platform's least debuggable failure:
 * "your roles do not grant it", "the slug was never registered", "a deny
 * override cancels it" and "the gateway is down" all render as the same
 * nothing. The only way to tell them apart used to be querying the database.
 *
 * Deliberately **ungated** (`isAlwaysAllowed`). A diagnosis page you need a
 * grant to open is useless in the one situation it exists for.
 *
 * Nothing here grants anything or asks for anything. It renders the answer
 * `/auth/me` already gave.
 */

import Link from "next/link";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import {
  type PaneReport,
  paneReport,
  summarise,
  unmappedFeatures,
} from "@/lib/accessReport";

export default function AccessPage() {
  const { access, loading } = useAccess();

  if (loading) {
    return (
      <div className="p-4 text-sm text-muted-foreground">Resolving access…</div>
    );
  }

  const report = paneReport(access);
  const unmapped = unmappedFeatures(access);
  const denied = report.filter((r) => r.status === "denied");

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col overflow-y-auto p-4">
      <h1 className="text-base font-semibold text-foreground">Your access</h1>
      <p className="mt-1 text-sm text-muted-foreground">{summarise(report)}</p>

      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Signed in as</dt>
        <dd className="text-foreground">{access.email || "nobody"}</dd>
        <dt className="text-muted-foreground">Roles</dt>
        <dd className="text-foreground">{access.roles.join(", ") || "none"}</dd>
        <dt className="text-muted-foreground">Grants</dt>
        <dd className="break-all text-foreground">
          {access.permissions.join(", ") || "none"}
        </dd>
        {access.denied.length ? (
          <>
            <dt className="text-muted-foreground">Denied</dt>
            <dd className="break-all text-foreground">
              {access.denied.join(", ")}
            </dd>
          </>
        ) : null}
      </dl>

      {denied.length ? (
        <p className="mt-3 rounded-md border border-border p-2 text-xs text-muted-foreground">
          A pane below marked <strong>needs a grant</strong> is not missing from
          the product — it is switched off for you. Someone holding{" "}
          <code>admin:roles:manage</code> can turn it on at{" "}
          <Link href="/settings/roles" className="underline">
            Settings → Roles
          </Link>
          , or per person at{" "}
          <Link href="/settings/organization" className="underline">
            Organisation → Members &amp; roles
          </Link>
          .
        </p>
      ) : null}

      <ul className="mt-3 divide-y divide-border border-y border-border">
        {report.map((row) => (
          <Row key={row.href} row={row} />
        ))}
      </ul>

      {unmapped.length ? (
        <div className="mt-3 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">
            Granted, but with no menu item
          </p>
          {/* The one failure the pane list cannot show: reachable by URL and
              undiscoverable in the UI, which also reads as "not built". */}
          <p className="mt-0.5">
            {unmapped.join(", ")} — reachable by URL, but nothing in the sidebar
            points at them.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function Row({ row }: { row: PaneReport }) {
  const ok = row.status === "granted";
  // Three outcomes, three icons, three labels. `not-launched` gets its own
  // (D49 / LS-3) precisely because a padlock beside it would be a lie: no
  // grant unlocks an app we are not offering yet, and a reader who cannot tell
  // the two apart goes and asks an admin for a permission that will not help.
  const notLaunched = row.status === "not-launched";
  const icon = ok ? "Check" : notLaunched ? "Clock" : "Lock";
  return (
    <li className="flex items-start gap-2 py-2">
      <Icon
        name={icon}
        size={13}
        className={`mt-0.5 shrink-0 ${ok ? "text-foreground" : "text-muted-foreground"}`}
      />
      <div className="min-w-0 flex-1">
        <p className="text-xs text-foreground">
          {ok ? (
            <Link href={row.href} className="hover:underline">
              {row.label}
            </Link>
          ) : (
            row.label
          )}
          <span className="text-muted-foreground"> · {row.href}</span>
        </p>
        <p className="text-[11px] text-muted-foreground">{row.reason}</p>
      </div>
      {!ok ? (
        <span className="shrink-0 rounded border border-border px-1 py-0.5 text-[10px] text-muted-foreground">
          {notLaunched ? "not available yet" : "needs a grant"}
        </span>
      ) : null}
    </li>
  );
}
