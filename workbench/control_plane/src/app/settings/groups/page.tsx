"use client";

/**
 * Settings → Teams — the org's groups (Centers Phase B).
 *
 * Spec: project-docs/specs/department_centers.md §3 Phase B;
 *       groups_sessions_authority.md §1/§6.
 *
 * "Team" is the human word; the primitive is `org_group`, and for the six
 * Center groups the slug IS the center slug. Groups scope (rooms, team
 * agents, a Center's data slice) — they never authorize. The one bridge is
 * the add-member toggle that also grants the matching `center.<slug>`
 * feature override, which is Phase B's "one admin action". Removing someone
 * from a team deliberately does NOT revoke that feature — revocation is an
 * explicit decision made on their member page.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAccess } from "@/components/AccessProvider";
import type { Group, Member } from "../members/types";

export default function GroupsPage() {
  const { access } = useAccess();
  const [groups, setGroups] = useState<Group[]>([]);
  const [roster, setRoster] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Group | null>(null);
  const [addingTo, setAddingTo] = useState<Group | null>(null);

  const load = useCallback(async () => {
    try {
      // Fetch first: no synchronous setState inside the mount effect.
      const [g, m] = await Promise.all([
        fetch("/api/admin/groups", { cache: "no-store" }),
        fetch("/api/admin/members", { cache: "no-store" }),
      ]);
      setError("");
      if (!g.ok) {
        const body = await g.json().catch(() => ({}));
        throw new Error(body.detail ?? `Could not load teams (${g.status})`);
      }
      setGroups(await g.json());
      if (m.ok) setRoster(await m.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load teams.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Wrapped rather than called directly: the effect must not reach a
    // setState synchronously (react-hooks/set-state-in-effect), and `load` is
    // also invoked after every mutation, so it stays a useCallback.
    const run = async () => {
      await load();
    };
    void run();
  }, [load]);

  const removeGroup = async (slug: string) => {
    setError("");
    const res = await fetch(`/api/admin/groups/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail ?? "Could not delete this team.");
      return;
    }
    await load();
  };

  const removeMember = async (slug: string, email: string) => {
    setError("");
    const res = await fetch(
      `/api/admin/groups/${encodeURIComponent(slug)}/members/${encodeURIComponent(email)}`,
      { method: "DELETE" }
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail ?? "Could not remove this member.");
      return;
    }
    await load();
  };

  const setMemberRole = async (slug: string, email: string, role: "lead" | "member") => {
    setError("");
    // The add endpoint upserts, so promoting/demoting is the same call. The
    // grant is off here: role changes must not mint feature overrides.
    const res = await fetch(`/api/admin/groups/${encodeURIComponent(slug)}/members`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, role, grant_center_access: false }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail ?? "Could not change this member's role.");
      return;
    }
    await load();
  };

  if (!access.is_admin && !loading) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">Teams is admin-only.</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-3 shrink-0 sm:px-6 sm:py-4">
        <div className="flex items-center gap-3">
          <Link
            href="/settings/organization"
            className="rounded-lg border border-border p-2 text-muted-foreground tech-transition hover:bg-secondary"
            aria-label="Back to Organisation"
          >
            <Icon name="ArrowLeft" size={15} />
          </Link>
          <div>
            <h1 className="text-base font-bold text-foreground sm:text-lg">Teams</h1>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Groups that scope Centers, shared sessions, and team agents
            </p>
          </div>
        </div>
        <Button size="lg" layout="flex items-center" onClick={() => setCreating(true)}>
          <Icon name="Plus" size={15} />
          New team
        </Button>
      </div>

      {error && (
        <div className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive sm:mx-6">
          <span className="flex-1">{error}</span>
          <button onClick={() => setError("")} aria-label="Dismiss">
            <Icon name="X" size={13} />
          </button>
        </div>
      )}
      {notice && (
        <div className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs text-muted-foreground sm:mx-6">
          <span className="flex-1">{notice}</span>
          <button onClick={() => setNotice("")} aria-label="Dismiss">
            <Icon name="X" size={13} />
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        <p className="mb-4 rounded-lg border border-border bg-secondary/30 px-3 py-2 text-[11px] text-muted-foreground">
          Teams decide what people share — a Center&apos;s slice, shared
          sessions, team agents. They never grant permissions: those stay on
          roles and each person&apos;s member page.
        </p>

        {loading ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Icon name="Loader2" size={14} className="animate-spin" /> Loading teams…
          </div>
        ) : groups.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No teams yet — the six department teams arrive with the seed
            migration, or create one above.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {groups.map((g) => (
              <div key={g.slug} className="rounded-xl border border-border bg-card p-3 sm:p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setEditing(g)}
                        className="text-sm font-medium text-foreground hover:text-primary tech-transition"
                        title="Edit name & description"
                      >
                        {g.display_name}
                      </button>
                      <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                        {g.slug}
                      </code>
                      {g.is_center && (
                        <span
                          className="flex items-center gap-1 text-[10px] text-muted-foreground"
                          title={`Pairs with the center.${g.slug} feature — cannot be deleted`}
                        >
                          <Icon name="Building2" size={10} /> Center
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{g.description}</p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-[11px] text-muted-foreground">
                      {g.member_count} member{g.member_count === 1 ? "" : "s"}
                    </span>
                    <Button variant="secondary" size="none" layout="flex items-center" onClick={() => setAddingTo(g)} className="gap-1.5 px-2.5 py-1.5 text-[11px]">
                      <Icon name="UserPlus" size={12} />
                      Add member
                    </Button>
                    {!g.is_center && (
                      <Button variant="destructive" size="icon" layout="" onClick={() => void removeGroup(g.slug)} title="Delete team (must be empty)">
                        <Icon name="Trash2" size={14} />
                      </Button>
                    )}
                  </div>
                </div>

                {g.members.length > 0 && (
                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {g.members.map((m) => (
                      <span
                        key={m.email}
                        className="flex items-center gap-1.5 rounded-lg border border-border bg-secondary/40 px-2 py-1 text-[11px] text-foreground"
                      >
                        <button
                          onClick={() =>
                            void setMemberRole(
                              g.slug,
                              m.email,
                              m.role === "lead" ? "member" : "lead"
                            )
                          }
                          title={
                            m.role === "lead"
                              ? "Lead — click to make member"
                              : "Member — click to make lead"
                          }
                          className={
                            m.role === "lead"
                              ? "text-warning"
                              : "text-muted-foreground/50 hover:text-warning"
                          }
                        >
                          <Icon name="Star"
                            size={11}
                            fill={m.role === "lead" ? "currentColor" : "none"}
                          />
                        </button>
                        <span title={m.email}>{m.display_name || m.email}</span>
                        <button
                          onClick={() => void removeMember(g.slug, m.email)}
                          className="text-muted-foreground hover:text-destructive"
                          aria-label={`Remove ${m.email} from ${g.display_name}`}
                          title="Remove from team (keeps any Center access — revoke that on their member page)"
                        >
                          <Icon name="X" size={11} />
                        </button>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {creating && (
        <CreateGroupDialog
          onClose={() => setCreating(false)}
          onDone={async () => {
            setCreating(false);
            await load();
          }}
        />
      )}
      {editing && (
        <EditGroupDialog
          group={editing}
          onClose={() => setEditing(null)}
          onDone={async () => {
            setEditing(null);
            await load();
          }}
        />
      )}
      {addingTo && (
        <AddMemberDialog
          group={addingTo}
          roster={roster}
          onClose={() => setAddingTo(null)}
          onDone={async (granted) => {
            setAddingTo(null);
            setNotice(
              granted
                ? `Added — the ${addingTo.display_name} Center is now visible to them.`
                : ""
            );
            await load();
          }}
        />
      )}
    </div>
  );
}

function CreateGroupDialog({
  onClose,
  onDone,
}: {
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const slug = name.trim().toLowerCase().replace(/\s+/g, "-");

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/admin/groups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug,
          display_name: name.trim(),
          description: description.trim(),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Could not create the team.");
      }
      await onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the team.");
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
            <h2 className="text-sm font-semibold text-foreground">New team</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              A scoping group — it shares things, it never grants permissions.
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
          Name
        </label>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Night Shift"
          className="mb-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
        />
        <p className="mb-3 text-[11px] text-muted-foreground">
          {slug ? (
            <>
              Slug: <code className="font-mono">{slug}</code> — the wire name;
              it cannot be changed later.
            </>
          ) : (
            "The slug is derived from the name and cannot be changed later."
          )}
        </p>

        <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
          Description (optional)
        </label>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What this team is for"
          className="mb-4 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
        />

        {error && (
          <p className="mb-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground tech-transition hover:text-foreground"
          >
            Cancel
          </button>
          <Button size="none" layout="flex items-center" onClick={() => void submit()} disabled={busy || !slug} className="gap-1.5 px-4 py-2 text-sm">
            {busy && <Icon name="Loader2" size={14} className="animate-spin" />}
            Create team
          </Button>
        </div>
      </div>
    </div>
  );
}

function EditGroupDialog({
  group,
  onClose,
  onDone,
}: {
  group: Group;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const [name, setName] = useState(group.display_name);
  const [description, setDescription] = useState(group.description);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`/api/admin/groups/${encodeURIComponent(group.slug)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: name.trim() || group.display_name,
          description: description.trim(),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Could not save the team.");
      }
      await onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the team.");
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
            <h2 className="text-sm font-semibold text-foreground">
              Edit {group.display_name}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              The slug (<code className="font-mono">{group.slug}</code>) is the
              wire name and cannot change.
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
          Name
        </label>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="mb-3 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
        />

        <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
          Description
        </label>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="mb-4 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
        />

        {error && (
          <p className="mb-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground tech-transition hover:text-foreground"
          >
            Cancel
          </button>
          <Button size="none" layout="flex items-center" onClick={() => void submit()} disabled={busy} className="gap-1.5 px-4 py-2 text-sm">
            {busy && <Icon name="Loader2" size={14} className="animate-spin" />}
            Save
          </Button>
        </div>
      </div>
    </div>
  );
}

function AddMemberDialog({
  group,
  roster,
  onClose,
  onDone,
}: {
  group: Group;
  roster: Member[];
  onClose: () => void;
  onDone: (granted: boolean) => Promise<void>;
}) {
  const inGroup = new Set(group.members.map((m) => m.email));
  const candidates = roster.filter(
    (m) => m.status === "active" && !inGroup.has(m.email)
  );
  const [email, setEmail] = useState(candidates[0]?.email ?? "");
  const [role, setRole] = useState<"lead" | "member">("member");
  const [grant, setGrant] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await fetch(
        `/api/admin/groups/${encodeURIComponent(group.slug)}/members`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email,
            role,
            grant_center_access: group.is_center && grant,
          }),
        }
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? "Could not add the member.");
      }
      await onDone(Boolean(body.center_access_granted));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add the member.");
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
              <Icon name="UserPlus" size={15} /> Add to {group.display_name}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-muted-foreground hover:bg-secondary"
            aria-label="Close"
          >
            <Icon name="X" size={15} />
          </button>
        </div>

        {candidates.length === 0 ? (
          <p className="mb-4 text-xs text-muted-foreground">
            Every active member is already on this team.
          </p>
        ) : (
          <>
            <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
              Member
            </label>
            <select
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mb-3 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
            >
              {candidates.map((m) => (
                <option key={m.email} value={m.email}>
                  {m.display_name ? `${m.display_name} — ${m.email}` : m.email}
                </option>
              ))}
            </select>

            <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
              Team role
            </label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as "lead" | "member")}
              className="mb-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
            >
              <option value="member">Member</option>
              <option value="lead">Lead</option>
            </select>
            <p className="mb-3 text-[11px] text-muted-foreground">
              Leads manage this team&apos;s membership. Neither role grants
              platform permissions.
            </p>

            {group.is_center && (
              <label className="mb-4 flex items-start gap-2 rounded-lg border border-border bg-secondary/30 px-3 py-2">
                <input
                  type="checkbox"
                  checked={grant}
                  onChange={(e) => setGrant(e.target.checked)}
                  className="mt-0.5"
                />
                <span className="text-[11px] text-muted-foreground">
                  <span className="font-medium text-foreground">
                    Also grant access to the {group.display_name} Center
                  </span>{" "}
                  — writes an allow override for{" "}
                  <code className="font-mono">center.{group.slug}</code> on
                  their member page. An existing override is never changed.
                </span>
              </label>
            )}
          </>
        )}

        {error && (
          <p className="mb-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground tech-transition hover:text-foreground"
          >
            Cancel
          </button>
          <Button size="none" layout="flex items-center" onClick={() => void submit()} disabled={busy || !email || candidates.length === 0} className="gap-1.5 px-4 py-2 text-sm">
            {busy && <Icon name="Loader2" size={14} className="animate-spin" />}
            Add
          </Button>
        </div>
      </div>
    </div>
  );
}
