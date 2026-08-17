"use client";

/**
 * /agents — Agent Management
 *
 * Lists all registered agents (built-in + user-added from GitHub repos).
 * Lets users add a new agent via GitHub repo URL and remove user-added ones.
 *
 * Flow for adding a new agent:
 *   1. Enter repo URL + metadata
 *   2. GitHub OAuth (device flow) — only shown if GitHub not yet connected
 *   3. Register → agent appears in picker on the Chat page
 */

import Button from "@/components/ui/Button";
import AppIcon from "@/components/Icon";
import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { AgentEntry } from "@/app/api/agent/list/route";
import type { MutationEntry } from "@/app/api/agent/mutations/route";
import type { IntegrationStatus } from "@/app/api/integrations/status/route";
import type { SkillsCatalog, SkillFamily } from "@/app/api/integrations/skills/route";
import type { AgentSkillSettings } from "@/app/api/agent/[name]/skills/route";
import GitHubDeviceConnect from "@/components/GitHubDeviceConnect";
import FilterPills from "@/components/FilterPills";
import { AgentAvatar, useAgentAvatars, notifyAgentAvatarsChanged } from "@/components/AgentAvatar";
import BreathingCharacter, { characterForAgent } from "@/components/BreathingCharacter";
import {
  CHARACTER_LIBRARY,
  LIBRARY_IDS,
  type LibChar,
} from "@/app/observability/character-library.generated";

// ---------------------------------------------------------------------------
// Pending commits panel (GitHub Copilot agents only)
// ---------------------------------------------------------------------------

function PendingCommits({ agentName }: { agentName: string }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<MutationEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);
  const [busy, setBusy] = useState<Record<string, "approve" | "reject" | "remutate" | "dismiss">>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [expandDiff, setExpandDiff] = useState<Record<string, boolean>>({});
  const [diffs, setDiffs] = useState<Record<string, string>>({});
  // Toast shown after a cascade-approve (e.g. "Approved — 3 earlier commits auto-approved")
  const [cascadeMsg, setCascadeMsg] = useState<string | null>(null);

  const fetchRows = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/agent/mutations");
      const all: MutationEntry[] = res.ok ? await res.json() : [];
      // Show newest first: the latest commit is the one to approve — approving it
      // cascade-approves (and pushes) every earlier commit in the chain (A→B→C),
      // so the latest is surfaced as the single action.
      const filtered = all.filter((r) => r.agent === agentName);
      filtered.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
      setRows(filtered);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [agentName]);

  const toggle = () => {
    if (!open && !fetched) fetchRows();
    setOpen((v) => !v);
  };

  const act = async (id: string, action: "approve" | "reject") => {
    setBusy((b) => ({ ...b, [id]: action }));
    setErrors((e) => { const c = { ...e }; delete c[id]; return c; });
    setCascadeMsg(null);
    try {
      const res = await fetch(`/api/agent/mutations/${encodeURIComponent(id)}/${action}`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setErrors((e) => ({ ...e, [id]: body.detail ?? body.error ?? `HTTP ${res.status}` }));
      } else {
        const body = await res.json().catch(() => ({}));
        // Native-MAF approval opened a Metorite PR — point the operator to it.
        if (action === "approve" && body.status === "pr_open" && body.pr_url) {
          setCascadeMsg(
            `Opened a Metorite PR for ${body.target_path ?? "this agent"} — review and merge it on GitHub.`
          );
          setTimeout(() => setCascadeMsg(null), 8000);
        } else if (action === "approve" && typeof body.cascade_approved === "number" && body.cascade_approved > 0) {
          // Show cascade info when an approve auto-approved earlier commits in the chain
          setCascadeMsg(
            `Approved — ${body.cascade_approved} earlier commit${body.cascade_approved === 1 ? "" : "s"} in this chain were auto-approved.`
          );
          setTimeout(() => setCascadeMsg(null), 6000);
        }
        await fetchRows();
      }
    } catch (err) {
      setErrors((e) => ({ ...e, [id]: String(err) }));
    } finally {
      setBusy((b) => { const c = { ...b }; delete c[id]; return c; });
    }
  };

  const remutate = async (id: string) => {
    setBusy((b) => ({ ...b, [id]: "remutate" }));
    setErrors((e) => { const c = { ...e }; delete c[id]; return c; });
    try {
      const res = await fetch(`/api/agent/mutations/${encodeURIComponent(id)}/remutate`, { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setErrors((e) => ({ ...e, [id]: body.detail ?? body.error ?? `HTTP ${res.status}` }));
      } else {
        setErrors((e) => ({ ...e, [id]: body.message ?? "Commit cleared. Trigger a fresh agent run to re-attempt." }));
        await fetchRows();
      }
    } catch (err) {
      setErrors((e) => ({ ...e, [id]: String(err) }));
    } finally {
      setBusy((b) => { const c = { ...b }; delete c[id]; return c; });
    }
  };

  // Dismiss = remove from view only (terminal state: approved / rejected / audit)
  const dismiss = async (runId: string) => {
    setBusy((b) => ({ ...b, [runId]: "dismiss" }));
    try {
      await fetch(`/api/agent/mutations/${encodeURIComponent(runId)}/dismiss`, { method: "DELETE" });
      await fetchRows();
    } finally {
      setBusy((b) => { const c = { ...b }; delete c[runId]; return c; });
    }
  };

  const deleteCommit = async (id: string) => {
    setBusy((b) => ({ ...b, [id]: "dismiss" }));
    try {
      await fetch(`/api/agent/mutations/${encodeURIComponent(id)}/delete`, { method: "DELETE" });
      await fetchRows();
    } finally {
      setBusy((b) => { const c = { ...b }; delete c[id]; return c; });
    }
  };

  const loadDiff = async (id: string) => {
    if (diffs[id] !== undefined) { setExpandDiff((d) => ({ ...d, [id]: !d[id] })); return; }
    try {
      const res = await fetch(`/api/agent/mutations/${encodeURIComponent(id)}/diff`);
      const data = await res.json();
      setDiffs((d) => ({ ...d, [id]: typeof data.diff_text === "string" ? data.diff_text : JSON.stringify(data) }));
      setExpandDiff((d) => ({ ...d, [id]: true }));
    } catch {
      setDiffs((d) => ({ ...d, [id]: "Failed to load diff." }));
      setExpandDiff((d) => ({ ...d, [id]: true }));
    }
  };

  const pending = rows.filter((r) => r.type === "pending_commit" && r.status === "pending");
  const evalFailed = rows.filter((r) => r.type === "pending_commit" && r.status === "eval_failed");
  // Commits awaiting a decision (pending or eval_failed)
  const awaitingDecision = rows.filter(
    (r) => r.type === "pending_commit" && (r.status === "pending" || r.status === "eval_failed")
  );
  const pendingCount = awaitingDecision.length;

  // Separate display order:
  // 1. Commits awaiting a decision (newest first — the latest is the action)
  // 2. Terminal/informational entries (approved, rejected, audit) shown below
  const decisionRows = rows.filter(
    (r) => r.type === "pending_commit" && (r.status === "pending" || r.status === "eval_failed")
  );
  const terminalRows = rows.filter(
    (r) => !(r.type === "pending_commit" && (r.status === "pending" || r.status === "eval_failed"))
  );
  // Rows are newest-first, so the first plain-pending row is the latest commit.
  // Approving it cascade-approves every earlier pending commit, so ONLY the
  // latest exposes Approve/Reject; the earlier ones render as "included".
  const pendingDecision = decisionRows.filter((r) => r.status === "pending");
  const latestPendingId = pendingDecision[0]?.id ?? null;
  const earlierPendingCount = Math.max(0, pendingDecision.length - 1);

  return (
    <div className="mt-3 border-t border-border pt-3">
      <Button variant="text" size="none" layout="flex items-center" onClick={toggle} className="gap-2 text-xs w-full text-left">
        <span className="font-medium">Self-mutation commits</span>
        {pendingCount > 0 && (
          <span className="rounded-full bg-primary px-1.5 py-0.5 text-[10px] font-semibold text-white">
            {pendingCount}
          </span>
        )}
        {loading && <span className="text-[10px] text-muted-foreground">loading…</span>}
        <span className="ml-auto text-muted-foreground/70">{open ? "▲" : "▼"}</span>
      </Button>

      {open && (
        <div className="mt-2 flex flex-col gap-2">
          {/* Cascade toast */}
          {cascadeMsg && (
            <div className="rounded-md border border-success/20 bg-success/5 px-3 py-2 text-[11px] text-success">
              ✓ {cascadeMsg}
            </div>
          )}

          {fetched && rows.length === 0 && (
            <p className="text-xs text-muted-foreground/70 italic">No commits yet.</p>
          )}

          {/* Decision-required entries: approve/reject must happen before dismiss */}
          {decisionRows.map((m, i) => {
            const key = m.id ?? m.run_id ?? String(i);
            const isPending = m.status === "pending";
            const isEvalFailed = m.status === "eval_failed";
            // The latest pending commit is the single approval action; earlier
            // pending commits are merged in automatically when it's approved.
            const isLatestPending = isPending && m.id === latestPendingId;

            return (
              <div
                key={key}
                className={`rounded-lg border p-3 text-xs ${
                  isEvalFailed
                    ? "border-warning/20 bg-warning/5"
                    : "border-primary/20 bg-primary/5"
                }`}
              >
                {/* Status badge + commit message — no X here, decision required */}
                <div className="flex items-start gap-2">
                  <span
                    className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${
                      isEvalFailed
                        ? "border-warning/20 bg-warning/10 text-warning"
                        : "border-primary/20 bg-primary/10 text-primary"
                    }`}
                  >
                    {isEvalFailed ? "⚠ Eval failed" : "Awaiting review"}
                  </span>
                  {m.commit_message && (
                    <span className="font-mono text-foreground line-clamp-1 flex-1">{m.commit_message}</span>
                  )}
                </div>

                {/* SHA + timestamp */}
                <div className="mt-1 flex items-center gap-3 text-muted-foreground">
                  {m.commit_sha && <span className="font-mono">{m.commit_sha.slice(0, 8)}</span>}
                  <span>{new Date(m.at).toLocaleString("en-IN")}</span>
                </div>

                {m.test_summary && (
                  <div className={`mt-1 ${isEvalFailed ? "text-warning/80" : "text-muted-foreground"}`}>
                    {m.test_summary}
                  </div>
                )}

                {/* Cascade hint — only on the latest commit (the action) */}
                {isLatestPending && earlierPendingCount > 0 && (
                  <p className="mt-1 text-[10px] text-primary/60">
                    Latest commit — approving also approves & pushes {earlierPendingCount} earlier commit{earlierPendingCount === 1 ? "" : "s"} in this chain.
                  </p>
                )}

                {/* Diff toggle */}
                {m.id && (
                  <button
                    onClick={() => loadDiff(m.id!)}
                    className="mt-1.5 text-muted-foreground hover:text-muted-foreground underline underline-offset-2"
                  >
                    {expandDiff[m.id] ? "Hide diff" : "Show diff"}
                  </button>
                )}
                {m.id && expandDiff[m.id] && diffs[m.id] !== undefined && (
                  <pre className="mt-2 overflow-x-auto max-h-48 rounded bg-background p-2 text-[10px] text-muted-foreground border border-border whitespace-pre-wrap">
                    {diffs[m.id] || "(empty diff)"}
                  </pre>
                )}

                {errors[key] && (
                  <p className="mt-1.5 text-destructive">{errors[key]}</p>
                )}

                {/* eval_failed actions */}
                {isEvalFailed && m.id && (
                  <div className="mt-2 flex flex-col gap-1.5">
                    <p className="text-[10px] text-warning/70 mb-0.5">
                      Tests failed — review the diff, then choose an action.
                    </p>
                    <div className="flex gap-1.5 flex-wrap">
                      <button
                        disabled={!!busy[m.id]}
                        onClick={() => act(m.id!, "approve")}
                        className="rounded bg-warning/10 px-2.5 py-1 text-warning hover:bg-warning/15 disabled:opacity-50 transition-colors"
                      >
                        {busy[m.id] === "approve" ? "Pushing…" : "Push Anyway"}
                      </button>
                      <button
                        disabled={!!busy[m.id]}
                        onClick={() => remutate(m.id!)}
                        className="rounded bg-primary/10 px-2.5 py-1 text-primary hover:bg-primary/20 disabled:opacity-50 transition-colors"
                      >
                        {busy[m.id] === "remutate" ? "Resetting…" : "Re-mutate"}
                      </button>
                      <button
                        disabled={!!busy[m.id]}
                        onClick={() => act(m.id!, "reject")}
                        className="rounded bg-destructive/10 px-2.5 py-1 text-destructive hover:bg-destructive/10 disabled:opacity-50 transition-colors"
                      >
                        {busy[m.id] === "reject" ? "Rejecting…" : "Reject & Discard"}
                      </button>
                    </div>
                  </div>
                )}

                {/* pending actions — only the latest commit is actionable;
                    approving it cascade-approves the earlier ones */}
                {isLatestPending && m.id && (
                  <div className="mt-2 flex gap-1.5">
                    <button
                      disabled={!!busy[m.id]}
                      onClick={() => act(m.id!, "approve")}
                      className="rounded bg-success/15 px-2.5 py-1 text-success hover:bg-success/25 disabled:opacity-50 transition-colors"
                    >
                      {busy[m.id] === "approve"
                        ? "Pushing…"
                        : earlierPendingCount > 0
                        ? `Approve & Push (incl. ${earlierPendingCount} earlier)`
                        : "Approve & Push"}
                    </button>
                    <button
                      disabled={!!busy[m.id]}
                      onClick={() => act(m.id!, "reject")}
                      className="rounded bg-destructive/10 px-2.5 py-1 text-destructive hover:bg-destructive/10 disabled:opacity-50 transition-colors"
                    >
                      {busy[m.id] === "reject" ? "Rejecting…" : "Reject & Discard"}
                    </button>
                  </div>
                )}

                {/* Earlier pending commits in the chain — no action; approving
                    the latest above includes them */}
                {isPending && !isLatestPending && (
                  <p className="mt-2 text-[10px] text-muted-foreground/70">
                    ↑ Included when you approve the latest commit above.
                  </p>
                )}
              </div>
            );
          })}

          {/* Terminal / informational entries — dismissable with X */}
          {terminalRows.length > 0 && (
            <>
              {decisionRows.length > 0 && (
                <div className="border-t border-border/60 pt-1 text-[10px] text-muted-foreground uppercase tracking-wide">
                  History
                </div>
              )}
              {terminalRows.map((m, i) => {
                const key = m.id ?? m.run_id ?? String(i);
                const isAudit = m.type === "audit_event";
                const isApproved = m.status === "approved";
                const isRejected = m.status === "rejected";
                const isPrOpen = m.status === "pr_open";
                return (
                  <div
                    key={key}
                    className="relative rounded-lg border border-border bg-card/40 p-3 text-xs"
                  >
                    {/* X = dismiss from view (only for terminal entries) */}
                    <button
                      onClick={() =>
                        isAudit && m.run_id
                          ? dismiss(m.run_id)
                          : m.id
                          ? deleteCommit(m.id)
                          : undefined
                      }
                      disabled={!!busy[key]}
                      className="absolute top-2 right-2 rounded p-0.5 text-muted-foreground hover:bg-secondary hover:text-muted-foreground transition-colors disabled:opacity-30"
                      title="Dismiss"
                    >
                      <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>

                    <div className="flex items-start gap-2 pr-5">
                      <span
                        className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${
                          isApproved
                            ? "border-success/20 bg-success/10 text-success"
                            : isPrOpen
                            ? "border-primary/20 bg-primary/10 text-primary"
                            : isRejected
                            ? "border-border/50 bg-secondary text-muted-foreground"
                            : m.status === "failed"
                            ? "border-destructive/20 bg-destructive/5 text-destructive"
                            : "border-border/50 bg-secondary text-muted-foreground"
                        }`}
                      >
                        {isApproved
                          ? "✓ Approved"
                          : isPrOpen
                          ? "⇡ PR open"
                          : isRejected
                          ? "✗ Rejected"
                          : m.status === "failed"
                          ? "Failed"
                          : m.status}
                      </span>
                      {m.commit_message && (
                        <span className="font-mono text-muted-foreground line-clamp-1">{m.commit_message}</span>
                      )}
                    </div>

                    <div className="mt-1 flex items-center gap-3 text-muted-foreground/70">
                      {m.commit_sha && <span className="font-mono">{m.commit_sha.slice(0, 8)}</span>}
                      <span>{new Date(m.at).toLocaleString("en-IN")}</span>
                      {m.reviewed_by && (
                        <span className="text-muted-foreground">
                          · {m.reviewed_by.startsWith("cascade:") ? "auto-approved" : m.reviewed_by}
                        </span>
                      )}
                      {m.pr_url && (
                        <a
                          href={m.pr_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline underline-offset-2"
                          title={m.target_path ? `PR edits ${m.target_path}` : "View pull request"}
                        >
                          View PR ↗
                        </a>
                      )}
                    </div>

                    {m.id && (
                      <button
                        onClick={() => loadDiff(m.id!)}
                        className="mt-1.5 text-muted-foreground/70 hover:text-muted-foreground underline underline-offset-2"
                      >
                        {expandDiff[m.id] ? "Hide diff" : "Show diff"}
                      </button>
                    )}
                    {m.id && expandDiff[m.id] && diffs[m.id] !== undefined && (
                      <pre className="mt-2 overflow-x-auto max-h-48 rounded bg-background p-2 text-[10px] text-muted-foreground border border-border whitespace-pre-wrap">
                        {diffs[m.id] || "(empty diff)"}
                      </pre>
                    )}
                  </div>
                );
              })}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skills panel — per-agent skill-family toggles (WS-23 S2)
// ---------------------------------------------------------------------------

function fmtTokens(n: number): string {
  return n >= 1000 ? `~${(n / 1000).toFixed(1)}k` : `~${n}`;
}

function AgentSkillsPanel({ agentName }: { agentName: string }) {
  const [open, setOpen] = useState(false);
  const [fetched, setFetched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [families, setFamilies] = useState<SkillFamily[]>([]);
  const [nonToggleable, setNonToggleable] = useState<Record<string, string>>({});
  // Stored reasons per family (from existing rows), for display.
  const [storedReasons, setStoredReasons] = useState<Record<string, string>>({});
  // enabled state per family slug (default: everything enabled — no rows).
  const [enabled, setEnabled] = useState<Record<string, boolean>>({});
  // Baseline from the server, to compute dirtiness.
  const [baseline, setBaseline] = useState<Record<string, boolean>>({});
  const [reason, setReason] = useState("");

  const applySettings = useCallback(
    (fams: SkillFamily[], s: AgentSkillSettings) => {
      const map: Record<string, boolean> = {};
      for (const f of fams) map[f.slug] = !s.disabled_families.includes(f.slug);
      const reasons: Record<string, string> = {};
      for (const row of s.settings) if (row.reason) reasons[row.family] = row.reason;
      setEnabled(map);
      setBaseline(map);
      setStoredReasons(reasons);
      setNonToggleable(s.non_toggleable ?? {});
    },
    [],
  );

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cRes, sRes] = await Promise.all([
        fetch("/api/integrations/skills"),
        fetch(`/api/agent/${encodeURIComponent(agentName)}/skills`),
      ]);
      const cat = cRes.ok ? ((await cRes.json()) as SkillsCatalog) : null;
      const st = sRes.ok ? ((await sRes.json()) as AgentSkillSettings) : null;
      if (!cat || !st) {
        const bad = !cRes.ok ? cRes : sRes;
        const body = await bad.json().catch(() => ({}));
        setError(String(body?.error ?? body?.detail ?? `Request failed (${bad.status})`));
        return;
      }
      setFamilies(cat.families);
      applySettings(cat.families, st);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [agentName, applySettings]);

  const toggle = () => {
    if (!open && !fetched) void fetchAll();
    setOpen((v) => !v);
  };

  const dirty = families.some((f) => enabled[f.slug] !== baseline[f.slug]);
  const enabledFams = families.filter((f) => !f.dynamic && enabled[f.slug] !== false);
  const costTokens = enabledFams.reduce((sum, f) => sum + f.token_cost, 0);

  const save = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      // Replace-wholesale: rows only for families toggled OFF — no rows means
      // the default (enabled), so re-enabling everything clears the table.
      const settings = families
        .filter((f) => !(f.slug in nonToggleable) && enabled[f.slug] === false)
        .map((f) => ({ family: f.slug, enabled: false, reason: reason.trim() || storedReasons[f.slug] || "" }));
      const res = await fetch(`/api/agent/${encodeURIComponent(agentName)}/skills`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(String(body?.detail ?? body?.error ?? `HTTP ${res.status}`));
      } else {
        applySettings(families, body as AgentSkillSettings);
        setReason("");
        setSaved(true);
        setTimeout(() => setSaved(false), 4000);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-3 border-t border-border pt-3">
      <Button variant="text" size="none" layout="flex items-center" onClick={toggle} className="gap-2 text-xs w-full text-left">
        <span className="font-medium">Skills</span>
        {fetched && !loading && families.length > 0 && (
          <span className="text-[10px] text-muted-foreground">
            enabled: {enabledFams.length} families · {fmtTokens(costTokens)} tokens of tool context
          </span>
        )}
        {loading && <span className="text-[10px] text-muted-foreground">loading…</span>}
        <span className="ml-auto text-muted-foreground/70">{open ? "▲" : "▼"}</span>
      </Button>

      {open && (
        <div className="mt-2 flex flex-col gap-1.5">
          <p className="text-[10px] text-muted-foreground/70 leading-relaxed">
            Toggles only narrow what this agent&apos;s code declares — a switch never grants
            a tool the code never asked for. Costs are measured from the injected tool context.
          </p>

          {error && (
            <div className="rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">
              {error}
            </div>
          )}
          {saved && (
            <div className="rounded-md border border-success/20 bg-success/5 px-3 py-2 text-[11px] text-success">
              ✓ Saved — applies from the next run.
            </div>
          )}

          {families.map((f) => {
            const locked = f.slug in nonToggleable;
            const isOn = enabled[f.slug] !== false;
            return (
              <label
                key={f.slug}
                className={`flex items-start gap-2.5 rounded-lg border px-3 py-2 text-xs transition-colors ${
                  locked
                    ? "border-border/60 bg-secondary/30"
                    : isOn
                    ? "border-border bg-card/40 hover:border-primary/30 cursor-pointer"
                    : "border-warning/20 bg-warning/5 hover:border-warning/40 cursor-pointer"
                }`}
                title={locked ? nonToggleable[f.slug] : f.description}
              >
                <input
                  type="checkbox"
                  checked={isOn}
                  disabled={locked || saving}
                  onChange={() => {
                    setSaved(false);
                    setEnabled((m) => ({ ...m, [f.slug]: !(m[f.slug] !== false) }));
                  }}
                  className="mt-0.5 accent-[var(--primary)] disabled:opacity-40"
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-medium text-foreground">{f.label}</span>
                    {f.slug === "core" && (
                      <span className="rounded border border-border/50 bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        always on
                      </span>
                    )}
                    {f.slug === "apps" && (
                      <span className="rounded border border-border/50 bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        managed via App grants
                      </span>
                    )}
                    {!isOn && !locked && (
                      <span className="rounded border border-warning/20 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">
                        disabled
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-[10px] text-muted-foreground">
                    {f.dynamic
                      ? "per-grant tools, resolved at run time"
                      : `${f.tool_count} tool${f.tool_count === 1 ? "" : "s"} · ${fmtTokens(f.token_cost)} tokens`}
                    {storedReasons[f.slug] && !isOn ? ` · “${storedReasons[f.slug]}”` : ""}
                  </span>
                </span>
              </label>
            );
          })}

          {fetched && !loading && families.length === 0 && !error && (
            <p className="text-xs text-muted-foreground/70 italic">Skills catalog unavailable.</p>
          )}

          {dirty && (
            <div className="mt-1 flex flex-col gap-1.5">
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why? (recorded with the change, like member overrides)"
                className="w-full rounded-lg border border-border bg-secondary px-3 py-1.5 text-xs text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none"
              />
              <div className="flex items-center gap-1.5">
                <Button size="none" radius="keep" layout="" onClick={() => void save()} disabled={saving} className="rounded px-3 py-1.5 text-xs">
                  {saving ? "Saving…" : "Save skills"}
                </Button>
                <button
                  onClick={() => { setEnabled(baseline); setReason(""); }}
                  disabled={saving}
                  className="rounded border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-secondary disabled:opacity-50 transition-colors"
                >
                  Reset
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ModalStep = "form" | "github" | "registering" | "done" | "error";

interface AgentConfig {
  name?: string;
  description?: string;
  tags?: string[];
  integrations?: string[];
  optional_integrations?: string[];
}

interface FormValues {
  repoUrl: string;
  name: string;
  description: string;
  tags: string;
  integrations: string;
  optionalIntegrations: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function slugFromRepo(url: string): string {
  const raw = url.trim().replace(/\/$/, "");
  // Local path: use the last folder segment
  if (/^([A-Za-z]:[\\/]|\/)/.test(raw)) {
    const segments = raw.replace(/\\/g, "/").split("/").filter(Boolean);
    const folder = segments[segments.length - 1] ?? "";
    return folder.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  }
  // Extract the repo name part from "owner/repo" or full URL
  const parts = raw.replace("https://github.com/", "").replace("http://github.com/", "").split("/");
  const repo = parts[parts.length - 1] ?? "";
  return repo.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
}

function parseCsv(s: string): string[] {
  return s.split(",").map((t) => t.trim()).filter(Boolean);
}

// ---------------------------------------------------------------------------
// Add-Agent Modal
// ---------------------------------------------------------------------------

function AddAgentModal({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: (agent: AgentEntry) => void;
}) {
  const [step, setStep] = useState<ModalStep>("form");
  const [githubStatus, setGithubStatus] = useState<IntegrationStatus | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [addedAgent, setAddedAgent] = useState<AgentEntry | null>(null);
  const [configLoading, setConfigLoading] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [registerPhase, setRegisterPhase] = useState<"saving" | "cloning">("saving");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [form, setForm] = useState<FormValues>({
    repoUrl: "",
    name: "",
    description: "",
    tags: "",
    integrations: "",
    optionalIntegrations: "",
  });

  // ---------------------------------------------------------------------------
  // Fetch config.json from GitHub and auto-fill form
  // ---------------------------------------------------------------------------

  const fetchConfig = useCallback(async (repoUrl: string) => {
    const raw = repoUrl.trim().replace(/\/$/, "");
    if (!raw) return;

    // Local path: pass raw path directly as repo= param (no GitHub slug extraction)
    const isLocal = /^([A-Za-z]:[\\/]|\/)/.test(raw);
    const paramValue = isLocal
      ? raw
      : raw.replace("https://github.com/", "").replace("http://github.com/", "");

    // For GitHub slugs, need at least owner/repo (two slash-separated parts)
    if (!isLocal && paramValue.split("/").filter(Boolean).length < 2) return;

    setConfigLoading(true);
    setConfigLoaded(false);
    try {
      const res = await fetch(`/api/agent/config?repo=${encodeURIComponent(paramValue)}`);
      if (!res.ok) return; // 404 = no config.json, just leave fields editable
      const cfg: AgentConfig = await res.json();
      setForm((f) => ({
        ...f,
        // Only fill name if user hasn't manually changed it from the slug default
        name:
          f.name === "" || f.name === slugFromRepo(f.repoUrl)
            ? (cfg.name ? slugFromRepo(cfg.name) : slugFromRepo(repoUrl))
            : f.name,
        description: cfg.description ?? f.description,
        tags: cfg.tags?.join(", ") ?? f.tags,
        integrations: cfg.integrations?.join(", ") ?? f.integrations,
        optionalIntegrations: cfg.optional_integrations?.join(", ") ?? f.optionalIntegrations,
      }));
      setConfigLoaded(true);
    } catch {
      // Silent — user can fill manually
    } finally {
      setConfigLoading(false);
    }
  }, []);

  // Debounced repo URL handler: update name immediately, fetch config after 600ms pause
  const handleRepoUrlChange = (val: string) => {
    const slug = slugFromRepo(val);
    setForm((f) => ({
      ...f,
      repoUrl: val,
      name: f.name === "" || f.name === slugFromRepo(f.repoUrl) ? slug : f.name,
    }));
    setConfigLoaded(false);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchConfig(val), 600);
  };

  useEffect(() => () => { if (debounceRef.current) clearTimeout(debounceRef.current); }, []);

  const handleField = (key: keyof FormValues, val: string) =>
    setForm((f) => ({ ...f, [key]: val }));

  // ---------------------------------------------------------------------------
  // Step 1 → submit: check GitHub, then register
  // ---------------------------------------------------------------------------

  const handleFormSubmit = async () => {
    if (!form.repoUrl.trim() || !form.name.trim()) return;

    // Validate agent name format (must match backend: 2-50 lowercase alphanumeric + hyphens)
    const nameRegex = /^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$/;
    if (!nameRegex.test(form.name.trim())) {
      setErrorMsg(
        "Agent name must be 2-50 lowercase letters, digits, or hyphens " +
        "(no spaces, no leading/trailing hyphens)."
      );
      setStep("error");
      return;
    }

    // Check GitHub connection status
    try {
      const res = await fetch("/api/integrations/status");
      if (res.ok) {
        const statuses: IntegrationStatus[] = await res.json();
        const gh = statuses.find((s) => s.service === "github");
        if (gh && !gh.configured) {
          setGithubStatus(gh);
          setStep("github");
          return;
        }
      }
    } catch {
      // If status check fails, proceed to register without blocking
    }

    await doRegister();
  };

  // ---------------------------------------------------------------------------
  // Register the agent
  // ---------------------------------------------------------------------------

  const doRegister = useCallback(async () => {
    setStep("registering");
    setRegisterPhase("saving");
    const cloneTimer = setTimeout(() => setRegisterPhase("cloning"), 800);
    try {
      const rawInput = form.repoUrl.trim();
      const isLocal = /^([A-Za-z]:[\\/]|\/)/.test(rawInput);
      const body: Record<string, unknown> = {
        name: form.name.trim(),
        description: form.description.trim(),
        tags: parseCsv(form.tags),
        integrations: parseCsv(form.integrations),
        optional_integrations: parseCsv(form.optionalIntegrations),
      };
      if (isLocal) {
        body.local_path = rawInput;
      } else {
        body.repo_url = rawInput;
      }
      const res = await fetch("/api/agent/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      clearTimeout(cloneTimer);
      const data = await res.json();
      if (res.ok) {
        setAddedAgent(data as AgentEntry);
        setStep("done");
      } else {
        setErrorMsg(String(data?.detail ?? data?.error ?? `Error ${res.status}`));
        setStep("error");
      }
    } catch (err) {
      clearTimeout(cloneTimer);
      setErrorMsg(err instanceof Error ? err.message : "Network error");
      setStep("error");
    }
  }, [form]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center sm:p-4 bg-black/70"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-t-xl sm:rounded-xl border-t sm:border border-border bg-card shadow-2xl max-h-[92vh] overflow-y-auto mb-14 sm:mb-0 pb-safe"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <div className="text-sm font-semibold text-foreground">Add Agent</div>
            <div className="text-xs text-muted-foreground mt-0.5">
              {step === "form" && "Enter a GitHub repo or a local directory path"}
              {step === "github" && "Connect GitHub to access private repos"}
              {step === "registering" && "Registering agent…"}
              {step === "done" && "Agent registered successfully"}
              {step === "error" && "Registration failed"}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="p-5">
          {/* ── Step: Form ── */}
          {step === "form" && (
            <div className="flex flex-col gap-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">
                  GitHub Repo or Local Path <span className="text-destructive">*</span>
                </label>
                <div className="relative">
                  <input
                    type="text"
                    placeholder="owner/repo  ·  https://github.com/owner/repo  ·  C:\path\to\agent"
                    value={form.repoUrl}
                    onChange={(e) => handleRepoUrlChange(e.target.value)}
                    className="w-full rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none pr-8"
                  />
                  {configLoading && (
                    <div className="absolute right-2.5 top-2.5 h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                  )}
                  {configLoaded && !configLoading && (
                    <div className="absolute right-2.5 top-2 text-success text-xs" title="config.json loaded">✓</div>
                  )}
                </div>
                {configLoaded && (
                  <p className="mt-1 text-xs text-success">config.json found — fields auto-filled</p>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">
                    Agent name <span className="text-destructive">*</span>
                  </label>
                  <input
                    type="text"
                    placeholder="my-agent"
                    value={form.name}
                    onChange={(e) => handleField("name", e.target.value)}
                    className="w-full rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">lowercase, hyphens only</p>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">Tags</label>
                  <input
                    type="text"
                    placeholder="sales, outbound"
                    value={form.tags}
                    onChange={(e) => handleField("tags", e.target.value)}
                    className="w-full rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">comma-separated</p>
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Description</label>
                <input
                  type="text"
                  placeholder="What does this agent do?"
                  value={form.description}
                  onChange={(e) => handleField("description", e.target.value)}
                  className="w-full rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">
                    Mandatory integrations
                  </label>
                  <input
                    type="text"
                    placeholder="zoho-crm, apollo"
                    value={form.integrations}
                    onChange={(e) => handleField("integrations", e.target.value)}
                    className="w-full rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">
                    Optional integrations
                  </label>
                  <input
                    type="text"
                    placeholder="instantly, smtp"
                    value={form.optionalIntegrations}
                    onChange={(e) => handleField("optionalIntegrations", e.target.value)}
                    className="w-full rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none"
                  />
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-1">
                <Button variant="secondary" size="none" layout="" onClick={onClose} className="px-4 py-2 text-sm">
                  Cancel
                </Button>
                <Button size="none" layout="" disabled={!form.repoUrl.trim() || !form.name.trim() || configLoading} onClick={handleFormSubmit} title={configLoading ? "Fetching config.json…" : undefined} className="px-4 py-2 text-sm">
                  {configLoading ? "Fetching…" : "Next →"}
                </Button>
              </div>
            </div>
          )}

          {/* ── Step: GitHub OAuth ── */}
          {step === "github" && githubStatus && (
            <div>
              <p className="mb-4 text-sm text-muted-foreground">
                Connect GitHub so Metorite can clone{" "}
                <span className="font-mono text-foreground">{form.repoUrl}</span> and access
                private repositories.
              </p>
              <GitHubDeviceConnect
                integration={githubStatus}
                onConfigured={() => doRegister()}
              />
              <button
                onClick={() => doRegister()}
                className="mt-3 text-xs text-muted-foreground hover:text-muted-foreground underline"
              >
                Skip — repository is public
              </button>
            </div>
          )}

          {/* ── Step: Registering ── */}
          {step === "registering" && (
            <div className="flex flex-col items-center gap-4 py-8">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <div className="text-center">
                <p className="text-sm text-foreground font-medium">
                  {registerPhase === "saving" ? "Saving agent…" : "Cloning repository…"}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {registerPhase === "saving"
                    ? "Writing to the agent registry"
                    : /^([A-Za-z]:[\\/]|\/)/.test(form.repoUrl.trim())
                      ? "Verifying local path"
                      : `Pulling ${form.repoUrl.trim().split("/").slice(-2).join("/")} in the background`}
                </p>
              </div>
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <span className={registerPhase === "saving" ? "text-primary" : "text-success"}>
                  {registerPhase === "saving" ? "● saving" : "✓ saved"}
                </span>
                <span className="text-muted-foreground/70">→</span>
                <span className={registerPhase === "cloning" ? "text-primary" : "text-muted-foreground"}>
                  {registerPhase === "cloning" ? "● cloning" : "○ clone"}
                </span>
              </div>
            </div>
          )}

          {/* ── Step: Done ── */}
          {step === "done" && addedAgent && (
            <div className="flex flex-col gap-4 py-2">
              {/* Mini agent card preview — same style as AgentCard */}
              <div className="rounded-xl border border-border bg-secondary/60 p-4">
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <span className="text-sm font-semibold text-foreground">{addedAgent.name}</span>
                  <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">custom</span>
                  {addedAgent.local_path && (
                    <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">local</span>
                  )}
                  {/* Runtime badges in the success preview */}
                  {addedAgent.agent_runtime === "github-copilot" ? (
                    <>
                      <span className="shrink-0 rounded-full border border-accent/20 bg-accent/10 px-2 py-0.5 text-xs text-accent">MAF</span>
                      <span className="shrink-0 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs text-primary/80" title="GitHub Copilot SDK — shell, file r/w, MCP, native BYOK">Copilot SDK</span>
                    </>
                  ) : (
                    <span className="shrink-0 rounded-full border border-accent/20 bg-accent/10 px-2 py-0.5 text-xs text-accent">MAF</span>
                  )}
                  <span className="shrink-0 rounded-full bg-success/10 px-2 py-0.5 text-xs text-success">live</span>
                </div>
                {addedAgent.description && (
                  <p className="text-xs text-muted-foreground mb-2">{addedAgent.description}</p>
                )}
                {(addedAgent.tags?.length ?? 0) > 0 && (
                  <div className="flex flex-wrap gap-1 mb-2">
                    {addedAgent.tags!.map((t) => (
                      <span key={t} className="rounded-full bg-secondary px-2 py-0.5 text-xs text-muted-foreground">{t}</span>
                    ))}
                  </div>
                )}
                {((addedAgent.integrations?.length ?? 0) > 0 || (addedAgent.optional_integrations?.length ?? 0) > 0) && (
                  <div className="flex flex-wrap gap-1">
                    {addedAgent.integrations?.map((i) => (
                      <span key={i} className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
                        · {i}
                      </span>
                    ))}
                    {addedAgent.optional_integrations?.map((i) => (
                      <span key={i} className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5 text-xs text-muted-foreground">
                        {i}
                      </span>
                    ))}
                  </div>
                )}
                {addedAgent.local_path ? (
                  <div className="mt-2 flex items-center gap-1 text-xs text-muted-foreground" title={addedAgent.local_path}>
                    <svg className="h-3 w-3 shrink-0" viewBox="0 0 16 16" fill="currentColor">
                      <path d="M1 3.5A1.5 1.5 0 012.5 2h3.257c.466 0 .917.18 1.25.503l.69.69A.5.5 0 008.05 3.5H13.5A1.5 1.5 0 0115 5v7.5A1.5 1.5 0 0113.5 14h-11A1.5 1.5 0 011 12.5V3.5z"/>
                    </svg>
                    <span className="truncate">{addedAgent.local_path}</span>
                  </div>
                ) : addedAgent.repo_url ? (
                  <div className="mt-2">
                    <a href={addedAgent.repo_url} target="_blank" rel="noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-muted-foreground">
                      <svg className="h-3 w-3" viewBox="0 0 16 16" fill="currentColor">
                        <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
                      </svg>
                      {addedAgent.repo_name ?? addedAgent.repo_url}
                    </a>
                  </div>
                ) : null}
              </div>
              <p className="text-xs text-muted-foreground text-center">
                {addedAgent.local_path
                  ? "Agent registered from local path — no cloning needed."
                  : "Repository is being cloned in the background — the agent will be ready shortly."}
              </p>
              <button
                onClick={() => {
                  onAdded(addedAgent);
                  onClose();
                }}
                className="w-full rounded-lg bg-primary px-5 py-2 text-sm font-medium text-white hover:opacity-90 transition-colors"
              >
                Done
              </button>
            </div>
          )}

          {/* ── Step: Error ── */}
          {step === "error" && (
            <div className="flex flex-col gap-4 py-4">
              <div className="rounded-lg border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                {errorMsg}
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="secondary" size="none" layout="" onClick={() => setStep("form")} className="px-4 py-2 text-sm">
                  Back
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function GithubIcon({ size = 16, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" className={className}>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Agent icons + colors by name
// ---------------------------------------------------------------------------

const AGENT_ICONS: Record<string, string> = {
  "task-manager": "CheckSquare",
  "sales":        "TrendingUp",
  "delivery":     "Package",
  "triage":       "Filter",
  "reconciler":   "RefreshCw",
  "billing":      "Receipt",
  "strategy":     "Lightbulb",
  "apis-config":  "Plug",
};

const AGENT_COLORS: Record<string, string> = {
  "task-manager": "text-cyan-400",
  "sales":        "text-emerald-400",
  "delivery":     "text-blue-400",
  "triage":       "text-amber-400",
  "reconciler":   "text-violet-400",
  "billing":      "text-indigo-400",
  "strategy":     "text-orange-400",
  "apis-config":  "text-primary",
};

function getAgentIcon(agent: AgentEntry): string {
  return AGENT_ICONS[agent.name] ?? "Bot";
}

function getAgentColor(agent: AgentEntry): string {
  return AGENT_COLORS[agent.name] ?? "text-muted-foreground";
}

function agentReadiness(
  agent: AgentEntry,
  statuses: IntegrationStatus[]
): "ready" | "blocked" | "unknown" {
  const deps = agent.integrations ?? [];
  if (deps.length === 0) return "ready";
  if (statuses.length === 0) return "unknown";
  return deps.some((i) => {
    const s = statuses.find((x) => x.service === i);
    return s !== undefined && !s.configured;
  })
    ? "blocked"
    : "ready";
}

// ---------------------------------------------------------------------------
// AgentTile — compact grid card
// ---------------------------------------------------------------------------

function AgentTile({
  agent,
  selected,
  statuses,
  onClick,
  onRefresh,
  avatarLibraryId,
}: {
  agent: AgentEntry;
  selected: boolean;
  statuses: IntegrationStatus[];
  onClick: () => void;
  onRefresh?: () => void;
  avatarLibraryId?: string | null;
}) {
  const iconName  = getAgentIcon(agent);
  const color     = getAgentColor(agent);
  const readiness = agentReadiness(agent, statuses);
  const behindBy  = (agent as any).behind_by as number | undefined;
  const [pulling, setPulling] = useState(false);

  const handlePull = async (e: React.MouseEvent) => {
    e.stopPropagation(); // don't select the tile
    setPulling(true);
    try {
      const res = await fetch(
        `/api/agent/${encodeURIComponent(agent.name)}/pull`,
        { method: "POST" },
      );
      if (res.ok) {
        const data = await res.json();
        // If LLM resolved conflicts, show briefly in a tooltip-like way
        if (data.conflicts_resolved_by_llm) {
          // The onRefresh will re-fetch and update behind_by
        }
      }
      onRefresh?.();
    } catch {
      // silently fail — badge will update on next poll
    } finally {
      setPulling(false);
    }
  };

  const isCopilotAgent = agent.agent_runtime === "github-copilot";

  return (
    <button
      onClick={onClick}
      className={`text-left w-full rounded-xl border transition-all relative overflow-hidden ${
        selected
          ? "border-primary bg-primary/5 ring-1 ring-primary/20"
          : "border-border bg-card hover:border-primary/40 hover:bg-secondary/30"
      }`}
    >
      {/* Update badge — shows count when behind, green check when up-to-date for Copilot agents */}
      {isCopilotAgent && (
        behindBy && behindBy > 0 ? (
          <button
            onClick={handlePull}
            disabled={pulling}
            className="absolute top-1 right-1 rounded-full bg-amber-500 px-2 py-0.5 text-[10px] font-bold text-white shadow-md hover:bg-amber-600 disabled:opacity-60 transition-colors cursor-pointer z-10"
            title={`${behindBy} commit${behindBy !== 1 ? "s" : ""} behind — click to pull`}
          >
            {pulling ? "…" : behindBy}
          </button>
        ) : (
          <span
            className="absolute top-1 right-1 rounded-full bg-success/80 px-1.5 py-0.5 text-[10px] font-bold text-white shadow-md z-10"
            title="Up to date"
          >
            ✓
          </span>
        )
      )}

      {/* Two columns: the agent's CHARACTER (breathing) fills the left at
          full card height; identity + status + description on the right. */}
      <div className="flex items-stretch min-h-[6.5rem]">
        <div className="flex w-16 shrink-0 items-center justify-center self-stretch border-r border-border/60 bg-background/40">
          <BreathingCharacter
            char={characterForAgent(agent.name, avatarLibraryId)}
            box={62}
            fallback={
              <AgentAvatar
                libraryId={avatarLibraryId}
                size={34}
                fallback={<AppIcon name={iconName} size={26} className={`${color} shrink-0`} />}
              />
            }
          />
        </div>
        <div className="min-w-0 flex-1 p-3">
          <div className="flex items-start justify-between gap-2">
            <div className="font-medium text-sm text-foreground leading-tight truncate">
              {agent.display_name || agent.name}
            </div>
            <span
              className={`w-2 h-2 mt-1 rounded-full shrink-0 ${
                readiness === "ready"   ? "bg-success" :
                readiness === "blocked" ? "bg-warning"  : "bg-muted"
              }`}
              title={
                readiness === "ready"   ? "Ready" :
                readiness === "blocked" ? "Blocked \u2014 needs API connections" : "Unknown"
              }
            />
          </div>
          <div className={`text-[10px] mt-0.5 ${
            readiness === "ready"   ? "text-success" :
            readiness === "blocked" ? "text-warning"  : "text-muted-foreground"
          }`}>
            {readiness === "ready" ? "\u25cf Ready" :
             readiness === "blocked" ? "\u26a0 Blocked" : "\u25cb Unknown"}
          </div>
          {agent.description && (
            <p className="text-[10px] text-muted-foreground mt-1.5 line-clamp-2 leading-relaxed">
              {agent.description}
            </p>
          )}
        </div>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Avatar picker — assign a character from the generated library
// ---------------------------------------------------------------------------

// Steps a library character's breathing spritesheet (south, N frames) so the
// picker previews show the same gentle breathing as the office. The sprite frames
// carry a lot of transparent padding, so we render the sprite ~1.35x the tile and
// clip it (overflow hidden) — the character reads much bigger without cropping.
const LIB_PICKER_STYLE = `
.lib-breathe-wrap { display:flex; align-items:center; justify-content:center; overflow:hidden; }
.lib-breathe { display:block; flex:0 0 auto; image-rendering:pixelated;
  background-repeat:no-repeat; background-position:0 0;
  background-size: calc(var(--n) * var(--w)) var(--w);
  animation: lib-play calc(var(--n) * .2s) steps(var(--n)) infinite; }
@keyframes lib-play { to { background-position-x: calc(-1 * var(--n) * var(--w)); } }
@media (prefers-reduced-motion: reduce){ .lib-breathe { animation: none; } }
`;

function labelizeRole(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function LibBreathingSprite({ char, box, scale = 1.35 }: { char: LibChar; box: number; scale?: number }) {
  const sheet = char.breathing?.south;
  const frames = char.breathingFrames;
  const w = Math.round(box * scale);
  return (
    <span className="lib-breathe-wrap" style={{ width: box, height: box }}>
      {sheet && frames ? (
        <span
          className="lib-breathe"
          style={{
            width: w,
            height: w,
            backgroundImage: `url(${sheet})`,
            "--n": frames,
            "--w": `${w}px`,
          } as React.CSSProperties}
        />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={char.portrait} alt="" style={{ width: w, height: w, imageRendering: "pixelated" }} />
      )}
    </span>
  );
}

function AgentAvatarPicker({ agentName }: { agentName: string }) {
  // undefined = still loading the current assignment; null = default (no library char)
  const [libraryId, setLibraryId] = useState<string | null | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [cat, setCat] = useState<string>("all");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // The component is keyed by agentName at the render site, so it mounts fresh per
  // agent (initial state `undefined` = loading) — no synchronous reset needed here.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/observability/avatars");
        const data = await res.json();
        const cfg = data?.avatars?.[agentName]?.config as { libraryId?: string | null } | undefined;
        if (alive) setLibraryId(cfg?.libraryId ?? null);
      } catch {
        if (alive) setLibraryId(null);
      }
    })();
    return () => { alive = false; };
  }, [agentName]);

  const chars = LIBRARY_IDS.map((id) => CHARACTER_LIBRARY[id]);
  const roles = Array.from(new Set(chars.map((c) => c.role)));
  const shown = cat === "all" ? chars : chars.filter((c) => c.role === cat);

  const assign = async (id: string | null) => {
    if (saving) return;
    const prev = libraryId;
    setSaving(true);
    setErr(null);
    setLibraryId(id); // optimistic
    try {
      const url = `/api/observability/avatars/${encodeURIComponent(agentName)}`;
      const res = id === null
        ? await fetch(url, { method: "DELETE" })
        : await fetch(url, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ config: { libraryId: id }, sprite: null }),
          });
      if (!res.ok) throw new Error(String(res.status));
      // Tell every surface (grid tiles, detail header, chat) to refetch so the
      // new avatar shows immediately without a page reload.
      notifyAgentAvatarsChanged();
    } catch {
      setLibraryId(prev ?? null);
      setErr("Couldn't save — try again.");
    } finally {
      setSaving(false);
    }
  };

  const tileCls = (active: boolean) =>
    `flex items-center justify-center overflow-hidden rounded-lg border bg-background/40 transition-transform hover:scale-105 ${
      active ? "border-primary ring-2 ring-primary ring-offset-1 ring-offset-card" : "border-border"
    }`;

  const current = libraryId ? CHARACTER_LIBRARY[libraryId] : null;

  return (
    <div>
      <style>{LIB_PICKER_STYLE}</style>
      <div className="text-[10px] text-muted uppercase tracking-wider mb-1.5">Avatar</div>

      {/* Compact trigger — opens the picker in a popup so it doesn't crowd the panel */}
      <button
        onClick={() => setOpen(true)}
        className="flex w-full items-center gap-2.5 rounded-lg border border-border bg-card/40 p-2 text-left transition-colors hover:border-primary/40"
      >
        <span className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-md bg-background/40">
          {current ? (
            <LibBreathingSprite char={current} box={44} />
          ) : (
            <AppIcon name="Bot" size={20} className="text-muted-foreground" />
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-foreground">
            {libraryId === undefined
              ? "Loading…"
              : current
              ? [labelizeRole(current.role), current.gender].filter(Boolean).join(" · ")
              : "Default character"}
          </span>
          <span className="block text-[10px] text-muted-foreground">
            Tap to choose from the library
          </span>
        </span>
        <AppIcon name="ChevronRight" size={15} className="shrink-0 text-muted-foreground" />
      </button>

      {/* Popup picker — bottom sheet on mobile, centered dialog on desktop */}
      {open && (
        <div
          className="fixed inset-0 z-[80] flex items-end justify-center bg-black/70 sm:items-center sm:p-4"
          onClick={() => setOpen(false)}
        >
          <div
            className="mb-14 flex max-h-[85vh] w-full max-w-md flex-col overflow-hidden rounded-t-2xl border-t border-border bg-card shadow-2xl pb-safe sm:mb-0 sm:rounded-2xl sm:border"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-foreground">Choose avatar</div>
                <div className="truncate text-[11px] text-muted-foreground">{agentName}</div>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="rounded-md p-1 text-muted-foreground hover:bg-secondary transition-colors"
              >
                <AppIcon name="X" size={16} />
              </button>
            </div>
            <div className="overflow-y-auto p-4">
              {LIBRARY_IDS.length === 0 ? (
                <p className="text-[11px] text-muted-foreground/70">
                  No library characters available yet.
                </p>
              ) : (
                <>
                  {/* Broad categories + show-all */}
                  <div className="mb-3 flex flex-wrap gap-1">
                    {["all", ...roles].map((r) => (
                      <button
                        key={r}
                        onClick={() => setCat(r)}
                        className={`rounded-lg border px-2.5 py-1 text-xs capitalize transition-colors ${
                          cat === r
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-border bg-card/60 text-muted-foreground hover:border-primary/40 hover:text-foreground"
                        }`}
                      >
                        {r === "all" ? "All" : labelizeRole(r)}
                      </button>
                    ))}
                  </div>
                  {/* Fixed-size tiles in a centered wrap — 3-up on the mobile sheet,
                      3–4-up on the desktop dialog. Bigger sprites read clearly. */}
                  <div className="flex flex-wrap justify-center gap-2.5">
                    {/* Default (no library character — office uses the role default) */}
                    <button
                      onClick={() => assign(null)}
                      disabled={saving}
                      title="Default role character"
                      style={{ width: 104, height: 104 }}
                      className={`${tileCls(libraryId === null)} flex-col gap-1 disabled:opacity-60`}
                    >
                      <AppIcon name="Bot" size={26} className="text-muted-foreground" />
                      <span className="text-[10px] text-muted-foreground">Default</span>
                    </button>
                    {shown.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => assign(c.id)}
                        disabled={saving}
                        title={`${[labelizeRole(c.role), c.gender].filter(Boolean).join(" · ")}\n${c.description}`}
                        style={{ width: 104, height: 104 }}
                        className={`${tileCls(libraryId === c.id)} disabled:opacity-60`}
                      >
                        <LibBreathingSprite char={c} box={98} />
                      </button>
                    ))}
                  </div>
                  {err && <p className="mt-2 text-[11px] text-destructive">{err}</p>}
                  <p className="mt-3 text-[11px] text-muted-foreground/70">
                    Assigned characters appear in the office with their full breathing /
                    typing / sleeping animation.
                  </p>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentSidePanel
// ---------------------------------------------------------------------------

function AgentSidePanel({
  agent,
  statuses,
  onClose,
  onRemove,
  onRefresh,
  compact = false,
  avatarLibraryId,
}: {
  agent: AgentEntry;
  statuses: IntegrationStatus[];
  onClose: () => void;
  onRemove: (name: string) => void;
  onRefresh?: () => void;
  compact?: boolean;
  avatarLibraryId?: string | null;
}) {
  const iconName  = getAgentIcon(agent);
  const color = getAgentColor(agent);
  const [confirming, setConfirming] = useState(false);
  const [removing, setRemoving]     = useState(false);
  const [pulling, setPulling]       = useState(false);
  const [checking, setChecking]     = useState(false);
  const [pullResult, setPullResult] = useState<{
    pulled: number;
    behind_by: number;
    strategy: string;
    conflicts_resolved_by_llm: boolean;
    head_before?: string;
    head_after?: string;
  } | null>(null);
  const behindBy = (agent as any).behind_by as number | undefined;

  const doPull = async () => {
    setPulling(true);
    setPullResult(null);
    try {
      const res = await fetch(
        `/api/agent/${encodeURIComponent(agent.name)}/pull`,
        { method: "POST" },
      );
      if (res.ok) {
        const data = await res.json();
        setPullResult({
          pulled: data.pulled ?? 0,
          behind_by: data.behind_by ?? 0,
          strategy: data.strategy ?? "unknown",
          conflicts_resolved_by_llm: data.conflicts_resolved_by_llm ?? false,
          head_before: data.head_before,
          head_after: data.head_after,
        });
      }
      onRefresh?.();
    } catch {
      // silently fail
    } finally {
      setPulling(false);
    }
  };

  const checkUpdates = async () => {
    setChecking(true);
    try {
      await fetch(
        `/api/agent/${encodeURIComponent(agent.name)}/pull`,
        { method: "POST" },
      );
      onRefresh?.();
    } catch {
      // silently fail
    } finally {
      setChecking(false);
    }
  };

  const isCopilotAgent = agent.agent_runtime === "github-copilot";

  const missingDeps = (agent.integrations ?? []).filter((i) => {
    const s = statuses.find((x) => x.service === i);
    return s !== undefined && !s.configured;
  });

  const handleRemove = async () => {
    setRemoving(true);
    try {
      const res = await fetch(`/api/agent/${encodeURIComponent(agent.name)}`, { method: "DELETE" });
      if (res.ok) { onRemove(agent.name); onClose(); }
      else {
        const d = await res.json().catch(() => ({}));
        alert(String(d?.detail ?? d?.error ?? "Failed to remove agent"));
      }
    } catch { alert("Network error while removing agent"); }
    finally { setRemoving(false); setConfirming(false); }
  };

  // Display name (alias) editing — allowed for every agent (built-in + custom);
  // a pure display overlay, saved via PATCH. The canonical `agent.name` never
  // changes. Re-seed when a different agent is selected.
  const [alias, setAlias] = useState(agent.display_name ?? "");
  const [savingAlias, setSavingAlias] = useState(false);
  const [aliasSaved, setAliasSaved] = useState(false);
  useEffect(() => {
    setAlias(agent.display_name ?? "");
    setAliasSaved(false);
  }, [agent.name, agent.display_name]);

  const aliasDirty = alias.trim() !== (agent.display_name ?? "").trim();
  const saveAlias = async () => {
    setSavingAlias(true);
    setAliasSaved(false);
    try {
      const res = await fetch(`/api/agent/${encodeURIComponent(agent.name)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: alias.trim() }),
      });
      if (res.ok) {
        setAliasSaved(true);
        onRefresh?.();
      } else {
        const d = await res.json().catch(() => ({}));
        alert(String(d?.detail ?? d?.error ?? "Failed to save display name"));
      }
    } catch {
      alert("Network error while saving display name");
    } finally {
      setSavingAlias(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header — hidden in compact (mobile) mode since the wrapper provides its own.
          Two columns: the agent's CHARACTER (breathing, full height) on the left,
          identity/info on the right — the persona fronts the settings panel. */}
      {!compact && (
        <div className="flex items-stretch justify-between border-b border-border shrink-0">
          <div className="flex items-stretch gap-0 min-w-0 flex-1">
            <div className="flex w-24 shrink-0 items-center justify-center self-stretch border-r border-border/60 bg-background/40 py-4">
              <BreathingCharacter
                char={characterForAgent(agent.name, avatarLibraryId)}
                box={92}
                fallback={
                  <AgentAvatar
                    libraryId={avatarLibraryId}
                    size={48}
                    fallback={<AppIcon name={iconName} size={40} className={`${color} shrink-0`} />}
                  />
                }
              />
            </div>
            <div className="min-w-0 flex-1 p-5">
              <div className="font-semibold text-foreground text-base">{agent.display_name || agent.name}</div>
              {agent.display_name ? (
                <div className="text-[11px] text-muted-foreground font-mono mt-0.5">{agent.name}</div>
              ) : null}
              <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                {agent.agent_runtime === "github-copilot" ? (
                  <>
                    <span className="text-[10px] px-1.5 py-0.5 rounded border border-accent/20 bg-accent/10 text-accent">MAF</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded border border-primary/30 bg-primary/10 text-primary/80">Copilot SDK</span>
                  </>
                ) : (
                  <span className="text-[10px] px-1.5 py-0.5 rounded border border-accent/20 bg-accent/10 text-accent">MAF</span>
                )}
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                  agent.status === "live" ? "bg-success/10 text-success" : "bg-secondary text-muted-foreground"
                }`}>{agent.status}</span>
                {agent.dynamic && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">custom</span>
                )}
              </div>
            </div>
          </div>
          <button onClick={onClose} className="self-start m-3 text-muted-foreground hover:text-foreground p-1 rounded transition-colors">
            <AppIcon name="X" className="w-4 h-4" />
          </button>
        </div>
      )}

      <div className={`flex-1 overflow-y-auto space-y-4 ${compact ? "p-3" : "p-5"}`}>
        {/* Display name (alias) — editable for every agent (built-in + custom) */}
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Display name
          </label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder={agent.name}
              value={alias}
              onChange={(e) => { setAlias(e.target.value); setAliasSaved(false); }}
              onKeyDown={(e) => { if (e.key === "Enter" && aliasDirty && !savingAlias) saveAlias(); }}
              className="w-full rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none"
            />
            <button
              onClick={saveAlias}
              disabled={!aliasDirty || savingAlias}
              className="shrink-0 rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-foreground hover:bg-accent/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {savingAlias ? "Saving…" : "Save"}
            </button>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Friendly nickname shown in chat, observability, and here. The technical name{" "}
            <span className="font-mono">{agent.name}</span> stays the same.
            {aliasSaved && !aliasDirty ? <span className="ml-1 text-success">Saved.</span> : null}
          </p>
        </div>

        {agent.description && (
          <p className="text-sm text-muted-foreground leading-relaxed">{agent.description}</p>
        )}

        {(agent.tags?.length ?? 0) > 0 && (
          <div className="flex flex-wrap gap-1">
            {agent.tags!.map((t) => (
              <span key={t} className="text-xs px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{t}</span>
            ))}
          </div>
        )}

        {/* Avatar — assign a character from the generated library */}
        <AgentAvatarPicker key={agent.name} agentName={agent.name} />

        {missingDeps.length > 0 && (
          <div className="flex items-start gap-2 rounded-lg border border-warning/20 bg-warning/5 px-3 py-2.5">
            <AppIcon name="AlertTriangle" className="w-3.5 h-3.5 text-warning mt-0.5 shrink-0" />
            <div className="text-xs text-warning">
              <span className="font-medium">Agent blocked</span> — needs{" "}
              <Link href="/integrations" className="underline hover:opacity-80">
                {missingDeps.map((i) => statuses.find((s) => s.service === i)?.label ?? i).join(", ")}
              </Link>
            </div>
          </div>
        )}

        {agent.dep_status?.ok === false && (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2.5">
            <AppIcon name="AlertTriangle" className="w-3.5 h-3.5 text-destructive mt-0.5 shrink-0" />
            <div className="text-xs text-destructive min-w-0">
              <span className="font-medium">Dependencies failed to install</span>
              {(agent.dep_status.needs_system_packages?.length ?? 0) > 0 ? (
                <>
                  {" "}— this agent (or a dependency) needs system packages. Run on the server:
                  <code className="mt-1 block font-mono text-[11px] bg-background/60 rounded px-2 py-1 break-all">
                    sudo apt-get install -y {agent.dep_status.needs_system_packages!.join(" ")}
                  </code>
                </>
              ) : (
                <> — some of its tools may not work.</>
              )}
              {agent.dep_status.error && (
                <div className="mt-1 text-[10px] text-muted-foreground font-mono whitespace-pre-wrap break-all max-h-16 overflow-y-auto">
                  {agent.dep_status.error.slice(-280)}
                </div>
              )}
            </div>
          </div>
        )}

        {(agent.integrations?.length ?? 0) > 0 && (
          <div>
            <div className="text-[10px] text-muted uppercase tracking-wider mb-1.5">Required Integrations</div>
            <div className="flex flex-wrap gap-1.5">
              {agent.integrations!.map((i) => {
                const intg  = statuses.find((s) => s.service === i);
                const state = intg === undefined ? "unknown" : intg.configured ? "ok" : "missing";
                return (
                  <Link key={i} href="/integrations">
                    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium cursor-pointer transition-colors ${
                      state === "ok"
                        ? "border-success/20 text-success bg-success/10 hover:bg-success/20"
                        : state === "missing"
                        ? "border-warning/20 text-warning bg-warning/5 hover:bg-warning/10"
                        : "border-border text-muted-foreground"
                    }`}>
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                        state === "ok" ? "bg-success" : state === "missing" ? "bg-warning" : "bg-muted"
                      }`} />
                      {intg?.label ?? i}
                      {state === "missing" && <AppIcon name="ExternalLink" className="w-2.5 h-2.5" />}
                    </span>
                  </Link>
                );
              })}
            </div>
          </div>
        )}

        {(agent.optional_integrations?.length ?? 0) > 0 && (
          <div>
            <div className="text-[10px] text-muted uppercase tracking-wider mb-1.5">Optional Integrations</div>
            <div className="flex flex-wrap gap-1.5">
              {agent.optional_integrations!.map((i) => {
                const intg = statuses.find((s) => s.service === i);
                const ok   = intg?.configured === true;
                return (
                  <Link key={i} href="/integrations">
                    <span className={`inline-flex items-center gap-1.5 rounded-full border border-dashed px-2.5 py-1 text-xs cursor-pointer transition-colors ${
                      ok ? "border-success/20 text-success hover:bg-success/10" : "border-border text-muted-foreground hover:bg-secondary"
                    }`}>
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${ok ? "bg-success" : "bg-muted"}`} />
                      {intg?.label ?? i}
                    </span>
                  </Link>
                );
              })}
            </div>
          </div>
        )}

        {(agent.local_path ?? agent.repo_url ?? agent.repo_name) && (
          <div>
            <div className="text-[10px] text-muted uppercase tracking-wider mb-1.5">Source</div>
            {agent.local_path ? (
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono bg-secondary rounded-lg px-2.5 py-1.5">
                <AppIcon name="FolderOpen" className="w-3.5 h-3.5 shrink-0" />
                <span className="truncate">{agent.local_path}</span>
              </div>
            ) : (
              <a
                href={
                  agent.repo_url ??
                  (agent.repo_name?.includes("/")
                    ? `https://github.com/${agent.repo_name}`
                    : `https://github.com/FracktalWorks/${agent.repo_name}`)
                }
                target="_blank" rel="noreferrer"
                className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                <GithubIcon size={14} className="shrink-0" />
                <span className="truncate">{agent.repo_name ?? agent.repo_url}</span>
                <AppIcon name="ExternalLink" className="w-2.5 h-2.5 shrink-0 ml-auto" />
              </a>
            )}
          </div>
        )}

        {/* Pull updates section — always visible for Copilot agents */}
        {isCopilotAgent && (
          <div>
            <div className="text-[10px] text-muted uppercase tracking-wider mb-1.5">
              Repository Updates
            </div>

            {/* Status: up-to-date */}
            {!behindBy && !pullResult && (
              <div className="flex items-center gap-2">
                <span className="flex items-center gap-1.5 rounded-lg bg-success/5 border border-success/10 px-3 py-1.5 text-xs text-success">
                  <span className="w-1.5 h-1.5 rounded-full bg-success" />
                  Up to date
                </span>
                <Button variant="secondary" size="none" layout="" onClick={checkUpdates} disabled={checking} title="Force-check for remote updates" className="px-2.5 py-1.5 text-xs">
                  <AppIcon name="RefreshCw" className={`w-3 h-3 ${checking ? "animate-spin" : ""}`} />
                </Button>
              </div>
            )}

            {/* Status: behind — pull button */}
            {behindBy && behindBy > 0 && !pullResult && (
              <button
                onClick={doPull}
                disabled={pulling}
                className="flex items-center justify-center gap-2 w-full rounded-lg bg-amber-500/10 border border-amber-500/20 px-3 py-2 text-xs font-medium text-amber-600 hover:bg-amber-500/20 disabled:opacity-50 transition-colors"
              >
                <AppIcon name="RefreshCw" className={`w-3.5 h-3.5 ${pulling ? "animate-spin" : ""}`} />
                {pulling
                  ? "Pulling…"
                  : `Pull ${behindBy} update${behindBy !== 1 ? "s" : ""}`}
              </button>
            )}

            {/* Pulling spinner */}
            {pulling && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground animate-pulse">
                <AppIcon name="RefreshCw" className="w-3.5 h-3.5 animate-spin" />
                Pulling latest commits…
              </div>
            )}

            {/* Pull result card */}
            {pullResult && (
              <div className={`rounded-lg border p-3 text-xs ${
                pullResult.conflicts_resolved_by_llm
                  ? "border-primary/30 bg-primary/5"
                  : pullResult.pulled > 0
                  ? "border-success/20 bg-success/5"
                  : "border-border bg-secondary/30"
              }`}>
                {pullResult.pulled > 0 ? (
                  <>
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`w-1.5 h-1.5 rounded-full ${
                        pullResult.conflicts_resolved_by_llm
                          ? "bg-primary"
                          : "bg-success"
                      }`} />
                      <span className="font-medium text-foreground">
                        {pullResult.pulled} commit{pullResult.pulled !== 1 ? "s" : ""} pulled
                      </span>
                    </div>
                    <div className="text-muted-foreground space-y-0.5">
                      {pullResult.head_before && pullResult.head_after && (
                        <div className="font-mono">
                          {pullResult.head_before} → {pullResult.head_after}
                        </div>
                      )}
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="capitalize">
                          Strategy: {pullResult.strategy.replace(/-/g, " ")}
                        </span>
                        {pullResult.conflicts_resolved_by_llm && (
                          <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-primary">
                            🤖 LLM-resolved conflicts
                          </span>
                        )}
                        {pullResult.strategy === "rebase-ours" && (
                          <span className="rounded-full bg-warning/10 px-1.5 py-0.5 text-warning">
                            ⚠ Auto-resolved (--ours)
                          </span>
                        )}
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <span className="w-1.5 h-1.5 rounded-full bg-muted" />
                    Already up to date
                  </div>
                )}
                {pullResult.behind_by > 0 && (
                  <p className="mt-1 text-muted-foreground">
                    Still {pullResult.behind_by} commit{pullResult.behind_by !== 1 ? "s" : ""} behind — pull again?
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {/* Per-agent skill toggles (WS-23 S2) — narrows the injected tool
            families; keyed so switching agents remounts with fresh state. */}
        <AgentSkillsPanel key={agent.name} agentName={agent.name} />

        {/* Show pending commits for ALL agents — any agent can have
            self-mutation commits awaiting review, not just github-copilot. */}
        <PendingCommits agentName={agent.name} />

        <div className="flex gap-2 pt-2">
          <Link
            href={`/chat?agent=${encodeURIComponent(agent.name)}`}
            className="flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-lg bg-primary hover:opacity-90 text-sm font-medium text-primary-foreground transition-colors"
          >
            <AppIcon name="MessageCircle" className="w-4 h-4" /> Chat
          </Link>
          {agent.dynamic && (
            confirming ? (
              <div className="flex items-center gap-1.5">
                <button disabled={removing} onClick={() => void handleRemove()}
                  className="px-3 py-2 rounded-lg bg-destructive text-destructive-foreground text-xs hover:opacity-90 disabled:opacity-50 transition-colors">
                  {removing ? "\u2026" : "Remove"}
                </button>
                <button onClick={() => setConfirming(false)}
                  className="px-3 py-2 rounded-lg border border-border text-xs text-muted-foreground hover:bg-secondary transition-colors">
                  Cancel
                </button>
              </div>
            ) : (
              <button onClick={() => setConfirming(true)}
                className="p-2.5 rounded-lg border border-border text-muted-foreground hover:border-destructive/40 hover:text-destructive transition-colors"
                title="Remove agent">
                <AppIcon name="Trash2" className="w-4 h-4" />
              </button>
            )
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AgentsPage() {
  const [agents, setAgents]     = useState<AgentEntry[]>([]);
  const [intgs, setIntgs]       = useState<IntegrationStatus[]>([]);
  const [loading, setLoading]   = useState(true);
  const agentAvatars            = useAgentAvatars();
  const [filter, setFilter]     = useState<"all" | "builtin" | "custom">("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [showAdd, setShowAdd]   = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [aRes, iRes] = await Promise.all([
        fetch("/api/agent/list"),
        fetch("/api/integrations/status"),
      ]);
      if (aRes.ok) setAgents(await aRes.json());
      if (iRes.ok) { const d = await iRes.json(); if (Array.isArray(d)) setIntgs(d); }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    // Only auto-select first agent on desktop (sm+). On mobile, keep the grid clean.
    const isDesktop = typeof window !== "undefined" && window.innerWidth >= 640;
    if (!loading && agents.length > 0 && !selected && isDesktop) setSelected(agents[0].name);
  }, [loading, agents, selected]);

  const handleRemove = (name: string) => {
    setAgents((p) => p.filter((a) => a.name !== name));
    if (selected === name) setSelected(null);
  };

  const handleAdded = (agent: AgentEntry) =>
    setAgents((p) => [...p, { ...agent, dynamic: true }]);

  const [checkingAll, setCheckingAll] = useState(false);
  const copilotAgents = agents.filter(
    (a) => a.agent_runtime === "github-copilot",
  );
  const behindCount = copilotAgents.filter(
    (a) => typeof (a as any).behind_by === "number" && (a as any).behind_by > 0,
  ).length;

  const checkAllUpdates = async () => {
    setCheckingAll(true);
    try {
      await Promise.all(
        copilotAgents.map((a) =>
          fetch(`/api/agent/${encodeURIComponent(a.name)}/pull`, {
            method: "POST",
          }).catch(() => {}),
        ),
      );
      await load();
    } catch {
      // silently fail
    } finally {
      setCheckingAll(false);
    }
  };

  const filtered = agents.filter((a) =>
    filter === "builtin" ? !a.dynamic : filter === "custom" ? !!a.dynamic : true
  );
  const selectedAgent = selected ? (agents.find((a) => a.name === selected) ?? null) : null;
  const readyCnt      = agents.filter((a) => agentReadiness(a, intgs) === "ready").length;
  const blockedCnt    = agents.filter((a) => agentReadiness(a, intgs) === "blocked").length;

  const FILTERS = [
    { id: "all"     as const, label: "All",      count: agents.length },
    { id: "builtin" as const, label: "Built-in", count: agents.filter((a) => !a.dynamic).length },
    { id: "custom"  as const, label: "Custom",   count: agents.filter((a) => !!a.dynamic).length },
  ];

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-4 sm:px-6 py-3 sm:py-4 border-b border-border shrink-0">
        <div>
          <h1 className="text-base sm:text-lg font-bold text-foreground">Agents</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            {loading
              ? "Loading\u2026"
              : `${agents.length} agents \u00b7 ${readyCnt} ready${blockedCnt > 0 ? ` \u00b7 ${blockedCnt} blocked` : ""}${copilotAgents.length > 0 ? ` \u00b7 ${copilotAgents.length} git-tracked` : ""}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {copilotAgents.length > 0 && (
            <button
              onClick={checkAllUpdates}
              disabled={checkingAll}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm font-medium transition-colors disabled:opacity-50 ${
                behindCount > 0
                  ? "border-amber-500/30 bg-amber-500/10 text-amber-600 hover:bg-amber-500/20"
                  : "border-border text-muted-foreground hover:bg-secondary hover:text-foreground"
              }`}
              title={
                behindCount > 0
                  ? `${behindCount} agent${behindCount !== 1 ? "s" : ""} behind — pull all`
                  : "Check all for updates"
              }
            >
              <AppIcon name="RefreshCw" className={`w-3.5 h-3.5 ${checkingAll ? "animate-spin" : ""}`} />
              <span className="hidden sm:inline">
                {checkingAll
                  ? "Checking…"
                  : behindCount > 0
                  ? `Pull All (${behindCount})`
                  : "Check All"}
              </span>
            </button>
          )}
          <button onClick={() => void load()} title="Refresh agent list"
            className="p-2 rounded-lg border border-border text-muted-foreground hover:bg-secondary transition-colors">
            <AppIcon name="RefreshCw" className="w-4 h-4" />
          </button>
          <Button size="none" layout="flex items-center" onClick={() => setShowAdd(true)} className="gap-1.5 px-3 py-2 text-sm">
            <AppIcon name="Plus" className="w-4 h-4" /><span className="hidden sm:inline">Add Agent</span>
          </Button>
        </div>
      </div>

      {/* Filter pills — shared FilterPills component */}
      <FilterPills
        items={FILTERS}
        activeId={filter}
        onChange={(id) => setFilter(id as typeof filter)}
      />

      <div className="flex flex-1 overflow-hidden relative">
        <div className={`flex-1 p-4 overflow-y-auto ${selectedAgent ? "sm:min-w-0" : ""}`}>
          {loading ? (
            <div className="flex items-center justify-center h-48 gap-3 text-muted-foreground text-sm">
              <AppIcon name="Loader2" className="w-4 h-4 animate-spin" /> Loading agents…
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-3 text-muted-foreground text-sm">
              <p>No {filter !== "all" ? filter : ""} agents found.</p>
              <button onClick={() => setShowAdd(true)} className="flex items-center gap-1.5 text-xs text-primary hover:opacity-80">
                <AppIcon name="Plus" className="w-3.5 h-3.5" /> Add agent
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
              {filtered.map((agent) => (
                <AgentTile
                  key={agent.name}
                  agent={agent}
                  selected={selected === agent.name}
                  statuses={intgs}
                  onClick={() => setSelected(agent.name)}
                  onRefresh={load}
                  avatarLibraryId={agentAvatars[agent.name]}
                />
              ))}
              <button
                onClick={() => setShowAdd(true)}
                className="p-4 rounded-xl border-2 border-dashed border-border text-muted-foreground hover:border-primary/40 hover:text-primary hover:bg-primary/5 transition-all flex flex-col items-center justify-center gap-2 min-h-[120px]"
              >
                <AppIcon name="Plus" className="w-5 h-5" /><span className="text-xs">Add Agent</span>
              </button>
            </div>
          )}
        </div>

        {selectedAgent && (
          <>
            {/* Mobile: compact slide-up panel (60% height) — grid stays visible below */}
            <div className="sm:hidden fixed inset-0 z-[60] pointer-events-none">
              <div className="absolute inset-0 bg-black/50 pointer-events-auto" onClick={() => setSelected(null)} />
              <aside className="absolute inset-x-0 bottom-14 pointer-events-auto flex max-h-[60%] flex-col rounded-t-2xl border-t border-border bg-card shadow-2xl chat-fade-in">
                <div className="flex items-center justify-between px-4 py-2 border-b border-border shrink-0">
                  <div className="flex items-center gap-2 min-w-0">
                    {(() => {
                      const iconName = getAgentIcon(selectedAgent);
                      const color = getAgentColor(selectedAgent);
                      return (
                        <AgentAvatar
                          libraryId={agentAvatars[selectedAgent.name]}
                          size={20}
                          fallback={<AppIcon name={iconName} size={20} className={color} />}
                        />
                      );
                    })()}
                    <span className="text-sm font-semibold truncate">{selectedAgent.display_name || selectedAgent.name}</span>
                  </div>
                  <button onClick={() => setSelected(null)} className="p-1 rounded-md hover:bg-secondary text-muted-foreground shrink-0">
                    <AppIcon name="X" size={16} />
                  </button>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto pb-safe">
                  <AgentSidePanel
                    agent={selectedAgent}
                    statuses={intgs}
                    onClose={() => setSelected(null)}
                    onRemove={handleRemove}
                    onRefresh={load}
                    compact
                    avatarLibraryId={agentAvatars[selectedAgent.name]}
                  />
                </div>
              </aside>
            </div>
            {/* Desktop: side panel */}
            <div className="hidden sm:flex w-[380px] border-l border-border bg-card shrink-0 flex-col overflow-hidden">
              <AgentSidePanel
                agent={selectedAgent}
                statuses={intgs}
                onClose={() => setSelected(null)}
                onRemove={handleRemove}
                onRefresh={load}
                avatarLibraryId={agentAvatars[selectedAgent.name]}
              />
            </div>
          </>
        )}
      </div>

      {showAdd && (
        <AddAgentModal
          onClose={() => setShowAdd(false)}
          onAdded={handleAdded}
        />
      )}
    </div>
  );
}
