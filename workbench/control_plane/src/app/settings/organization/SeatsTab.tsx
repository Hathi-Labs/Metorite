"use client";

/**
 * Organisation → Seat assignments.
 *
 * Owning spec: `project-docs/specs/launch_surface.md` §6.2 · LS-7. D49.
 * The plumbing is `customer_console.md` §6 **CP-2h slice 1 — D-SEAT-4**.
 *
 * One row per member of the organization, each **Seated** or **Unassigned**,
 * with Assign / Release. Above them, the seat counts — `purchased`,
 * `assigned`, `available` — read verbatim from the Console's seat grid.
 *
 * ## Three rules this surface exists to keep
 *
 * 1. **Nothing here computes a seat count.** Every number is the Console's,
 *    computed once by `seat_counts` (D32.5) and clamped there. After a write
 *    the surface REFETCHES rather than adjusting a number locally, because a
 *    locally-adjusted count is a second implementation that drifts the first
 *    time a write partially succeeds.
 * 2. **Unassigned is a state, not an absence.** The roster comes from the
 *    GATEWAY (every member), and seat state from the Console (only members who
 *    have held one). Releasing somebody must leave them on this screen with an
 *    enabled Assign — see `lib/seatRoster.ts` for the join and why it is that
 *    way round.
 * 3. **The refusals are the Console's, relayed — and a refusal is not an
 *    outage.** A cap 409 on a WRITE shows the Console's own `buy_more`
 *    sentence. On the READ, `interpretOverviewRead` (in `lib/seatRoster.ts`,
 *    where a test can reach it — inline here it was not, which is how it stayed
 *    wrong) maps the status onto five states: **403 is the founder-only state**,
 *    calm and non-red, because the Console ANSWERED and said this account is not
 *    an active `owner|admin` in its registry — which today is every admin except
 *    the founder, no Console code path ever writing `role='admin'` (§6 CP-2f);
 *    **409 is the multi-org state**, its own sentence, fixed by CP-2h slice 2;
 *    503 alone means this deployment is unwired; and only an unrecognised status
 *    is an error. This surface still pre-judges nothing — the authorization is
 *    `_admin_scheme_context`'s and the capacity check is `decide_assignment`'s;
 *    it just stops relabelling their verdicts as an incident.
 *
 * ## Where the data comes from — CP-2h slice 1 (2026-08-24)
 *
 * The READ is `/api/org/seats`: browser → Next hop → **gateway** → Console's
 * deployment-key `seat_admin` door. It used to come from
 * `/api/billing/{seats,members}`, which present a per-org
 * `CUSTOMER_CONSOLE_ORG_KEY` — and on a shared multi-tenant deployment there is
 * no single correct org key, so the variable is unset and the tab read
 * "not configured for this deployment" **forever**. That was structural, not a
 * missing flag. One read now returns both halves, so the counts and the roster
 * are one consistent snapshot rather than two races.
 *
 * The WRITES stay on `/api/billing/seats/{assign,release}` — which despite the
 * path are **already** the gateway hops, holding no Console credential and
 * reaching the same deployment-key door. A duplicate pair under `/api/org/`
 * shipped with this slice and was deleted in the same review: the seam is the
 * gateway route, and a second BFF file in front of it is a second way to say one
 * thing (CLAUDE.md §5). Retiring the `/api/billing/` NAME for these two is a
 * later slice's rename, not a reason to keep two implementations.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useAccess } from "@/components/AccessProvider";
import { hasCapability } from "@/lib/access";
import type { Member } from "@/app/settings/members/types";
import {
  assignBody,
  buyMoreMessage,
  interpretSeatAction,
  releaseBody,
  type Member as BillingMember,
} from "@/app/settings/billing/lib/manage";
import { SEAT_COUNTS, type SeatPlan } from "@/app/settings/billing/lib/seats";

import {
  buildSeatRows,
  canOfferSeat,
  interpretOverviewRead,
  isSeated,
  readSeatOverview,
  tally,
  type SeatOverviewPayload,
  type SeatPlaneRead,
  type SeatRow,
} from "./lib/seatRoster";

/**
 * How the seat plane answered, plus the one state that is not an answer.
 *
 * Distinguished for `launch_surface.md` §8.2's reason, one layer along: an
 * unreachable Console and a Console reporting nobody-has-a-seat look identical
 * in an empty list, and only one of them means the admin should stop and read.
 * The five answered states are `SeatPlaneRead`'s and are decided in
 * `lib/seatRoster.ts`; `loading` is this component's own and means we have not
 * asked yet.
 */
type PlaneState = SeatPlaneRead | "loading";

export default function SeatsTab({
  members,
  loading,
  onChanged,
}: {
  /** The gateway roster the parent already loaded. One read, two tabs. */
  members: Member[];
  loading: boolean;
  /** Refetch the parent's roster after a write. */
  onChanged: () => Promise<void> | void;
}) {
  const { access } = useAccess();
  const [plans, setPlans] = useState<SeatPlan[] | null>(null);
  const [billing, setBilling] = useState<BillingMember[] | null>(null);
  const [plane, setPlane] = useState<PlaneState>("loading");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [problem, setProblem] = useState("");

  /**
   * Whether to draw the write controls at all.
   *
   * `billing:purchase` on top of the page's admin gate, mirroring
   * `/settings/billing`. Neither is the boundary: the real authorization is
   * Console-side, and hiding a control the caller cannot use is a courtesy —
   * offering one whose only outcome is a 403 is worse.
   */
  const canManage = hasCapability(access, "billing:purchase");

  const load = useCallback(async () => {
    setPlane("loading");
    try {
      // ONE read for both halves (CP-2h slice 1): the Console composes the seat
      // grid and the roster in a single transaction, so the counts and the rows
      // cannot disagree the way two independent fetches could.
      const r = await fetch("/api/org/seats", { cache: "no-store" });
      const payload = await r.json().catch(() => null);
      // Status → state in ONE place, `lib/seatRoster.ts`, because "which
      // non-2xx is an outage" is a judgement and a judgement inline in a
      // callback is a judgement no test can reach. 503 is this deployment's
      // missing configuration; 403 and 409 are the Console ANSWERING; only the
      // remainder is an incident.
      const state = interpretOverviewRead(r.status, payload);
      if (state !== "ready") {
        setPlans(null);
        setBilling(null);
        setPlane(state);
        return;
      }
      const overview = readSeatOverview(payload as SeatOverviewPayload | null);
      setPlans(overview.plans);
      setBilling(overview.members);
      setPlane("ready");
    } catch {
      setPlans(null);
      setBilling(null);
      setPlane("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = useMemo(
    () => buildSeatRows(members, billing),
    [members, billing],
  );
  const counts = useMemo(() => tally(rows), [rows]);

  const grid: SeatPlan[] = plans ?? [];
  /**
   * The plan a seat is assigned ON.
   *
   * Under D49 there is exactly one sellable plan, so "which plan" is not a
   * question to put to an admin — the picker the multi-package design needed
   * would now be a control with one option. It is read from the seats payload
   * rather than hardcoded, so the surface follows the catalog rather than
   * needing an edit if a second plan ever returns.
   */
  const plan = grid[0]?.plan_slug ?? "core";

  const act = useCallback(
    async (row: SeatRow, kind: "assign" | "release") => {
      setBusy(row.email);
      setMessage("");
      setProblem("");
      try {
        const body =
          kind === "assign"
            ? assignBody(row.email, plan)
            : releaseBody(row.email, row.seats[0] ?? plan);
        // The EXISTING gateway-backed write pair — no Console credential, same
        // deployment-key door as the read. The `/api/billing/` prefix is a name
        // this slice deliberately did not duplicate under `/api/org/`.
        const res = await fetch(`/api/billing/seats/${kind}`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        });
        const payload = await res.json().catch(() => null);
        const verdict = interpretSeatAction(res.status, payload);
        if (verdict.kind === "cap") {
          // The Console's own sentence, from the Console's own numbers.
          setProblem(buyMoreMessage(verdict.buyMore));
        } else if (verdict.kind === "error") {
          setProblem(verdict.message);
        } else {
          setMessage(
            kind === "assign"
              ? `${row.displayName} now holds a seat.`
              : `${row.displayName}'s seat is free. They stay on the roster and can be reassigned.`,
          );
        }
      } catch {
        setProblem("Could not reach seat management. Nothing was changed.");
      } finally {
        setBusy("");
        // Refetch whatever the outcome — including after a failure, because a
        // write that failed halfway is exactly when a stale count misleads.
        await load();
        await onChanged();
      }
    },
    [load, onChanged, plan],
  );

  if (plane === "unconfigured") {
    return (
      <Notice
        icon="PlugZap"
        title="Seat management is not configured for this deployment"
        body="Seats are held by the Customer Console, which this deployment cannot reach yet. Members and roles are unaffected — everyone on the Members tab still signs in normally."
      />
    );
  }

  // 403 — the Console answered. It is not an outage and it must not look like
  // one: the registry role it gates on is only ever `owner` for the founder (no
  // Console door writes `role='admin'`), so this is the state MOST admins see.
  if (plane === "restricted") {
    return (
      <Notice
        icon="Info"
        title="Seats are managed by this organization's founder"
        body="Seat management is limited to the organization's founder for now — you can manage members on the Members tab. Nothing is wrong: the seat plane answered, it just does not recognise this account as the one that holds the subscription."
      />
    );
  }

  // 409 — the acting email is a member of more than one organization on this
  // deployment and the seat plane will not guess between them. Slice 2 threads
  // the signed-in organization through, which removes this state entirely.
  if (plane === "ambiguous") {
    return (
      <Notice
        icon="Layers"
        title="This email belongs to more than one organization here"
        body="Seat management cannot yet tell which organization's seats to show for an account that is a member of several on this deployment, so it is showing none rather than guessing. Members and roles are unaffected — the Members tab works normally."
      />
    );
  }

  if (plane === "error") {
    return (
      <Notice
        icon="AlertTriangle"
        tone="warning"
        title="Could not read seats"
        body="The seat plane did not answer. Nothing has changed; the Members tab is unaffected."
        action={
          <Button size="sm" variant="secondary" onClick={() => void load()}>
            Try again
          </Button>
        }
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* ── The counts, verbatim from the Console's seat grid ─────────────── */}
      <div className="rounded-xl border border-border bg-card/40 p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">Seats</h2>
          <span className="text-[11px] text-muted-foreground">
            {counts.seated} of {counts.total} people seated ·{" "}
            {counts.unassigned} unassigned
          </span>
        </div>
        {grid.length === 0 ? (
          <p className="mt-2 text-xs text-muted-foreground">
            No seats have been bought yet. Assigning one will report how many
            more you need.
          </p>
        ) : (
          <div className="mt-3 flex flex-wrap gap-6">
            {grid.map((p) => (
              <div key={p.plan_slug} className="flex gap-6">
                {SEAT_COUNTS.map((c) => (
                  <div key={c.key}>
                    <div className="text-lg font-semibold text-foreground">
                      {p[c.key]}
                    </div>
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      {c.label}
                    </div>
                  </div>
                ))}
                {/* The server's FLAG, never derived from `available === 0` —
                    a plan can be oversubscribed with available clamped to zero,
                    and can have zero available without being oversubscribed. */}
                {p.oversubscribed ? (
                  <div className="self-center rounded-md border border-warning/40 bg-warning/10 px-2 py-1 text-[10px] text-warning">
                    More seats are assigned than were bought
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>

      {message ? (
        <div className="flex items-start gap-2 rounded-lg border border-border bg-secondary px-3 py-2 text-xs text-muted-foreground">
          <span className="flex-1">{message}</span>
          <button onClick={() => setMessage("")} aria-label="Dismiss">
            <Icon name="X" size={13} />
          </button>
        </div>
      ) : null}

      {problem ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <span className="flex-1">{problem}</span>
          <button onClick={() => setProblem("")} aria-label="Dismiss">
            <Icon name="X" size={13} />
          </button>
        </div>
      ) : null}

      {/* Reaching this list at all means the Console admitted the caller as an
          `owner|admin` of the org, so the old "you can look but not touch"
          reader — a plain member — cannot get here any more; they are the 403
          above. What remains is the genuine, narrow case: the two gates live on
          two different planes (registry role vs tenant capability) and can
          disagree, so the copy names both instead of implying a read-only
          viewer role that does not exist. */}
      {!canManage ? (
        <p className="text-xs text-muted-foreground">
          Reading seats needs your registry role and changing them needs the{" "}
          <code className="font-mono">billing:purchase</code> capability — two
          different planes. This account has the first and not the second, so
          the roster is visible and Assign / Release are not.
        </p>
      ) : null}

      {/* ── One row per member of the organization ────────────────────────── */}
      {loading || plane === "loading" ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Icon name="Loader2" size={14} className="animate-spin" /> Loading
          seats…
        </div>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Nobody is in the organization yet. Invite someone from the Members
          tab.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {rows.map((row) => (
            <SeatRowCard
              key={row.email}
              row={row}
              canManage={canManage}
              busy={busy === row.email}
              onAssign={() => void act(row, "assign")}
              onRelease={() => void act(row, "release")}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function SeatRowCard({
  row,
  canManage,
  busy,
  onAssign,
  onRelease,
}: {
  row: SeatRow;
  canManage: boolean;
  busy: boolean;
  onAssign: () => void;
  onRelease: () => void;
}) {
  const seated = isSeated(row);
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-card/40 px-3 py-2.5">
      <span
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
          seated ? "bg-primary/10 text-primary" : "bg-secondary text-muted-foreground"
        }`}
      >
        <Icon name={seated ? "UserCheck" : "UserPlus"} size={15} />
      </span>

      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-foreground">
          {row.displayName}
        </div>
        <div className="truncate text-[11px] text-muted-foreground">
          {row.email}
          {row.status !== "active" ? ` · ${row.status}` : ""}
        </div>
      </div>

      <span
        className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${
          seated
            ? "border-primary/40 text-primary"
            : "border-border text-muted-foreground"
        }`}
        title={seated ? row.seats.join(", ") : undefined}
      >
        {seated ? "Seated" : "Unassigned"}
      </span>

      {canManage && canOfferSeat(row) ? (
        <Button
          size="sm"
          variant={seated ? "secondary" : "primary"}
          disabled={busy}
          onClick={seated ? onRelease : onAssign}
        >
          {busy ? "…" : seated ? "Release" : "Assign seat"}
        </Button>
      ) : null}
    </div>
  );
}

function Notice({
  icon,
  title,
  body,
  action,
  tone = "muted",
}: {
  icon: string;
  title: string;
  body: string;
  action?: React.ReactNode;
  tone?: "muted" | "warning";
}) {
  return (
    <div
      className={`flex items-start gap-3 rounded-xl border p-4 ${
        tone === "warning"
          ? "border-warning/30 bg-warning/10"
          : "border-border bg-card/40"
      }`}
    >
      <Icon
        name={icon}
        size={16}
        className={`mt-0.5 shrink-0 ${
          tone === "warning" ? "text-warning" : "text-muted-foreground"
        }`}
      />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-foreground">{title}</div>
        <p className="mt-1 text-xs text-muted-foreground">{body}</p>
        {action ? <div className="mt-3">{action}</div> : null}
      </div>
    </div>
  );
}
