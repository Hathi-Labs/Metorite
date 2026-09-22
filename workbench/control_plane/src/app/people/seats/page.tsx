"use client";

/**
 * People Center · §5.6 Seats and roles (WS-28f).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.6 ·
 * `org_access_control.md` · `groups_sessions_authority.md` §1.
 *
 * **Why it is here and not in Settings.** "Who is in Sales" is a People
 * question, and answering it today means leaving the app for
 * `/settings/groups` — which lists groups and expands their members, so you
 * read it group-first. This reads the same data person-first, which is the
 * direction somebody asks the question in.
 *
 * **No new endpoint and no new write path.** The matrix pivots
 * `GET /admin/groups`, `GET /admin/members` and `GET /people` (see
 * `../lib/seats.ts`), and a toggle calls the two endpoints that already own
 * group membership:
 *
 *     POST   /admin/groups/{slug}/members   { email }
 *     DELETE /admin/groups/{slug}/members/{email}
 *
 * A second write path for a thing that has one is the CLAUDE.md §5 defect,
 * and it is the expensive kind here: group membership is a Center grant
 * (D12), so two doors means two places to get an authorization check right.
 *
 * ## Three states, and the controls differ rather than the labels
 *
 * - **`admin:members:manage`** — the checkboxes are live.
 * - **`admin:members:read` only** — the matrix renders with ticks and NO
 *   checkboxes. §4.3's rule: a viewer without write rights sees the surface
 *   read-only with no disabled-button theatre, so the controls are absent.
 * - **Neither** — the tab is not in the bar and the gateway refuses the
 *   reads. Nothing here is the enforcement.
 *
 * ⚠️ **A seat needs a login, and a directory-only person cannot hold one.**
 * `org_group_member` references `app_user`. Their row shows no checkbox at
 * all and offers the invite instead, which is §5.6's other half.
 *
 * ## What this slice does NOT do, and why
 *
 * §5.6 asks that a non-owner's toggle "produce a request in the existing
 * access-request queue". **That queue cannot carry it.** `access_request`
 * (migration 143) is the SIGN-IN queue: unique on `lower(email)`, one row per
 * person, no field for what is being asked, and approving one provisions an
 * `app_user` through `_provision_member`. There is nowhere to put "please add
 * Priya to Sales", and a second row for the same address is refused by the
 * index. Filed as a handoff rather than invented here.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import PageHeader from "@/components/PageHeader";
import Badge from "@/components/ui/Badge";
import { Checkbox } from "@/components/ui/Checkbox";
import { useAccess } from "@/components/AccessProvider";
import { hasCapability } from "@/lib/access";

import { PAGE_FRAME_BLOCK } from "../lib/frame";
import {
  type SeatGroup,
  type SeatMatrix,
  type SeatMember,
  type SeatPerson,
  type SeatRow,
  actionFor,
  buildSeatMatrix,
} from "../lib/seats";

async function readJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return (await res.json()) as T;
}

export default function SeatsPage() {
  const { access } = useAccess();
  const canManage = hasCapability(access, "admin:members:manage");

  const [matrix, setMatrix] = useState<SeatMatrix | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string>("");

  /**
   * The three reads, pivoted.
   *
   * `live` is the app's idiom for this (`quality/page.tsx` and its
   * siblings): a toggle re-reads, and a member who clicks and leaves would
   * otherwise set state on a component that is gone.
   */
  const load = useCallback(async (live: () => boolean = () => true) => {
    try {
      const [groups, members, directory] = await Promise.all([
        readJson<SeatGroup[]>("/api/admin/groups"),
        readJson<SeatMember[]>("/api/admin/members"),
        readJson<{ rows: SeatPerson[] }>("/api/people"),
      ]);
      if (!live()) return;
      setMatrix(buildSeatMatrix(groups, members, directory.rows ?? []));
      setError("");
    } catch (e) {
      if (live()) setError(e instanceof Error ? e.message : "Could not load seats");
    }
  }, []);

  useEffect(() => {
    let live = true;
    void load(() => live);
    return () => {
      live = false;
    };
  }, [load]);

  const toggle = async (row: SeatRow, slug: string) => {
    const act = actionFor(row, slug);
    if (act === "blocked") return;
    const key = `${row.email}:${slug}`;
    setBusy(key);
    try {
      const res =
        act === "add"
          ? await fetch(`/api/admin/groups/${encodeURIComponent(slug)}/members`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ email: row.email }),
            })
          : await fetch(
              `/api/admin/groups/${encodeURIComponent(slug)}/members/${encodeURIComponent(row.email)}`,
              { method: "DELETE" },
            );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `${res.status}`);
      }
      // Re-read rather than patch the local matrix: the write can also grant
      // a Center feature (`grant_center_access`), so the server's answer is
      // wider than the checkbox that asked for it.
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The change was refused");
    } finally {
      setBusy("");
    }
  };

  return (
    <main className={PAGE_FRAME_BLOCK}>
      <PageHeader
        title="Seats and roles"
        subtitle="Which teams and Centers each person is in, and what their organisation role is."
        meta={matrix ? `${matrix.rows.length} people` : "loading…"}
        actions={
          <Link
            href="/settings/groups"
            className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground tech-transition hover:border-primary/30 hover:text-foreground"
          >
            Manage teams
          </Link>
        }
      />

      {error && (
        <p className="mb-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </p>
      )}

      {!canManage && matrix && (
        <p className="mb-3 text-xs text-muted-foreground">
          You can see who is where. Changing it needs the
          <code className="mx-1 font-mono">admin:members:manage</code>
          permission.
        </p>
      )}

      {matrix && matrix.directoryOnly > 0 && (
        <p className="mb-3 text-xs text-muted-foreground">
          {matrix.directoryOnly}{" "}
          {matrix.directoryOnly === 1 ? "person has" : "people have"} no login, so
          they cannot hold a seat yet. Invite them from{" "}
          <Link href="/settings/organization" className="underline">
            Organisation
          </Link>
          .
        </p>
      )}

      {matrix === null ? (
        <p className="text-xs text-muted-foreground">Loading…</p>
      ) : matrix.rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Nobody in the directory yet.
        </p>
      ) : (
        // The table is the one thing allowed to scroll sideways — a matrix
        // with a dozen columns cannot wrap, and the rest of the page must not
        // move with it.
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-max text-xs">
            <thead>
              <tr className="border-b border-border bg-muted/40 text-left">
                <th className="sticky left-0 z-10 bg-muted/40 px-3 py-2 font-medium">
                  Person
                </th>
                <th className="px-3 py-2 font-medium">Role</th>
                {matrix.columns.map((g) => (
                  <th key={g.slug} className="px-3 py-2 text-center font-medium">
                    <span className="block">{g.display_name || g.slug}</span>
                    {g.is_center && (
                      <span className="text-[10px] font-normal text-muted-foreground">
                        Center
                      </span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.rows.map((row) => (
                <tr key={row.email} className="border-b border-border last:border-0">
                  <td className="sticky left-0 z-10 bg-card px-3 py-2">
                    <div className="min-w-0">
                      {row.id ? (
                        <Link
                          href={`/people/${row.id}`}
                          className="font-medium text-foreground hover:underline"
                        >
                          {row.name}
                        </Link>
                      ) : (
                        <span className="font-medium text-foreground">{row.name}</span>
                      )}
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {row.title || row.email}
                      </span>
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    {row.hasLogin ? (
                      <Link
                        href={`/settings/members/${encodeURIComponent(row.email)}`}
                        title="Roles are edited in Organisation"
                      >
                        <Badge tone="neutral" size="xs">
                          {row.roles.length ? row.roles.join(" · ") : "member"}
                        </Badge>
                      </Link>
                    ) : (
                      <Badge tone="warning" size="xs">
                        no login
                      </Badge>
                    )}
                  </td>
                  {matrix.columns.map((g) => {
                    const cell = row.cells.find((c) => c.slug === g.slug)!;
                    const key = `${row.email}:${g.slug}`;
                    return (
                      <td key={g.slug} className="px-3 py-2 text-center">
                        {!row.hasLogin ? (
                          // No control at all. A box that cannot be ticked is
                          // worse than an empty cell, because it invites a
                          // click that silently does nothing.
                          <span className="text-muted-foreground">—</span>
                        ) : canManage ? (
                          <Checkbox
                            checked={cell.member}
                            disabled={busy === key}
                            onChange={() => void toggle(row, g.slug)}
                            aria-label={`${row.name} in ${g.display_name || g.slug}`}
                          />
                        ) : cell.member ? (
                          <span aria-label="in this group">✓</span>
                        ) : (
                          <span className="text-muted-foreground" aria-label="not in this group">
                            ·
                          </span>
                        )}
                        {cell.lead && (
                          <span className="ml-1 text-[10px] text-muted-foreground">
                            lead
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {matrix && matrix.membersWithoutADirectoryRow > 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          {matrix.membersWithoutADirectoryRow}{" "}
          {matrix.membersWithoutADirectoryRow === 1
            ? "person with a login has"
            : "people with a login have"}{" "}
          no directory row. They are still listed, because a seat is real
          whether or not the directory knows about them.
        </p>
      )}

      {canManage && (
        <p className="mt-3 text-xs text-muted-foreground">
          A tick is a group membership, and for a Center it also grants that
          Center&rsquo;s feature. Roles are edited in{" "}
          <Link href="/settings/organization" className="underline">
            Organisation
          </Link>
          , because one thing has one editor.
        </p>
      )}
    </main>
  );
}
