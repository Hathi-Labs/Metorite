"use client";

import { useState } from "react";

// The operator registry table and its four writes — WS-31 CP-12g.
//
// ⚠️ **Nothing here decides anything.** Every button posts to a BFF route and
// renders whatever the Console answers. The four guards of §6.1 — no self
// write, no demoting the last admin, a known role, a known status — live in
// `operators.py`. Re-implementing them here would put a second copy of the
// rules in a place nobody would think to check when they changed.
//
// What this file DOES owe the reader is the *reason* a refusal happened, in
// their words. A 409 with no explanation reads as a broken page.

export type OperatorRow = {
  id: string;
  email: string;
  role: string;
  status: string;
  has_signed_in: boolean;
  live_sessions: number;
};

const ROLES = ["viewer", "editor", "admin"] as const;

const ROLE_HELP: Record<string, string> = {
  viewer: "Reads everything. Changes nothing.",
  editor: "Runs the business: seats, subscriptions, ordinary credit grants.",
  admin: "Adds and removes operators. Reaches the destructive acts.",
};

// The Console answers 409 for every guard in §6.1 and says which. These turn
// that into something a person can act on.
function explain(status: number, detail: string): string {
  if (status === 409 && /last active admin/i.test(detail)) {
    return (
      "That is the last active admin. Promote somebody else first, or the " +
      "console would have nobody who can add people."
    );
  }
  if (status === 409) {
    return (
      "You cannot change your own role or status. Ask another admin — the " +
      "point is that nobody quietly promotes themselves."
    );
  }
  if (status === 403) {
    return "Only an admin can do that.";
  }
  if (status === 500) {
    return (
      `The Console answered 500. If this console is newly deployed, the ` +
      `operator tables may not exist yet — migration 009 (H-64). ${detail}`
    );
  }
  return detail || `The Console answered ${status}.`;
}

export default function OperatorAdmin({ rows }: { rows: OperatorRow[] }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null,
  );
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<string>("viewer");

  async function send(
    key: string,
    url: string,
    init: RequestInit,
    okText: string,
  ) {
    setBusy(key);
    setMessage(null);
    try {
      const res = await fetch(url, {
        headers: { "content-type": "application/json" },
        ...init,
      });
      const body = (await res.json().catch(() => ({}))) as {
        detail?: string;
        error?: string;
      };
      if (res.ok) {
        setMessage({ ok: true, text: okText });
        // A full reload rather than local state: the guards can change more than
        // the row that was touched (deactivating somebody drops their sessions),
        // and a table that showed a stale count would be lying about access.
        window.location.reload();
      } else {
        // The BFF gate speaks in `error`, the Console in `detail` — read
        // both, or a signed-out 401 degrades to a generic status line.
        setMessage({
          ok: false,
          text: explain(res.status, body.detail ?? body.error ?? ""),
        });
      }
    } catch {
      setMessage({
        ok: false,
        text: "The Console did not answer. Nothing changed — check the network and try again.",
      });
    } finally {
      setBusy(null);
    }
  }

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    return send(
      "add",
      "/api/operator/operators",
      { method: "POST", body: JSON.stringify({ email: email.trim(), role }) },
      `Added ${email.trim()}.`,
    );
  };

  return (
    <>
      {message && (
        <div className={`result ${message.ok ? "" : "err"}`}>
          {message.text}
        </div>
      )}

      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Status</th>
              <th>Signed in</th>
              <th>Live sessions</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="empty">
                  Nobody is registered yet. The first person to sign in with
                  the bootstrap address becomes an admin.
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.email}</td>
                <td>
                  <select
                    value={r.role}
                    aria-label={`Role for ${r.email}`}
                    disabled={busy !== null || r.status !== "active"}
                    onChange={(e) =>
                      send(
                        r.id,
                        `/api/operator/operators/${r.id}`,
                        {
                          method: "PATCH",
                          body: JSON.stringify({ role: e.target.value }),
                        },
                        `${r.email} is now ${e.target.value}.`,
                      )
                    }
                  >
                    {ROLES.map((value) => (
                      <option key={value} value={value} title={ROLE_HELP[value]}>
                        {value}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <span className={`pill ${r.status}`}>{r.status}</span>
                </td>
                <td>{r.has_signed_in ? "yes" : <span className="muted">never</span>}</td>
                <td>{r.live_sessions}</td>
                <td>
                  {r.status === "active" ? (
                    <button
                      type="button"
                      className="linklike"
                      disabled={busy !== null}
                      onClick={() =>
                        send(
                          r.id,
                          `/api/operator/operators/${r.id}`,
                          { method: "DELETE" },
                          `${r.email} is deactivated and signed out.`,
                        )
                      }
                    >
                      Deactivate
                    </button>
                  ) : (
                    <span className="muted small">
                      {/* D63 — the row is sealed, never deleted, so the audit
                          trail that named this person stays readable. */}
                      sealed
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <form className="panel" onSubmit={add}>
        <h2>Add an operator</h2>
        <p className="muted small">
          They must also be able to sign in through our Microsoft directory.
          Adding a row here does not create an account.
        </p>
        <label htmlFor="new-email">Work email</label>
        <input
          id="new-email"
          type="email"
          value={email}
          required
          onChange={(e) => setEmail(e.target.value)}
        />
        <label htmlFor="new-role">Role</label>
        <select
          id="new-role"
          value={role}
          onChange={(e) => setRole(e.target.value)}
        >
          {ROLES.map((value) => (
            <option key={value} value={value}>
              {value} — {ROLE_HELP[value]}
            </option>
          ))}
        </select>
        <button type="submit" disabled={busy !== null || email.trim() === ""}>
          {busy === "add" ? "Adding…" : "Add operator"}
        </button>
      </form>
    </>
  );
}
