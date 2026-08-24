"use client";

/**
 * Settings → Members — the organization roster, and the queue of people
 * trying to get into it.
 *
 * Spec: project-docs/specs/org_access_control.md §6;
 *       project-docs/specs/colleague_onboarding.md §6 (the Requests tab).
 *
 * Invite, suspend, change roles, and drill into one person's access. The
 * per-person editor is where the interesting work happens (./[email]); this
 * page is the list that gets you there.
 *
 * The **Requests** tab is the other direction. Until it existed this screen
 * only showed people an admin had already thought of; somebody arriving at the
 * front door produced a log line nobody read. The badge is the whole point —
 * the owner is supposed to learn that a colleague is locked out without being
 * told.
 *
 * **The roster knows who is reading it** (N7). It used to not, so it drew
 * **Suspend** on the viewer's own row like any other — the one click that
 * takes an owner out of the surface they would need to undo it. `rowActions`
 * (./selfGuard) decides what a row may offer; the refusal itself is the
 * gateway's, and this is only a courtesy (`lib/access.ts:126-129`).
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import FilterPills from "@/components/FilterPills";
import Tabs from "@/components/Tabs";
import { useAccess } from "@/components/AccessProvider";
import { rowActions } from "./selfGuard";
import { purgeConfirmed } from "./confirmPurge";
import type { AccessRequest, Member, PurgeResult, Role } from "./types";

const STATUS_STYLES: Record<Member["status"], string> = {
  active: "text-success",
  invited: "text-warning",
  suspended: "text-destructive",
  removed: "text-muted-foreground",
};

/**
 * What each status means to the person on the other end of it.
 *
 * `invited` is the one that matters: `is_active` is `status === "active"`
 * exactly, so an invited colleague sees the identical dead-end screen as a
 * total stranger while this list renders them as though they were live. That
 * ambiguity is the 2026-08-04 incident (colleague_onboarding.md §6) — the row
 * has to say so.
 */
const STATUS_LABELS: Record<Member["status"], string> = {
  active: "active",
  invited: "invited — never signed in",
  suspended: "suspended",
  removed: "removed",
};

/**
 * Human wording for the gateway's purge count keys (`members._PURGE_DELETES` /
 * `_PURGE_KEEPS`).
 *
 * Unlabelled keys fall back to their own name with the underscores knocked
 * out, so a table added on the gateway shows up here — badly worded, but
 * visible — instead of disappearing from the report. Silence is the failure
 * mode that matters for an irreversible action.
 */
const PURGE_LABELS: Record<string, string> = {
  member_record: "member record",
  role_grants: "role grant",
  permission_overrides: "permission override",
  group_memberships: "team membership",
  room_participations: "room membership",
  app_grants: "app grant",
  app_tool_grants: "remembered tool approval",
  email_accounts: "connected mailbox",
  whatsapp_accounts: "connected WhatsApp number",
  task_accounts: "connected task workspace",
  // The SYNCED half of the GTD store, which the connected task workspace
  // cascades away. Worded as "synced" on both sides so the deleted and kept
  // task counts in one sentence do not read as a contradiction.
  synced_tasks: "synced task",
  synced_projects: "synced project",
  private_chat_sessions: "private chat session",
  sign_in_requests: "sign-in request",
  audit_entries: "app audit entry",
  audit_events: "audit event",
  agent_runs: "agent run trace",
  shared_rooms: "shared room",
  apps: "app",
  workflows: "workflow",
  // Kept: the LOCAL half. Named against `synced_*` above rather than left as
  // a bare "task", so "2 synced tasks removed; 3 local tasks kept" reads as
  // one coherent sentence instead of a contradiction.
  tasks: "local task",
  projects: "local project",
  meetings: "meeting",
};

function purgeLabel(key: string, n: number): string {
  const base = PURGE_LABELS[key] ?? key.replace(/_/g, " ");
  return `${n} ${base}${n === 1 ? "" : "s"}`;
}

/** `{a: 2, b: 0}` → `["2 as"]` — zero rows are not news. */
function purgeParts(counts: Record<string, number>): string[] {
  return Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([key, n]) => purgeLabel(key, n));
}

/**
 * One line an admin can read after the fact: what was destroyed, what stayed.
 *
 * Both halves, always — "3 sessions and 1 mailbox deleted" on its own reads
 * like the whole database went.
 */
function summarisePurge(result: PurgeResult): string {
  const gone = purgeParts(result.deleted);
  const stayed = purgeParts(result.kept);
  return (
    `${result.email} deleted permanently — ` +
    `${gone.length ? gone.join(", ") : "nothing"} removed; ` +
    `${stayed.length ? stayed.join(", ") : "nothing"} kept.`
  );
}

/** Absolute date + time; a knock at 16:21 yesterday is a fact, not "a while ago". */
function when(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function MembersPage() {
  const { access, refresh: refreshAccess } = useAccess();
  const [members, setMembers] = useState<Member[]>([]);
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  /**
   * The queue's own failure, kept apart from `error` so it can be shown
   * WITHOUT taking the roster down — and so it cannot be silent.
   *
   * A failed `/members/requests` used to fall back to `[]`, which renders as
   * "Nobody is waiting." That is indistinguishable from the working state, and
   * it is the exact failure this tab exists to fix: somebody is locked out and
   * the screen says everything is fine. If migration 143 has not been applied
   * the list route 500s, and nothing else on the page would notice.
   */
  const [queueError, setQueueError] = useState("");
  /**
   * A decision that SUCCEEDED but did not do what was literally asked —
   * today only "they were already an active member, so their roles were left
   * alone". Separate from `error` because nothing went wrong, and shown
   * because a 200 that quietly ignored the role picker is otherwise
   * indistinguishable from one that honoured it.
   */
  const [notice, setNotice] = useState("");
  const [tab, setTab] = useState("members");
  const [filter, setFilter] = useState("all");
  const [inviting, setInviting] = useState(false);
  /**
   * The person an off-boarding is being confirmed for. Removal drops every
   * role assignment and is not a toggle — Activate does not undo it — so it
   * goes through a step that names them (N7 done-when 6).
   */
  const [removing, setRemoving] = useState<Member | null>(null);
  /**
   * The person a **permanent deletion** is being confirmed for (N8). Separate
   * state from `removing` on purpose: two destructive actions sharing one
   * "which member" slot is one refactor away from the dialog and the fetch
   * disagreeing about which one was clicked.
   */
  const [purging, setPurging] = useState<Member | null>(null);

  /**
   * Who is reading this screen. `/auth/me` returns it and the shell already
   * has it; the roster simply never asked, which is why it offered every
   * viewer the one action that locks them out of it.
   */
  const viewerEmail = access.email;

  const load = useCallback(async () => {
    try {
      // The fetch goes first so no setState runs synchronously in the mount
      // effect — clearing the error afterwards is equivalent and avoids a
      // cascading render.
      const [m, r, q] = await Promise.all([
        fetch("/api/admin/members", { cache: "no-store" }),
        fetch("/api/admin/roles", { cache: "no-store" }),
        fetch("/api/admin/members/requests", { cache: "no-store" }),
      ]);
      setError("");
      if (!m.ok) {
        const body = await m.json().catch(() => ({}));
        throw new Error(body.detail ?? `Failed to load members (${m.status})`);
      }
      setMembers(await m.json());
      if (r.ok) setRoles(await r.json());
      // A deployment whose migration has not run yet still shows the roster —
      // the queue is additive and must never take the page down with it — but
      // it says so out loud rather than rendering an empty queue as calm.
      if (q.ok) {
        setRequests(await q.json());
        setQueueError("");
      } else {
        const body = await q.json().catch(() => ({}));
        setRequests([]);
        setQueueError(
          body.detail ??
            `The sign-in queue could not be loaded (${q.status}). Somebody may be locked out and not shown here.`
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load members.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Wrapped rather than called directly: the effect must not reach a
    // setState synchronously (react-hooks/set-state-in-effect), and `load` is
    // also invoked from refresh buttons, so it stays a useCallback.
    const run = async () => {
      await load();
    };
    void run();
  }, [load]);

  const counts = useMemo(
    () => ({
      all: members.length,
      active: members.filter((m) => m.status === "active").length,
      invited: members.filter((m) => m.status === "invited").length,
      suspended: members.filter((m) => m.status === "suspended").length,
    }),
    [members]
  );

  const shown = useMemo(
    () => (filter === "all" ? members : members.filter((m) => m.status === filter)),
    [members, filter]
  );

  const setStatus = async (email: string, status: Member["status"]) => {
    setError("");
    const res = await fetch(`/api/admin/members/${encodeURIComponent(email)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail ?? "Could not update this member.");
      return;
    }
    await load();
    // The admin may have just changed their own access.
    await refreshAccess();
  };

  /**
   * Off-board somebody: `DELETE /admin/members/{email}` — status `removed`,
   * every role assignment dropped. The route has shipped since the roster
   * existed and had no caller in the product, so the only off-boarding this
   * screen could perform was Suspend.
   *
   * Two rules, both from the Requests tab's own history:
   *
   * 1. **The refusal is shown.** Hiding the control on the viewer's own row is
   *    a courtesy; the gateway's guard is the boundary, and a 409 that never
   *    reaches the screen looks exactly like a click that did nothing.
   * 2. **No early return.** A refusal is almost always ABOUT the row that was
   *    clicked — self, last owner, already off-boarded — so the row on screen
   *    is the stale thing. `load()` runs on every response.
   */
  const removeMember = async (email: string) => {
    setError("");
    setNotice("");
    const res = await fetch(`/api/admin/members/${encodeURIComponent(email)}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail ?? "Could not remove this member.");
    }
    await load();
    await refreshAccess();
  };

  /**
   * Delete somebody permanently: `DELETE /admin/members/{email}/purge` (N8).
   *
   * The identity, every credential and access grant, and their private
   * sessions are destroyed in one transaction; what they authored and the
   * audit trail are kept. The gateway answers with a count per table, and
   * **those counts are shown** — a purge that reports only "ok" leaves the
   * admin no way to tell one that removed a live OAuth token from one that
   * matched nothing.
   *
   * Same two rules as `removeMember`: the refusal is surfaced (the guard is
   * the server's), and there is no early return, because a refusal is about
   * the row that was clicked and that row is the stale one.
   */
  const purgeMember = async (email: string) => {
    setError("");
    setNotice("");
    const res = await fetch(
      `/api/admin/members/${encodeURIComponent(email)}/purge`,
      { method: "DELETE" }
    );
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(body.detail ?? "Could not delete this member.");
    } else {
      setNotice(summarisePurge(body as PurgeResult));
    }
    await load();
    await refreshAccess();
  };

  /**
   * Approve (provisions + activates in one action) or deny a knock.
   *
   * Two rules here, both about the queue never showing a state the server has
   * already corrected:
   *
   * 1. **No early return.** A refusal is almost always ABOUT the row that was
   *    clicked — already decided, suspended, off-boarded, or decided by
   *    another admin mid-flight — so the row on screen is exactly the thing
   *    that is stale. `load()` runs on every response, 200 or 409.
   * 2. **A 200 can still carry news.** Approving over somebody who is already
   *    an active member deliberately leaves their roles alone, so the role
   *    picker's selection was ignored. The gateway says so in `detail`; if the
   *    page swallowed it, that 200 would look identical to one that applied
   *    the choice.
   */
  const decide = async (
    email: string,
    decision: "approve" | "deny",
    roleSlug?: string
  ) => {
    setError("");
    setNotice("");
    const res = await fetch(
      `/api/admin/members/requests/${encodeURIComponent(email)}/${decision}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body:
          decision === "approve"
            ? JSON.stringify({ roles: roleSlug ? [roleSlug] : [] })
            : undefined,
      }
    );
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(body.detail ?? `Could not ${decision} this request.`);
    } else if (body.detail) {
      setNotice(body.detail);
    }
    // Decided rows leave the tab; an approval also adds a member; and a
    // refusal means this row is not what the tab thinks it is.
    await load();
  };

  if (!access.is_admin && !loading) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="max-w-sm rounded-xl border border-border bg-card p-8 text-center">
          <Icon name="ShieldOff" size={20} className="mx-auto mb-3 text-muted-foreground" />
          <h1 className="text-base font-semibold text-foreground">
            Members is admin-only
          </h1>
          <p className="mt-2 text-xs text-muted-foreground">
            You need the <code className="font-mono">admin:members:read</code>{" "}
            permission to manage the organization roster.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-3 shrink-0 sm:px-6 sm:py-4">
        <div>
          <h1 className="text-base font-bold text-foreground sm:text-lg">Members</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {access.organization?.display_name ?? "Organization"} ·{" "}
            {counts.active} active of {counts.all}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void load()}
            className="rounded-lg border border-border p-2 text-muted-foreground tech-transition hover:bg-secondary"
            title="Refresh"
          >
            <Icon name="RefreshCw" size={16} />
          </button>
          <Link
            href="/settings/groups"
            className="rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground tech-transition hover:border-primary/30 hover:text-foreground"
          >
            Teams
          </Link>
          <Link
            href="/settings/roles"
            className="rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground tech-transition hover:border-primary/30 hover:text-foreground"
          >
            Roles
          </Link>
          <Button size="lg" layout="flex items-center" onClick={() => setInviting(true)}>
            <Icon name="Plus" size={15} />
            Invite
          </Button>
        </div>
      </div>

      <Tabs
        variant="underline"
        activeTab={tab}
        onTabChange={setTab}
        tabs={[
          { id: "members", label: "Members", icon: "Users", count: counts.all },
          {
            id: "requests",
            label: "Requests",
            icon: "DoorOpen",
            // Only badged when there is something to answer — a permanent "0"
            // is the kind of chrome people stop reading. When the queue failed
            // to load there is no honest count, so the tooltip says so rather
            // than letting an absent badge read as "nobody is waiting".
            count: queueError ? undefined : requests.length || undefined,
            note: queueError || "People who signed in and found no account",
          },
        ]}
      />

      {tab === "members" && (
        <div className="border-b border-border px-4 py-2.5 sm:px-6">
          <FilterPills
            items={[
              { id: "all", label: "All", count: counts.all },
              { id: "active", label: "Active", count: counts.active },
              { id: "invited", label: "Invited", count: counts.invited },
              { id: "suspended", label: "Suspended", count: counts.suspended },
            ]}
            activeId={filter}
            onChange={setFilter}
          />
        </div>
      )}

      {error && (
        <div className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive sm:mx-6">
          <span className="flex-1">{error}</span>
          <button onClick={() => setError("")} aria-label="Dismiss">
            <Icon name="X" size={13} />
          </button>
        </div>
      )}

      {notice && (
        <div className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-border bg-secondary px-3 py-2 text-xs text-muted-foreground sm:mx-6">
          <span className="flex-1">{notice}</span>
          <button onClick={() => setNotice("")} aria-label="Dismiss">
            <Icon name="X" size={13} />
          </button>
        </div>
      )}

      {/* Non-blocking, and deliberately NOT dismissible: the roster below is
          fine, but the queue is not, and an owner who dismisses this goes back
          to a screen that says nobody is locked out. It clears when a reload
          succeeds. */}
      {queueError && (
        <div className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning sm:mx-6">
          <Icon name="AlertTriangle" size={13} className="mt-0.5 shrink-0" />
          <span className="flex-1">
            {queueError} The roster below is unaffected.
          </span>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Icon name="Loader2" size={14} className="animate-spin" /> Loading members…
          </div>
        ) : tab === "requests" ? (
          <RequestsTab
            requests={requests}
            roles={roles}
            onDecide={decide}
            failed={Boolean(queueError)}
          />
        ) : shown.length === 0 ? (
          <p className="text-xs text-muted-foreground">No members here yet.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {shown.map((m) => (
              <MemberRow
                key={m.email}
                member={m}
                roles={roles}
                viewerEmail={viewerEmail}
                setStatus={setStatus}
                onRemove={setRemoving}
                onPurge={setPurging}
              />
            ))}
          </div>
        )}
      </div>

      {inviting && (
        <InviteDialog
          roles={roles}
          onClose={() => setInviting(false)}
          onDone={async () => {
            setInviting(false);
            await load();
          }}
        />
      )}

      {removing && (
        <RemoveDialog
          member={removing}
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            const target = removing;
            setRemoving(null);
            await removeMember(target.email);
          }}
        />
      )}

      {purging && (
        <PurgeDialog
          member={purging}
          onClose={() => setPurging(null)}
          onConfirm={async () => {
            const target = purging;
            setPurging(null);
            await purgeMember(target.email);
          }}
        />
      )}
    </div>
  );
}

/**
 * One person on the roster.
 *
 * It is its own component so the destructive controls have exactly one place
 * to live, and so `rowActions` is consulted once per row rather than being
 * re-derived beside each button. The viewer's own row shows **This is you**
 * where Suspend and Remove would be: the server refuses either anyway
 * (`_common.assert_not_self_lockout`), and offering a button whose only
 * outcome is a 409 is worse than not offering it.
 */
function MemberRow({
  member,
  roles,
  viewerEmail,
  setStatus,
  onRemove,
  onPurge,
}: {
  member: Member;
  roles: Role[];
  /** The signed-in member's address, from `/auth/me`. */
  viewerEmail: string;
  setStatus: (email: string, status: Member["status"]) => Promise<void>;
  /** Opens the confirmation; the DELETE is fired from there, never here. */
  onRemove: (member: Member) => void;
  /** Likewise for the permanent one — this row never issues either request. */
  onPurge: (member: Member) => void;
}) {
  const actions = rowActions(viewerEmail, member);

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card p-3 sm:p-4">
      <div className="min-w-0 flex-1">
        <Link
          href={`/settings/members/${encodeURIComponent(member.email)}`}
          className="truncate text-sm font-medium text-foreground hover:text-primary tech-transition"
        >
          {member.display_name || member.email}
        </Link>
        <div className="truncate text-[11px] text-muted-foreground">
          {member.email}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {member.roles.length === 0 ? (
          <span className="rounded-md bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
            no role
          </span>
        ) : (
          member.roles.map((r) => (
            <span
              key={r}
              className="rounded-md bg-secondary px-2 py-0.5 text-[10px] text-foreground"
            >
              {roles.find((x) => x.slug === r)?.display_name ?? r}
            </span>
          ))
        )}
      </div>

      <div className="flex items-center gap-1.5">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            member.status === "active"
              ? "bg-success"
              : member.status === "invited"
                ? "bg-warning"
                : "bg-muted"
          }`}
        />
        <span
          className={`text-[11px] ${STATUS_STYLES[member.status]}`}
          title={
            member.status === "invited"
              ? "Their row exists but is not active — they still see the same dead-end screen as a stranger. Activate them."
              : undefined
          }
        >
          {STATUS_LABELS[member.status]}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <Link
          href={`/settings/members/${encodeURIComponent(member.email)}`}
          className="rounded-lg border border-border px-2.5 py-1.5 text-[11px] text-muted-foreground tech-transition hover:border-primary/30 hover:text-foreground"
        >
          Manage access
        </Link>
        {actions.isSelf && (
          <span
            className="rounded-lg border border-dashed border-border px-2.5 py-1.5 text-[11px] text-muted-foreground"
            title="You cannot suspend or remove your own access. Another owner or admin can."
          >
            This is you
          </span>
        )}
        {actions.canActivate && (
          <Button variant="secondary" size="none" layout="" onClick={() => void setStatus(member.email, "active")} className="px-2.5 py-1.5 text-[11px]">
            Activate
          </Button>
        )}
        {actions.canSuspend && (
          <Button variant="destructive" size="none" layout="" onClick={() => void setStatus(member.email, "suspended")} className="px-2.5 py-1.5 text-[11px]">
            Suspend
          </Button>
        )}
        {actions.canRemove && (
          <button
            onClick={() => onRemove(member)}
            className="flex items-center gap-1 rounded-lg border border-destructive/30 px-2.5 py-1.5 text-[11px] text-destructive tech-transition hover:bg-destructive/10"
          >
            <Icon name="UserMinus" size={12} />
            Remove
          </button>
        )}
        {/* Visually distinct from Remove on purpose: Remove is an outline, this
            is filled. They sit next to each other and only one of them can be
            undone. */}
        {actions.canPurge && (
          <button
            onClick={() => onPurge(member)}
            title="Delete this person and every credential and access grant they hold. Their work and the audit trail are kept."
            className="flex items-center gap-1 rounded-lg bg-destructive px-2.5 py-1.5 text-[11px] font-medium text-destructive-foreground tech-transition hover:opacity-90"
          >
            <Icon name="Trash2" size={12} />
            Delete permanently
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Confirm an off-boarding, by name.
 *
 * Remove is not the loud version of Suspend: it drops every role assignment
 * and the way back is a fresh invite plus an activation, not a click on this
 * screen. Suspend is the reversible one, and the copy says so — an admin who
 * wanted the reversible act should be able to notice before, not after.
 */
function RemoveDialog({
  member,
  onClose,
  onConfirm,
}: {
  member: Member;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-2xl">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Icon name="UserMinus" size={15} className="text-destructive" />
          Remove {member.display_name || member.email}?
        </h2>
        <p className="mt-2 text-xs text-muted-foreground">
          <span className="font-mono text-foreground">{member.email}</span> loses
          access immediately and every role assignment they hold is dropped.
          Their record is kept, because the rest of the system refers to people
          by address.
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          This is not a toggle — bringing them back means inviting them again
          and activating the invitation. If you only want to pause their access,
          use <span className="text-foreground">Suspend</span> instead.
        </p>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground tech-transition hover:text-foreground"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              setBusy(true);
              void onConfirm();
            }}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground tech-transition hover:opacity-90 disabled:opacity-50"
          >
            {busy && <Icon name="Loader2" size={14} className="animate-spin" />}
            Remove
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Confirm a **permanent deletion**, by name, and say what it costs (N8).
 *
 * "Are you sure?" is not informed consent for an irreversible act. Two rules
 * shaped this copy:
 *
 * 1. **Both halves are named.** The deleted list alone reads as though the
 *    whole database goes; the kept list is what makes the rest clickable, and
 *    the audit trail is the item an admin most needs to know survives.
 * 2. **The expensive items are named specifically**, not folded into "their
 *    data". A connected mailbox takes everything synced from it, because the
 *    schema cascades — an admin who did not expect that should find out here,
 *    not from the count afterwards.
 *
 * The address has to be typed. It is the one action on this screen with no way
 * back, and it sits next to a button (Remove) that looks similar and is
 * reversible.
 */
function PurgeDialog({
  member,
  onClose,
  onConfirm,
}: {
  member: Member;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [typed, setTyped] = useState("");
  // The rule lives in `confirmPurge.ts` and is unit-tested there. Inline, it
  // was fenced only by a grep for the surrounding copy, and `= true` passed.
  const confirmed = purgeConfirmed(typed, member.email);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-md rounded-xl border border-destructive/40 bg-card p-5 shadow-2xl">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Icon name="Trash2" size={15} className="text-destructive" />
          Delete {member.display_name || member.email} permanently?
        </h2>
        <p className="mt-2 text-xs text-muted-foreground">
          This cannot be undone.{" "}
          <span className="font-mono text-foreground">{member.email}</span> stops
          existing as a member; there is no row left to re-activate.
        </p>

        <p className="mt-3 text-[11px] font-medium text-destructive">Deleted</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">
          <li>Their member record, role grants and permission overrides</li>
          <li>Team and room memberships, and app access grants</li>
          <li>
            Connected accounts and their stored credentials — mailbox, WhatsApp
            number, task workspace. Everything mirrored from them goes with the
            account: the whole mailbox, the whole WhatsApp history, and every
            task and project that came from the task workspace.
          </li>
          <li>Their private chat sessions, and any sign-in request on record</li>
        </ul>

        <p className="mt-3 text-[11px] font-medium text-success">Kept</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">
          <li>
            The audit trail — every entry naming them stays, deliberately. An
            audit log that disappears with the person is not an audit log.
          </li>
          <li>Apps, workflows and meetings they authored</li>
          <li>
            Tasks and projects they created <em>here</em> — the ones synced
            from a connected task workspace are listed above, because they go
            with it
          </li>
          <li>
            Shared rooms they started, so other participants keep the transcript
          </li>
        </ul>

        <p className="mt-3 text-xs text-muted-foreground">
          If you only want to end their access, use{" "}
          <span className="text-foreground">Remove</span> — that one is
          recoverable.
        </p>

        <label className="mt-4 mb-1 block text-[11px] font-medium text-muted-foreground">
          Type <span className="font-mono text-foreground">{member.email}</span>{" "}
          to confirm
        </label>
        <input
          autoFocus
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-destructive/50"
        />

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground tech-transition hover:text-foreground"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              setBusy(true);
              void onConfirm();
            }}
            disabled={busy || !confirmed}
            className="flex items-center gap-1.5 rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground tech-transition hover:opacity-90 disabled:opacity-50"
          >
            {busy && <Icon name="Loader2" size={14} className="animate-spin" />}
            Delete permanently
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * The Requests tab — everyone who authenticated and found no account.
 *
 * Approve provisions them AND activates them in one action: an approval is the
 * decision to let somebody in, and they are already at the door. Leaving them
 * `invited` here would re-create the two-click trap this whole tab exists to
 * close (colleague_onboarding.md §6 done-when 7).
 */
function RequestsTab({
  requests,
  roles,
  onDecide,
  failed,
}: {
  requests: AccessRequest[];
  roles: Role[];
  onDecide: (
    email: string,
    decision: "approve" | "deny",
    roleSlug?: string
  ) => Promise<void>;
  /** The queue could not be loaded — an empty list means nothing here. */
  failed: boolean;
}) {
  // "Nobody is waiting" is a claim about the queue, and it may only be made
  // when the queue actually answered. Saying it after a failed fetch is the
  // same silence this tab was built to end.
  if (failed) {
    return (
      <div className="rounded-xl border border-dashed border-warning/40 p-8 text-center">
        <Icon name="AlertTriangle" size={18} className="mx-auto mb-2 text-warning" />
        <p className="text-xs text-muted-foreground">
          The sign-in queue is unavailable, so this list is not the truth.
          People may be locked out without appearing here. Retry with Refresh;
          if it persists, check that migration 143 has been applied.
        </p>
      </div>
    );
  }

  if (requests.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border p-8 text-center">
        <Icon name="DoorOpen" size={18} className="mx-auto mb-2 text-muted-foreground" />
        <p className="text-xs text-muted-foreground">
          Nobody is waiting. Anyone who signs in without an account shows up
          here — you do not have to be told.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {requests.map((r) => (
        <RequestRow key={r.email} request={r} roles={roles} onDecide={onDecide} />
      ))}
    </div>
  );
}

function RequestRow({
  request,
  roles,
  onDecide,
}: {
  request: AccessRequest;
  roles: Role[];
  onDecide: (
    email: string,
    decision: "approve" | "deny",
    roleSlug?: string
  ) => Promise<void>;
}) {
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState(false);
  const assignable = roles.filter((r) => r.slug !== "agent_service");

  const act = async (decision: "approve" | "deny") => {
    setBusy(true);
    try {
      await onDecide(request.email, decision, role);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card p-3 sm:p-4">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium text-foreground">
            {request.display_name || request.email}
          </span>
          {/* Approve provisions them `active` immediately, so this chip is the
              last point at which "not one of us" is visible. The tenant pin
              bounds who can AUTHENTICATE, not who works here — a B2B guest is
              a directory member like anybody else. */}
          {request.is_external && (
            <span
              className="flex shrink-0 items-center gap-1 rounded-md bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning"
              title="Outside the company sign-in domain. Guests invited into the directory can sign in too — check who this is before approving."
            >
              <Icon name="AlertTriangle" size={10} />
              outside the company domain
            </span>
          )}
        </div>
        <div className="truncate text-[11px] text-muted-foreground">
          {request.email}
        </div>
        <div className="mt-1 text-[11px] text-muted-foreground">
          First tried {when(request.first_seen_at)} · last{" "}
          {when(request.last_seen_at)}
        </div>
      </div>

      <span
        className={`rounded-md px-2 py-0.5 text-[10px] ${
          request.attempt_count > 5
            ? "bg-warning/10 text-warning"
            : "bg-secondary text-muted-foreground"
        }`}
        title="How many times they have tried to sign in"
      >
        {request.attempt_count}{" "}
        {request.attempt_count === 1 ? "attempt" : "attempts"}
      </span>

      <select
        value={role}
        onChange={(e) => setRole(e.target.value)}
        aria-label={`Role for ${request.email}`}
        className="rounded-lg border border-border bg-background px-2 py-1.5 text-[11px] text-foreground outline-none focus:border-primary/50"
      >
        {assignable.map((r) => (
          <option key={r.slug} value={r.slug}>
            {r.display_name}
          </option>
        ))}
      </select>

      <div className="flex items-center gap-2">
        <Button size="none" layout="flex items-center" onClick={() => void act("approve")} disabled={busy} className="gap-1.5 px-2.5 py-1.5 text-[11px]">
          {busy ? (
            <Icon name="Loader2" size={12} className="animate-spin" />
          ) : (
            <Icon name="Check" size={12} />
          )}
          Approve
        </Button>
        <Button variant="destructive" size="none" layout="" onClick={() => void act("deny")} disabled={busy} className="px-2.5 py-1.5 text-[11px]">
          Deny
        </Button>
      </div>
    </div>
  );
}

function InviteDialog({
  roles,
  onClose,
  onDone,
}: {
  roles: Role[];
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // What the invite hop reported about the NOTIFICATION email (WS-30 SC-2c).
  // Never an error: the membership is written on both planes either way, so a
  // notification that did not go out is something to MENTION, not to undo.
  const [notice, setNotice] = useState("");

  const assignable = roles.filter((r) => r.slug !== "agent_service");

  const submit = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      // WS-30 SC-2c: `/api/admin/members/invite` SHADOWS the `/api/admin/[…]`
      // catch-all for this one path. It forwards the identical gateway call and,
      // when the deployment is configured for it, sends one notification email.
      const res = await fetch("/api/admin/members/invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim(),
          display_name: displayName.trim(),
          roles: [role],
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Invite failed.");
      }
      const body = await res.json().catch(() => ({}));
      if (body.email_sent === false) {
        setNotice(
          "Invited. No notification email went out — tell them to sign in with this address.",
        );
      }
      await onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invite failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-2xl">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <Icon name="UserPlus" size={15} /> Invite a member
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              This sets up their access. They sign in with this email address —
              there is nothing for them to accept.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-muted-foreground hover:bg-secondary"
            aria-label="Close"
          >
            <Icon name="X" size={15} />
          </button>
        </div>

        <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
          Work email
        </label>
        <input
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="person@fracktal.in"
          className="mb-3 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
        />

        <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
          Display name (optional)
        </label>
        <input
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="mb-3 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
        />

        <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
          Role
        </label>
        <select
          value={role}
          onChange={(e) => setRole(e.target.value)}
          className="mb-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
        >
          {assignable.map((r) => (
            <option key={r.slug} value={r.slug}>
              {r.display_name}
            </option>
          ))}
        </select>
        <p className="mb-4 text-[11px] text-muted-foreground">
          {assignable.find((r) => r.slug === role)?.description ?? ""}
        </p>

        {error && (
          <p className="mb-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}

        {notice && (
          <p className="mb-3 rounded-lg border border-border bg-secondary px-3 py-2 text-xs text-muted-foreground">
            {notice}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground tech-transition hover:text-foreground"
          >
            Cancel
          </button>
          <Button size="none" layout="flex items-center" onClick={() => void submit()} disabled={busy || !email.includes("@")} className="gap-1.5 px-4 py-2 text-sm">
            {busy && <Icon name="Loader2" size={14} className="animate-spin" />}
            Invite
          </Button>
        </div>
      </div>
    </div>
  );
}
