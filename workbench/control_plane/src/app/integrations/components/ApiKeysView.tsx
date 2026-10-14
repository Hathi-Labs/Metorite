"use client";

/** The API keys view: the per-org service-key catalog (moved from the old APIs tab, no behaviour change). */

import Button from "@/components/ui/Button";
import Icon, { themedIcon } from "@/components/Icon";
import { useRouter } from "next/navigation";
import React, { useCallback, useEffect, useRef, useState } from "react";
import type { IntegrationStatus } from "@/app/api/integrations/status/route";
import type { AgentEntry } from "@/app/api/agent/list/route";
import GitHubAccountBadge from "@/components/GitHubAccountBadge";
import FilterPills from "@/components/FilterPills";
import { Input } from "@/components/ui/Input";
import { orgKeyConfigured, type Connection } from "../lib/connections";
import OperatorEnvVars from "../OperatorEnvVars";

// ===========================================================================
// APIS TAB — full existing implementation
// ===========================================================================

type ApiEntry = IntegrationStatus & {
  category: string;
  is_custom?: boolean;
  domain?: string;
};

interface DiscoveredDef {
  service_id: string;
  label: string;
  category: string;
  description: string;
  domain?: string;
  setup_url: string;
  docs_url: string;
  instructions: string;
  env_vars: { key: string; label: string; sensitive: boolean }[];
}

const CATEGORIES = [
  { id: "all",           label: "All" },
  { id: "core",          label: "Core" },
  { id: "crm",           label: "CRM" },
  { id: "prospecting",   label: "Prospecting" },
  { id: "productivity",  label: "Productivity" },
  { id: "search",        label: "Search" },
  { id: "analytics",     label: "Analytics" },
  { id: "payments",      label: "Payments" },
  { id: "communication", label: "Communication" },
  { id: "custom",        label: "Custom" },
] as const;
type CatId = (typeof CATEGORIES)[number]["id"];

const CAT_COLORS: Record<string, string> = {
  core:          "bg-violet-500/15 text-violet-400 border-violet-500/25",
  crm:           "bg-blue-500/15 text-blue-400 border-blue-500/25",
  email:         "bg-cat-12/15 text-cat-12 border-cat-12/25",
  prospecting:   "bg-emerald-500/15 text-emerald-400 border-emerald-500/25",
  productivity:  "bg-cyan-500/15 text-cyan-400 border-cyan-500/25",
  search:        "bg-orange-500/15 text-orange-400 border-orange-500/25",
  analytics:     "bg-rose-500/15 text-rose-400 border-rose-500/25",
  payments:      "bg-indigo-500/15 text-indigo-400 border-indigo-500/25",
  communication: "bg-teal-500/15 text-teal-400 border-teal-500/25",
  storage:       "bg-sky-500/15 text-sky-400 border-sky-500/25",
  custom:        "bg-secondary text-muted-foreground border-border",
};

const KNOWN_DOMAINS: Record<string, string> = {
  "zoho-crm":        "zoho.com",
  "apollo":          "apollo.io",
  "google-maps":     "google.com",
  "instantly":       "instantly.ai",
  "gmail-oauth":     "mail.google.com",
  "microsoft-oauth": "outlook.office.com",
  "whatsapp":        "whatsapp.com",
  "smtp":            "",
  "github":          "github.com",
  "serpapi":         "serpapi.com",
  "apify":           "apify.com",
  "anymailfinder":   "anymailfinder.com",
  "google-sheets":   "sheets.google.com",
};

/** Direct logo URLs for services where domain-based favicon/clearbit heuristics fail.
 *  These are official or reliable CDN-hosted SVG/PNG logos. */
const KNOWN_LOGOS: Record<string, string> = {
  // Google services — the favicon for mail.google.com returns the generic Google "G",
  // so we use Gmail's own static icon.
  "gmail-oauth":   "https://ssl.gstatic.com/ui/v1/icons/mail/rfr/gmail.ico",
  // Microsoft / Outlook
  "microsoft-oauth": "https://outlook.live.com/favicon.ico",
};

const DISCOVER_SUGGESTIONS = [
  "Microsoft", "Google", "Atlassian",
  "Notion", "Slack", "HubSpot",
  "Stripe", "Twilio", "Airtable", "Linear",
];

const CATEGORY_FALLBACK: Record<string, { icon: string; color: string }> = {
  core:          { icon: "Settings2",     color: "text-violet-400" },
  crm:           { icon: "Users",         color: "text-blue-400" },
  email:         { icon: "Mail",          color: "text-cat-12" },
  prospecting:   { icon: "Target",        color: "text-emerald-400" },
  productivity:  { icon: "CheckSquare",   color: "text-cyan-400" },
  search:        { icon: "Globe",         color: "text-orange-400" },
  analytics:     { icon: "BarChart2",     color: "text-rose-400" },
  payments:      { icon: "CreditCard",    color: "text-indigo-400" },
  communication: { icon: "MessageSquare", color: "text-teal-400" },
  storage:       { icon: "HardDrive",     color: "text-sky-400" },
};

function logoUrls(domain: string): string[] {
  if (!domain) return [];
  return [
    `https://www.google.com/s2/favicons?domain=${domain}&sz=128`,
    `https://logo.clearbit.com/${domain}`,
    `https://icons.duckduckgo.com/ip3/${domain}.ico`,
  ];
}

function ServiceLogo({
  service, label, domain, category, size = "md",
}: {
  service?: string; label: string; domain?: string; category: string; size?: "sm" | "md" | "lg";
}) {
  // 1) Direct known logo (bypasses domain heuristics for Gmail, Outlook, etc.)
  const knownLogo = service ? KNOWN_LOGOS[service] : undefined;
  // 2) Domain-based fallbacks (Google favicons → Clearbit → DuckDuckGo)
  const resolvedDomain = KNOWN_DOMAINS[service ?? ""] ?? domain ?? "";
  const domainUrls = logoUrls(resolvedDomain);
  // Combine: try the known logo first, then domain-based fallbacks
  const urls = knownLogo ? [knownLogo, ...domainUrls] : domainUrls;

  const [urlIdx, setUrlIdx] = useState(0);
  const px = size === "sm" ? 24 : size === "lg" ? 40 : 32;
  const currentUrl = urls[urlIdx];

  if (currentUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={currentUrl} alt={label} onError={() => setUrlIdx((i) => i + 1)}
        width={px} height={px} className="object-contain rounded shrink-0" />
    );
  }
  const fb = CATEGORY_FALLBACK[category];
  const fbName = fb?.icon ?? "Puzzle";
  const fbColor = fb?.color ?? "text-muted-foreground";
  return <Icon name={fbName} size={px} className={`${fbColor} shrink-0`} />;
}

function ApiTile({ api, selected, onClick }: { api: ApiEntry; selected: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className={`text-left w-full p-4 rounded-xl border transition-all ${
        selected
          ? "border-primary bg-primary/5 ring-1 ring-primary/20"
          : "border-border bg-card hover:border-primary/40 hover:bg-secondary/30"
      }`}>
      <div className="flex items-start justify-between mb-3">
        <ServiceLogo service={api.service} label={api.label} domain={api.domain} category={api.category} />
        <span className={`w-2 h-2 mt-1 rounded-full shrink-0 ${api.configured ? "bg-success" : "bg-muted"}`} />
      </div>
      <div className="font-medium text-sm text-foreground leading-tight break-words">{api.label}</div>
      <div className="text-[10px] text-muted-foreground mt-0.5 capitalize">{api.category}</div>
      <div className={`mt-2 text-[10px] font-medium ${api.configured ? "text-success" : "text-muted-foreground"}`}>
        {api.configured ? "● Connected" : "○ Not set up"}
      </div>
    </button>
  );
}

const ZOHO_SERVICE = "zoho-crm";

/** Zoho has no key form here. The CRM app owns the one-click connect. */
function ZohoInCrm() {
  const router = useRouter();
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">Zoho connects in the CRM app.</p>
      <Button variant="primary" size="sm" icon="ExternalLink" onClick={() => router.push("/crm")}>
        Open CRM
      </Button>
    </div>
  );
}

function CredentialForm({ api, onSaved }: {
  api: {
    env_vars: { key: string; label: string; sensitive: boolean }[];
    // Read-only: the operator sets these on the server (security fix, 2026-10-05).
    operator_env_vars?: { key: string; label: string }[];
  };
  onSaved: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(api.env_vars.map((v) => [v.key, ""]))
  );
  const [saving, setSaving] = useState(false);
  const [done, setDone]     = useState(false);
  const [err, setErr]       = useState<string | null>(null);

  useEffect(() => {
    setValues(Object.fromEntries(api.env_vars.map((v) => [v.key, ""])));
    setErr(null); setDone(false);
  }, [api.env_vars]);

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      const r = await fetch("/api/integrations/configure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vars: Object.entries(values).filter(([, v]) => v.trim()).map(([key, value]) => ({ key, value })),
        }),
      });
      if (r.ok) { setDone(true); setTimeout(() => { setDone(false); onSaved(); }, 900); }
      else { const d = await r.json().catch(() => ({})); setErr(String(d.error ?? `Save failed (${r.status})`)); }
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  };

  return (
    <div className="space-y-3">
      {api.env_vars.map((v) => (
        <div key={v.key}>
          <label className="flex items-center gap-1 text-xs text-muted-foreground mb-1">
            {v.label}
            {v.sensitive && <Icon name="Lock" className="w-2.5 h-2.5 opacity-50" />}
          </label>
          <input type={v.sensitive ? "password" : "text"} autoComplete="off" spellCheck={false}
            value={values[v.key] ?? ""}
            onChange={(e) => setValues((p) => ({ ...p, [v.key]: e.target.value }))}
            placeholder={`Enter ${v.label}…`}
            className="w-full px-3 py-2 rounded-lg bg-secondary border border-border text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:border-primary transition-colors"
          />
          <div className="text-[9px] text-muted-foreground mt-0.5 font-mono">{v.key}</div>
        </div>
      ))}
      <OperatorEnvVars api={api} />
      {err && <p className="text-xs text-destructive bg-destructive/10 rounded-lg px-3 py-2">{err}</p>}
      <Button size="none" layout="flex items-center justify-center" onClick={() => void save()} disabled={saving} className="w-full py-2.5 text-sm gap-2">
        {saving ? <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" /> : done ? <Icon name="Check" className="w-3.5 h-3.5" /> : null}
        {saving ? "Saving…" : done ? "Saved!" : "Save credentials"}
      </Button>
    </div>
  );
}

// ⚠️ The ClickUp multi-account connector was REMOVED 2026-08-24 (D52, board
// WS-39 S1). Metorite is the project-management system of record; there is no
// ClickUp catalog entry left on the gateway either, so this panel had nothing
// to render. The WhatsApp connector below is the surviving example of the
// multi-account shape, which is the part worth keeping.

// ---------------------------------------------------------------------------
// WhatsApp — multi-account connector (wa_accounts, per-number encrypted tokens).
// Special-cased away from the env-var CredentialForm: connecting is a guided
// WIZARD in the app (Embedded Signup / live credential test), so this panel
// just LISTS the connected numbers, disconnects them, and links out to it.
// ---------------------------------------------------------------------------

interface WaAccountLite {
  id: string;
  display_name: string;
  phone_number: string;
  quality_rating?: string | null;
  is_default?: boolean;
}

function WhatsAppConnector({ onChange }: { onChange?: () => void }) {
  const [accounts, setAccounts] = useState<WaAccountLite[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/whatsapp/accounts");
      const d = r.ok ? await r.json() : [];
      setAccounts(Array.isArray(d) ? d : []);
    } catch {
      /* non-fatal */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const disconnect = async (id: string) => {
    if (
      !confirm(
        "Disconnect this WhatsApp number? Its chats stay stored, but no new " +
          "messages will sync until you reconnect.",
      )
    )
      return;
    setBusy(id);
    setErr(null);
    try {
      const r = await fetch(`/api/whatsapp/accounts/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error();
      await load();
      onChange?.();
    } catch {
      setErr("Disconnect failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1.5">
          Connected numbers
        </div>
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" /> Loading…
          </div>
        ) : accounts.length === 0 ? (
          <div className="rounded-lg border border-border p-3 text-xs text-muted-foreground">
            No WhatsApp numbers connected yet.
          </div>
        ) : (
          <div className="space-y-2">
            {accounts.map((a) => (
              <div
                key={a.id}
                className="flex items-center gap-3 rounded-lg border border-border p-2.5"
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[11px] font-bold text-emerald-500">
                  {(a.display_name || a.phone_number || "WA")
                    .slice(0, 2)
                    .toUpperCase()}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">
                    {a.display_name || a.phone_number}
                  </div>
                  <div className="truncate text-[11px] text-muted-foreground">
                    {a.phone_number}
                    {a.is_default ? " · default" : ""}
                    {a.quality_rating
                      ? ` · ${a.quality_rating.toLowerCase()}`
                      : ""}
                  </div>
                </div>
                <button
                  onClick={() => void disconnect(a.id)}
                  disabled={busy === a.id}
                  className="shrink-0 rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-red-500 disabled:opacity-40"
                >
                  {busy === a.id ? (
                    <Icon name="Loader2" className="w-3 h-3 animate-spin" />
                  ) : (
                    "Disconnect"
                  )}
                </button>
              </div>
            ))}
          </div>
        )}
        {err && <div className="mt-2 text-[11px] text-red-500">{err}</div>}
      </div>

      <a
        href="/whatsapp/connect"
        className="flex items-center justify-center gap-1.5 rounded-lg bg-primary py-2 text-xs font-medium text-primary-foreground hover:opacity-90"
      >
        <Icon name="Plus" className="w-3.5 h-3.5" /> Connect a number
      </a>
      <p className="text-[10.5px] text-muted-foreground/70">
        Connecting opens the guided flow in the WhatsApp app — link a personal
        number by QR scan, or connect a business number via the official Cloud
        API. Numbers are stored per-account, encrypted at rest.
      </p>
    </div>
  );
}

function ApiSidePanel({ api, agents, onClose, onRefresh }: {
  api: ApiEntry; agents: AgentEntry[]; onClose: () => void; onRefresh: () => void;
}) {
  const [mode, setMode]         = useState<"info" | "edit">(api.configured ? "info" : "edit");
  const [testing, setTesting]   = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string } | null>(null);

  useEffect(() => { setMode(api.configured ? "info" : "edit"); setTestResult(null); }, [api.service, api.configured]);

  const usedBy = agents.filter(
    (a) => a.integrations?.includes(api.service) || a.optional_integrations?.includes(api.service)
  );

  const runTest = async () => {
    setTesting(true); setTestResult(null);
    try {
      const r = await fetch(`/api/integrations/test?service=${encodeURIComponent(api.service)}`);
      const d = await r.json();
      setTestResult({ ok: d.ok, detail: d.detail });
    } catch (e) {
      setTestResult({ ok: false, detail: e instanceof Error ? e.message : String(e) });
    } finally { setTesting(false); }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-start justify-between p-5 border-b border-border shrink-0">
        <div className="flex items-start gap-3">
          <ServiceLogo service={api.service} label={api.label} domain={api.domain} category={api.category} size="lg" />
          <div>
            <div className="font-semibold text-foreground">{api.label}</div>
            <div className="text-xs text-muted-foreground capitalize mt-0.5">{api.category}</div>
            <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
              <span className={`w-1.5 h-1.5 rounded-full ${api.configured ? "bg-success" : "bg-muted"}`} />
              <span className={`text-xs ${api.configured ? "text-success" : "text-muted-foreground"}`}>
                {api.configured ? "Connected" : "Not configured"}
              </span>
              {api.storage === "encrypted-db" && (
                <span className="text-[9px] flex items-center gap-0.5 text-violet-400 bg-violet-500/10 border border-violet-500/20 px-1.5 py-0.5 rounded-full">
                  <Icon name="Lock" className="w-2 h-2" /> Encrypted
                </span>
              )}
            </div>
          </div>
        </div>
        <Button variant="text" size="icon-xs" radius="keep" layout="" onClick={onClose} className="rounded mt-0.5">
          <Icon name="X" className="w-4 h-4" />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-5 space-y-5">
        {api.description && <p className="text-sm text-muted-foreground leading-relaxed">{api.description}</p>}

        {usedBy.length > 0 && (
          <div>
            <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1.5">Used by agents</div>
            <div className="flex flex-wrap gap-1">
              {usedBy.map((a) => (
                <span key={a.name} className="text-[10px] px-1.5 py-0.5 rounded bg-secondary text-foreground border border-border">{a.name}</span>
              ))}
            </div>
          </div>
        )}

        {(api.setup_url || api.docs_url) && api.service !== ZOHO_SERVICE && (
          <div className="flex gap-2 flex-wrap">
            {api.setup_url && (
              <a href={api.setup_url} target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-primary/30 text-primary hover:bg-primary/5 transition-colors">
                Get credentials <Icon name="ExternalLink" className="w-3 h-3" />
              </a>
            )}
            {api.docs_url && (
              <a href={api.docs_url} target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-border text-muted-foreground hover:bg-secondary transition-colors">
                Docs <Icon name="ExternalLink" className="w-3 h-3" />
              </a>
            )}
          </div>
        )}

        {api.service === "github" ? (
          <GitHubAccountBadge integration={api} onRefresh={onRefresh} />
        ) : api.service === ZOHO_SERVICE ? (
          // Zoho connects inside the CRM app (D96). No customer OAuth app.
          <ZohoInCrm />
        ) : api.service === "whatsapp" ? (
          // WhatsApp is multi-account too (wa_accounts); connecting is a guided
          // wizard in the app, so the panel just lists + disconnects numbers.
          <WhatsAppConnector onChange={onRefresh} />
        ) : mode === "info" ? (
          <div className="space-y-4">
            {api.instructions && (
              <details>
                <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground list-none flex items-center gap-1">
                  <Icon name="ChevronRight" className="w-3 h-3 shrink-0" /> Setup instructions
                </summary>
                <pre className="mt-2 text-xs text-muted-foreground bg-secondary/60 rounded-lg p-3 whitespace-pre-wrap leading-relaxed">{api.instructions}</pre>
              </details>
            )}
            <div className="flex gap-2">
              <button onClick={() => void runTest()} disabled={testing || !api.configured}
                className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg border border-border text-xs text-muted-foreground hover:bg-secondary disabled:opacity-40 transition-colors">
                {testing ? <Icon name="Loader2" className="w-3 h-3 animate-spin" /> : <Icon name="RefreshCw" className="w-3 h-3" />}
                {testing ? "Testing…" : "Test"}
              </button>
              <Button size="none" layout="" onClick={() => setMode("edit")} className="flex-1 py-2 text-xs">
                {api.configured ? "Update" : "Configure"}
              </Button>
            </div>
            {testResult && (
              <div className={`text-xs rounded-lg px-3 py-2 flex items-start gap-2 ${testResult.ok ? "text-success bg-success/10 border border-success/20" : "text-destructive bg-destructive/10 border border-destructive/20"}`}>
                {testResult.ok ? <Icon name="Check" className="w-3.5 h-3.5 mt-0.5 shrink-0" /> : <Icon name="AlertCircle" className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
                {testResult.detail}
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            {api.instructions && (
              <details>
                <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground list-none flex items-center gap-1">
                  <Icon name="ChevronDown" className="w-3 h-3" /> Setup instructions
                </summary>
                <pre className="mt-2 text-xs text-muted-foreground bg-secondary/60 rounded-lg p-3 whitespace-pre-wrap leading-relaxed">{api.instructions}</pre>
              </details>
            )}
            <CredentialForm api={api} onSaved={() => { setMode("info"); onRefresh(); }} />
            {api.configured && (
              <button onClick={() => setMode("info")} className="w-full py-2 rounded-lg border border-border text-xs text-muted-foreground hover:bg-secondary transition-colors">Cancel</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

type DiscoverStep = "search" | "select" | "configure";

function DiscoverModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [q, setQ]               = useState("");
  const [step, setStep]         = useState<DiscoverStep>("search");
  const [discovering, setDisc]  = useState(false);
  const [results, setResults]   = useState<DiscoveredDef[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [savingDefs, setSavingDefs]   = useState(false);
  const [configQueue, setConfigQueue] = useState<DiscoveredDef[]>([]);
  const [configIdx, setConfigIdx]     = useState(0);
  const [creds, setCreds]       = useState<Record<string, string>>({});
  const [savingCreds, setSavingCreds] = useState(false);
  const [err, setErr]           = useState<string | null>(null);
  const inputRef                = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  useEffect(() => {
    if (configQueue[configIdx])
      setCreds(Object.fromEntries(configQueue[configIdx].env_vars.map((v) => [v.key, ""])));
  }, [configIdx, configQueue]);

  const discover = async () => {
    if (!q.trim()) return;
    setDisc(true); setErr(null);
    try {
      const r = await fetch("/api/integrations/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q.trim() }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error ?? `Error ${r.status}`);
      const apiResults: DiscoveredDef[] = d.results ?? (d.definition ? [d.definition] : []);
      if (!apiResults.length) throw new Error("No APIs found for that query.");
      setResults(apiResults);
      setSelected(apiResults.length === 1 ? new Set([apiResults[0].service_id]) : new Set());
      setStep("select");
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setDisc(false); }
  };

  const toggle = (id: string) =>
    setSelected((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const saveDefinitions = async () => {
    const toSave = results.filter((r) => selected.has(r.service_id));
    if (!toSave.length) return;
    setSavingDefs(true); setErr(null);
    try {
      await Promise.all(toSave.map((def) =>
        fetch("/api/integrations/custom", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(def),
        })
      ));
      setConfigQueue(toSave); setConfigIdx(0); setStep("configure");
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setSavingDefs(false); }
  };

  const advanceOrFinish = async (skip: boolean) => {
    if (!skip) {
      const vars = Object.entries(creds).filter(([, v]) => v.trim()).map(([key, value]) => ({ key, value }));
      if (vars.length > 0) {
        setSavingCreds(true);
        try {
          const r = await fetch("/api/integrations/configure", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ vars }),
          });
          if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.error ?? `Error ${r.status}`); }
        } catch (e) { setErr(e instanceof Error ? e.message : String(e)); setSavingCreds(false); return; }
        setSavingCreds(false);
      }
    }
    if (configIdx < configQueue.length - 1) { setConfigIdx((i) => i + 1); setErr(null); }
    else { onSaved(); onClose(); }
  };

  const current = configQueue[configIdx];

  return (
    <div className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center sm:p-4 bg-background/80 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-xl bg-card border-t sm:border border-border rounded-t-2xl sm:rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[92vh] sm:max-h-[90vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <Icon name="Sparkles" className="w-4 h-4 text-primary" />
            <span className="font-semibold text-foreground text-sm">
              {step === "search" && "Add API — AI Discovery"}
              {step === "select" && `${results.length} API${results.length !== 1 ? "s" : ""} found — select to add`}
              {step === "configure" && current && (
                <>Configure{configQueue.length > 1 ? ` (${configIdx + 1}/${configQueue.length})` : ""}: {current.label}</>
              )}
            </span>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground transition-colors"><Icon name="X" className="w-4 h-4" /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {step === "search" && (
            <>
              <p className="text-sm text-muted-foreground leading-relaxed">
                Type any service or company name. Company names like{" "}
                <strong className="text-foreground">Google</strong> or{" "}
                <strong className="text-foreground">Microsoft</strong> return all their major APIs.
              </p>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Icon name="Search" className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <input ref={inputRef} type="text" value={q}
                    onChange={(e) => setQ(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && void discover()}
                    placeholder="e.g. Google, Microsoft, Stripe, Notion…"
                    className="w-full pl-9 pr-3 py-2.5 rounded-lg bg-secondary border border-border text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:border-primary transition-colors"
                  />
                </div>
                <Button size="none" layout="flex items-center" onClick={() => void discover()} disabled={!q.trim() || discovering} className="px-4 py-2 text-sm gap-2 shrink-0">
                  {discovering ? <Icon name="Loader2" className="w-4 h-4 animate-spin" /> : <Icon name="Sparkles" className="w-4 h-4" />}
                  {discovering ? "Searching…" : "Discover"}
                </Button>
              </div>
              <div>
                <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-2">Try these</div>
                <div className="flex flex-wrap gap-1.5">
                  {DISCOVER_SUGGESTIONS.map((s) => (
                    <button key={s} onClick={() => setQ(s)}
                      className="text-xs px-2.5 py-1 rounded-full bg-secondary border border-border text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors">
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

          {step === "select" && (
            <>
              {results.length > 1 && (
                <p className="text-sm text-muted-foreground">Select the APIs you want to add.</p>
              )}
              <div className={`grid gap-3 ${results.length === 1 ? "grid-cols-1" : "grid-cols-1 sm:grid-cols-2"}`}>
                {results.map((r) => {
                  const isSel = selected.has(r.service_id);
                  return (
                    <button key={r.service_id} onClick={() => toggle(r.service_id)}
                      className={`text-left p-3 rounded-xl border transition-all ${isSel ? "border-primary bg-primary/5 ring-1 ring-primary/20" : "border-border hover:border-primary/40 hover:bg-secondary/30"}`}>
                      <div className="flex items-start gap-2.5">
                        <ServiceLogo service={r.service_id} label={r.label} domain={r.domain} category={r.category} size="sm" />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="font-medium text-sm text-foreground">{r.label}</span>
                            {isSel && <Icon name="Check" className="w-3 h-3 text-primary shrink-0" />}
                          </div>
                          <div className="text-[10px] text-muted-foreground capitalize mb-1">{r.category}</div>
                          <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">{r.description}</p>
                          <div className="flex flex-wrap gap-1 mt-1.5">
                            {r.env_vars.slice(0, 3).map((v) => (
                              <span key={v.key} className="text-[9px] font-mono px-1 py-0.5 rounded bg-secondary border border-border text-muted-foreground">{v.key}</span>
                            ))}
                            {r.env_vars.length > 3 && <span className="text-[9px] text-muted-foreground">+{r.env_vars.length - 3}</span>}
                          </div>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
              <div className="flex gap-2 pt-1">
                <button onClick={() => { setStep("search"); setResults([]); setSelected(new Set()); }}
                  className="px-4 py-2 rounded-lg border border-border text-sm text-muted-foreground hover:bg-secondary transition-colors">
                  ← Back
                </button>
                <Button size="none" layout="flex items-center justify-center" onClick={() => void saveDefinitions()} disabled={selected.size === 0 || savingDefs} className="flex-1 py-2 text-sm gap-2">
                  {savingDefs ? <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" /> : <Icon name="ArrowRight" className="w-3.5 h-3.5" />}
                  {savingDefs ? "Adding…" : `Add ${selected.size} API${selected.size !== 1 ? "s" : ""}`}
                </Button>
              </div>
            </>
          )}

          {step === "configure" && current && (
            <>
              {configQueue.length > 1 && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Progress</span>
                  <div className="flex gap-1">
                    {configQueue.map((d, i) => (
                      <div key={d.service_id}
                        className={`w-2 h-2 rounded-full transition-colors ${i < configIdx ? "bg-success" : i === configIdx ? "bg-primary" : "bg-muted"}`}
                        title={d.label} />
                    ))}
                  </div>
                  <span className="text-xs text-muted-foreground">{configIdx + 1}/{configQueue.length}</span>
                </div>
              )}
              <div className="flex items-start gap-3">
                <ServiceLogo service={current.service_id} label={current.label} domain={current.domain} category={current.category} size="lg" />
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-foreground">{current.label}</div>
                  <div className="text-xs text-muted-foreground capitalize">{current.category}</div>
                  {current.description && <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{current.description}</p>}
                </div>
              </div>
              {current.instructions && (
                <details>
                  <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground list-none flex items-center gap-1">
                    <Icon name="ChevronRight" className="w-3 h-3" /> Setup instructions
                  </summary>
                  <pre className="mt-2 text-xs text-muted-foreground bg-secondary/60 rounded-lg p-3 whitespace-pre-wrap leading-relaxed">{current.instructions}</pre>
                </details>
              )}
              {(current.setup_url || current.docs_url) && (
                <div className="flex gap-3 flex-wrap">
                  {current.setup_url && (
                    <a href={current.setup_url} target="_blank" rel="noopener noreferrer"
                      className="flex items-center gap-1 text-xs text-primary hover:opacity-80 transition-colors">
                      Get credentials <Icon name="ExternalLink" className="w-3 h-3" />
                    </a>
                  )}
                  {current.docs_url && (
                    <a href={current.docs_url} target="_blank" rel="noopener noreferrer"
                      className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
                      API docs <Icon name="ExternalLink" className="w-3 h-3" />
                    </a>
                  )}
                </div>
              )}
              {current.env_vars.map((v) => (
                <div key={v.key}>
                  <label className="flex items-center gap-1 text-xs text-muted-foreground mb-1">
                    {v.label} {v.sensitive && <Icon name="Lock" className="w-2.5 h-2.5 opacity-50" />}
                  </label>
                  <input type={v.sensitive ? "password" : "text"} autoComplete="off"
                    value={creds[v.key] ?? ""}
                    onChange={(e) => setCreds((p) => ({ ...p, [v.key]: e.target.value }))}
                    placeholder={`Enter ${v.label}…`}
                    className="w-full px-3 py-2 rounded-lg bg-secondary border border-border text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:border-primary transition-colors"
                  />
                </div>
              ))}
              <div className="flex gap-2 pt-1">
                <button onClick={() => void advanceOrFinish(true)}
                  className="px-4 py-2 rounded-lg border border-border text-sm text-muted-foreground hover:bg-secondary transition-colors">
                  {configIdx < configQueue.length - 1 ? "Skip →" : "Finish"}
                </button>
                <Button size="none" layout="" onClick={() => void advanceOrFinish(false)} disabled={savingCreds} className="flex-1 py-2 text-sm">
                  {savingCreds ? "Saving…" : configIdx < configQueue.length - 1 ? "Save & next →" : "Save & finish"}
                </Button>
              </div>
            </>
          )}

          {err && (
            <p className="text-xs text-destructive bg-destructive/10 rounded-lg px-3 py-2 flex items-start gap-2">
              <Icon name="AlertCircle" className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {err}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ApiKeysView() {
  const [apis, setApis]       = useState<ApiEntry[]>([]);
  const [agents, setAgents]   = useState<AgentEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [cat, setCat]         = useState<CatId>("all");
  const [search, setSearch]   = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [showDiscover, setShowDiscover] = useState(false);
  const hasFetched = useRef(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      // WhatsApp is multi-account (its "connected" state comes from
      // wa_accounts, NOT provider_keys) — override it from the real count.
      const [sRes, aRes, waRes, cRes] = await Promise.all([
        fetch("/api/integrations/status"),
        fetch("/api/agent/list"),
        fetch("/api/whatsapp/accounts").catch(() => null),
        fetch("/api/integrations/connections").catch(() => null),
      ]);
      const [sData, aData] = await Promise.all([sRes.json(), aRes.json()]);
      let whatsappCount = 0;
      if (waRes && waRes.ok) {
        const accts = await waRes.json().catch(() => []);
        whatsappCount = Array.isArray(accts) ? accts.length : 0;
      }
      // The card reads the org's own key rows. Only a 404 (the read model is
      // not built yet) falls back to the deployment-wide status.
      let orgRows: Connection[] | null = null;
      if (cRes && cRes.ok) {
        const body = await cRes.json().catch(() => ({}));
        orgRows = Array.isArray(body?.connections) ? body.connections : [];
      }
      setApis(
        (Array.isArray(sData) ? sData : []).map((a: ApiEntry) => {
          const base = { ...a, category: a.category ?? "custom" };
          if (a.service === "whatsapp")
            return { ...base, configured: whatsappCount > 0 };
          return { ...base, configured: orgKeyConfigured(orgRows, a.service, a.configured) };
        }),
      );
      setAgents(Array.isArray(aData) ? aData : []);
    } catch { /* non-fatal */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (!hasFetched.current) { hasFetched.current = true; void fetchAll(); }
  }, [fetchAll]);

  useEffect(() => {
    const isDesktop = typeof window !== "undefined" && window.innerWidth >= 640;
    if (!loading && apis.length > 0 && !selected && isDesktop)
      setSelected((apis.find((a) => !a.configured) ?? apis[0]).service);
  }, [loading, apis, selected]);

  const filtered = apis.filter((a) => {
    if (cat !== "all" && a.category !== cat) return false;
    if (search && !a.label.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const selectedApi = selected ? (apis.find((a) => a.service === selected) ?? null) : null;
  const connectedCount = apis.filter((a) => a.configured).length;
  const catCounts: Record<string, number> = { all: apis.length };
  for (const a of apis) catCounts[a.category] = (catCounts[a.category] ?? 0) + 1;

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-4 py-2 border-b border-border shrink-0">
        <p className="text-xs text-muted-foreground">
          {loading ? "Loading…" : `${connectedCount} of ${apis.length} connected`}
        </p>
        <div className="flex items-center gap-2 ml-auto">
          <Button variant="ghost" size="icon-sm" icon="RefreshCw" aria-label="Refresh" title="Refresh" onClick={() => void fetchAll()} />
        </div>
      </div>

      {/* Category pills + search */}
      <div className="flex items-center border-b border-border shrink-0">
        <FilterPills
          className="flex-1 min-w-0 border-b-0"
          items={CATEGORIES.filter((c) => c.id === "all" || (catCounts[c.id] ?? 0) > 0).map((c) => ({
            id: c.id,
            label: c.label,
            count: c.id === "all" ? undefined : catCounts[c.id],
          }))}
          activeId={cat}
          onChange={(id) => setCat(id as CatId)}
        />
        <div className="shrink-0 pr-4">
          <Input icon="Search" inputSize="sm" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search…" className="w-28 sm:w-36" />
        </div>
      </div>

      {/* Grid + Side panel */}
      <div className="flex flex-1 overflow-hidden relative">
        <div className={`flex-1 p-4 overflow-y-auto ${selectedApi ? "sm:min-w-0" : ""}`}>
          {loading ? (
            <div className="flex items-center justify-center h-48 gap-3 text-muted-foreground text-sm">
              <Icon name="Loader2" className="w-4 h-4 animate-spin" /> Loading API connections…
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-3 text-muted-foreground text-sm">
              <p>No APIs found{search ? ` for "${search}"` : ""}.</p>
              <button onClick={() => setShowDiscover(true)} className="flex items-center gap-1.5 text-xs text-primary hover:opacity-80">
                <Icon name="Plus" className="w-3.5 h-3.5" /> Add a new API
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
              {filtered.map((api) => (
                <ApiTile key={api.service} api={api} selected={selected === api.service} onClick={() => setSelected(api.service)} />
              ))}
              <button onClick={() => setShowDiscover(true)}
                className="p-4 rounded-xl border-2 border-dashed border-border text-muted-foreground hover:border-primary/40 hover:text-primary hover:bg-primary/5 transition-all flex flex-col items-center justify-center gap-2 min-h-[120px]">
                <Icon name="Plus" className="w-5 h-5" /><span className="text-xs">Add API</span>
              </button>
            </div>
          )}
        </div>

        {selectedApi && (
          <>
            <div className="sm:hidden fixed inset-0 z-[60] pointer-events-none">
              <div className="absolute inset-0 bg-black/50 pointer-events-auto" onClick={() => setSelected(null)} />
              <aside className="absolute inset-x-0 bottom-14 pointer-events-auto flex max-h-[55%] flex-col rounded-t-2xl border-t border-border bg-card shadow-2xl chat-fade-in">
                <div className="flex items-center justify-between px-4 py-2 border-b border-border shrink-0">
                  <div className="flex items-center gap-2">
                    <ServiceLogo service={selectedApi.service} label={selectedApi.label ?? selectedApi.service} category={selectedApi.category} size="sm" />
                    <span className="text-sm font-semibold">{selectedApi.label ?? selectedApi.service}</span>
                  </div>
                  <button onClick={() => setSelected(null)} className="p-1 rounded-md hover:bg-secondary text-muted-foreground">
                    <Icon name="X" size={16} />
                  </button>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto">
                  <ApiSidePanel api={selectedApi} agents={agents} onClose={() => setSelected(null)} onRefresh={() => void fetchAll()} />
                </div>
              </aside>
            </div>
            <div className="hidden sm:flex w-[380px] border-l border-border bg-card shrink-0 flex-col overflow-hidden">
              <ApiSidePanel api={selectedApi} agents={agents} onClose={() => setSelected(null)} onRefresh={() => void fetchAll()} />
            </div>
          </>
        )}
      </div>

      {showDiscover && <DiscoverModal onClose={() => setShowDiscover(false)} onSaved={() => void fetchAll()} />}
    </div>
  );
}

