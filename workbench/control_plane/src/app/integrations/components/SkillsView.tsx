"use client";

/** The Skills view (moved from the old Skills tab, no behaviour change). */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import React, { useCallback, useEffect, useRef, useState } from "react";
import type { SkillFamily, SkillsCatalog } from "@/app/api/integrations/skills/route";

// ===========================================================================
// Main Page
// ===========================================================================

// ===========================================================================
// SKILLS TAB — read-only skill-family catalog (WS-23 S1)
// ===========================================================================

function fmtTokens(t: number): string {
  return t >= 1000 ? `~${(t / 1000).toFixed(1)}k tokens` : `~${t} tokens`;
}

function SkillFamilyCard({ family }: { family: SkillFamily }) {
  const [open, setOpen] = useState(false);
  const chevron = open ? "ChevronDown" : "ChevronRight";
  return (
    <div className="p-4 rounded-xl border border-border bg-card hover:bg-secondary/10 transition-colors">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left"
        aria-expanded={open}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-foreground">{family.label}</span>
              {family.core && (
                <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border bg-violet-500/15 text-violet-400 border-violet-500/25">
                  <Icon name="Lock" className="w-2.5 h-2.5" /> Core · always on
                </span>
              )}
              {!family.core && !family.scope_governed && (
                <span className="text-[10px] px-1.5 py-0.5 rounded border bg-teal-500/15 text-teal-400 border-teal-500/25">
                  Always injected
                </span>
              )}
              {family.dynamic && (
                <span className="text-[10px] px-1.5 py-0.5 rounded border bg-cat-12/15 text-cat-12 border-cat-12/25">
                  Dynamic
                </span>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{family.description}</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-[11px] font-mono text-muted-foreground whitespace-nowrap">
              {family.dynamic
                ? "per-grant"
                : `${family.tool_count} tool${family.tool_count !== 1 ? "s" : ""} · ${fmtTokens(family.token_cost)}`}
            </span>
            <Icon name={chevron} className="w-3.5 h-3.5 text-muted" />
          </div>
        </div>
      </button>
      {open && (
        <div className="mt-3 pt-3 border-t border-border">
          {family.dynamic ? (
            <div className="text-[11px] font-mono text-muted-foreground">
              {family.tool_pattern ?? "resolved at run time"}
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {family.tools.map((t) => (
                <span key={t} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-secondary text-muted-foreground border border-border">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function SkillsView() {
  const [catalog, setCatalog] = useState<SkillsCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const hasFetched = useRef(false);

  const fetchCatalog = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/integrations/skills");
      const d = await r.json();
      if (r.ok) setCatalog(d as SkillsCatalog);
      else setError(d?.error ?? `Request failed (${r.status})`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!hasFetched.current) { hasFetched.current = true; void fetchCatalog(); }
  }, [fetchCatalog]);

  const families = catalog?.families ?? [];
  const agents = catalog?.agents ?? [];

  return (
    <div className="flex flex-col flex-1 overflow-y-auto p-6 gap-6">
      {/* Hero */}
      <div className="flex items-start gap-4 p-5 rounded-2xl border border-border bg-card">
        <div className="w-10 h-10 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
          <Icon name="Sparkles" className="w-5 h-5 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="font-semibold text-foreground">Agent Skills</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                {loading
                  ? "Measuring…"
                  : catalog
                  ? `${families.length} families · full injected tool context ${fmtTokens(catalog.total_injected_tokens)}`
                  : "Unavailable"}
              </p>
            </div>
            <button onClick={() => void fetchCatalog()} disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border hover:bg-secondary text-xs font-medium text-muted-foreground shrink-0 disabled:opacity-40 transition-colors">
              <Icon name="RefreshCw" className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
            </button>
          </div>
          <p className="text-sm text-muted-foreground mt-2 leading-relaxed max-w-xl">
            The platform injects these tool families into agents at run time. Costs are measured — each
            family&apos;s system-prompt addendum plus its tool schemas, counted with the run-context tokenizer.
            The matrix shows each agent&apos;s effective surface (declared scope ∩ enabled families);
            toggle families per agent from the Agents page side panel.
          </p>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-xl border border-destructive/30 bg-destructive/10 text-xs text-destructive">
          <Icon name="AlertCircle" className="w-4 h-4 shrink-0" /> {error}
        </div>
      )}

      {/* Family cards */}
      {families.length > 0 && (
        <div>
          <div className="text-[10px] text-muted uppercase tracking-wider mb-3">Skill families</div>
          <div className="space-y-2">
            {families.map((f) => <SkillFamilyCard key={f.slug} family={f} />)}
          </div>
        </div>
      )}

      {/* Per-agent matrix */}
      {agents.length > 0 && (
        <div>
          <div className="text-[10px] text-muted uppercase tracking-wider mb-3">Which agent has what</div>
          <div className="rounded-xl border border-border bg-card overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left font-medium text-muted-foreground px-4 py-2.5">Agent</th>
                  {families.map((f) => (
                    <th key={f.slug} className="text-center font-medium text-muted-foreground px-3 py-2.5 whitespace-nowrap">
                      {f.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.name} className="border-b border-border last:border-b-0 hover:bg-secondary/10 transition-colors">
                    <td className="px-4 py-2.5">
                      <span className="font-mono font-semibold text-foreground">{a.name}</span>
                      {a.all_families && (
                        <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-secondary text-muted" title="No tool_scope declared — injection is fail-open, the agent receives every family.">
                          all (no scope)
                        </span>
                      )}
                    </td>
                    {families.map((f) => (
                      <td key={f.slug} className="text-center px-3 py-2.5">
                        {a.families.includes(f.slug)
                          ? <Icon name="Check" className="w-3.5 h-3.5 text-success inline" />
                          : (a.disabled_families ?? []).includes(f.slug)
                          ? <span className="text-[10px] px-1.5 py-0.5 rounded bg-warning/10 text-warning" title="Explicitly disabled by an admin (skill toggle)">off</span>
                          : <span className="text-muted">—</span>}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!loading && !error && families.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-3 py-10 text-center">
          <div className="w-12 h-12 rounded-2xl bg-secondary border border-border flex items-center justify-center">
            <Icon name="Sparkles" className="w-6 h-6 text-muted-foreground" />
          </div>
          <div>
            <div className="text-sm font-medium text-foreground">No skills catalog available</div>
            <div className="text-xs text-muted-foreground mt-0.5">The gateway did not return any skill families.</div>
          </div>
        </div>
      )}
    </div>
  );
}
