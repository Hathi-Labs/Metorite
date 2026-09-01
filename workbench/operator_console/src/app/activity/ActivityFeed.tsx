"use client";

import { useCallback, useEffect, useState } from "react";

// The audit feed — WS-31 CP-12f.
//
// ⚠️ **The cursor is EPHEMERAL and lives only in this component's state.**
// That is not an implementation detail, it is the property that makes the
// ordering safe. `operator_activity.py::CURSOR_IS_EPHEMERAL` carries the
// measured reason: `now()` is a transaction-start stamp, so a late commit can
// land behind a row already read, and a scroll can miss it. Every fresh read
// starts at the newest row, so the loss is bounded to one scroll.
//
// **Do not persist this cursor.** Putting it in the URL, in localStorage or in
// a saved view would turn a bounded miss into a permanent one, which is H-7 on
// migration 168 exactly.

type Row = {
  id: string;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  created_at: string | null;
  org_slug: string | null;
  org_name: string | null;
};

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

// `breakglass` is the shared token: it bypasses every role and every window.
// It should be rare, so it is marked rather than left to blend in.
function ActorCell({ actor }: { actor: string }) {
  if (actor === "breakglass") {
    return (
      <span
        className="warnbadge"
        title="The shared token. It bypasses roles and elevation — every use is also logged as a warning."
      >
        break-glass
      </span>
    );
  }
  return <>{actor}</>;
}

export default function ActivityFeed({ actions }: { actions: string[] }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [org, setOrg] = useState("");

  const load = useCallback(
    async (next: string | null, append: boolean) => {
      setBusy(true);
      setError(null);
      const q = new URLSearchParams();
      if (actor.trim()) q.set("actor", actor.trim());
      if (action) q.set("action", action);
      if (org.trim()) q.set("org_slug", org.trim());
      if (next) q.set("cursor", next);
      try {
        const res = await fetch(`/api/operator/activity?${q.toString()}`);
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as {
            detail?: string;
            error?: string;
          };
          setError(
            res.status === 500
              ? "The Console answered 500. On a newly deployed box this is " +
                "usually migration 009 not applied yet (H-64)."
              // The BFF gate speaks in `error`, the Console in `detail` —
              // read both, or a signed-out 401 loses its one useful sentence.
              : body.detail ?? body.error ?? `The Console answered ${res.status}.`,
          );
          return;
        }
        const body = (await res.json()) as {
          activity: Row[];
          next_cursor: string | null;
        };
        setRows((prev) => (append ? [...prev, ...body.activity] : body.activity));
        setCursor(body.next_cursor);
      } catch {
        setError("The Console did not answer — check the network and reload.");
      } finally {
        setBusy(false);
      }
    },
    [actor, action, org],
  );

  // Re-read from the top whenever a filter changes. ⚠️ From the TOP, and with
  // the cursor dropped: keeping a cursor across a filter change would page
  // into a position that belonged to a different query.
  useEffect(() => {
    void load(null, false);
  }, [load]);

  return (
    <>
      <div className="panel">
        <div className="row">
          <div>
            <label htmlFor="f-actor">Person</label>
            <input
              id="f-actor"
              value={actor}
              placeholder="somebody@fracktal.in"
              onChange={(e) => setActor(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="f-action">Action</label>
            <select
              id="f-action"
              value={action}
              onChange={(e) => setAction(e.target.value)}
            >
              <option value="">Everything</option>
              {actions.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="f-org">Company</label>
            <input
              id="f-org"
              value={org}
              placeholder="acme"
              onChange={(e) => setOrg(e.target.value)}
            />
          </div>
        </div>
      </div>

      {error && <div className="result err">{error}</div>}

      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>Action</th>
              <th>Company</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !busy && (
              <tr>
                <td colSpan={5} className="empty">
                  Nothing matches. An unknown company or person returns an
                  empty page rather than an error, on purpose.
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="small">{when(r.created_at)}</td>
                <td>
                  <ActorCell actor={r.actor} />
                </td>
                <td>{r.action}</td>
                <td>
                  {/* A null company is normal: `operator.*` acts name none, and
                      a purged customer's rows keep their history with the
                      organization set to NULL (D63). */}
                  {r.org_slug ? (
                    <span title={r.org_name ?? undefined}>{r.org_slug}</span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td className="small">
                  <code>{JSON.stringify(r.detail)}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="row">
        {cursor && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void load(cursor, true)}
          >
            {busy ? "Loading…" : "Older"}
          </button>
        )}
        <button
          type="button"
          className="linklike"
          disabled={busy}
          onClick={() => void load(null, false)}
        >
          Back to newest
        </button>
      </div>
    </>
  );
}
