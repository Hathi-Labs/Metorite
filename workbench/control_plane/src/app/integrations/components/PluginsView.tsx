"use client";

/** The Plugins view (moved from the old Plugins tab, no behaviour change). */

import Button from "@/components/ui/Button";
import Icon, { themedIcon } from "@/components/Icon";
import React, { useCallback, useEffect, useRef, useState } from "react";

// ===========================================================================
// PLUGINS TAB — Claude-style plugin registry (coming soon)
// ===========================================================================

const PLUGIN_EXAMPLES = [
  { name: "Web Browsing",      icon: themedIcon("Globe"),    desc: "Agents can open URLs, read pages, and extract structured data from the live web." },
  { name: "Code Interpreter",  icon: themedIcon("Box"),      desc: "Run sandboxed Python/JS and return computed results, charts, or transformed data." },
  { name: "Document Analysis", icon: themedIcon("HardDrive"), desc: "Upload PDFs, spreadsheets, or images and let agents extract and reason over content." },
  { name: "Email Actions",     icon: themedIcon("Mail"),     desc: "Draft, send, and thread emails directly from agent workflows without extra API setup." },
  { name: "Calendar & Booking",icon: themedIcon("CheckSquare"), desc: "Check availability, schedule meetings, and create calendar events autonomously." },
  { name: "Community Plugins", icon: themedIcon("Users"),    desc: "Install third-party plugins published by the Metorite community or build your own." },
];

export default function PluginsView() {
  const [plugins, setPlugins] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showInstall, setShowInstall] = useState(false);
  const hasFetched = useRef(false);

  const fetchPlugins = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/integrations/plugins");
      if (r.ok) setPlugins(await r.json());
    } catch { /* non-fatal */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (!hasFetched.current) { hasFetched.current = true; void fetchPlugins(); }
  }, [fetchPlugins]);

  const handleDelete = async (id: string) => {
    try {
      await fetch(`/api/integrations/plugins/${encodeURIComponent(id)}`, { method: "DELETE" });
      setPlugins((p) => p.filter((pl) => pl.id !== id));
    } catch { /* non-fatal */ }
  };

  return (
    <div className="flex flex-col flex-1 overflow-y-auto p-6 gap-6">
      {/* Hero */}
      <div className="flex items-start gap-4 p-5 rounded-2xl border border-border bg-card">
        <div className="w-10 h-10 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
          <Icon name="Puzzle" className="w-5 h-5 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="font-semibold text-foreground">Claude-style Plugins</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                {loading ? "Loading…" : `${plugins.length} plugin${plugins.length !== 1 ? "s" : ""} installed`}
              </p>
            </div>
            <Button layout="flex items-center" onClick={() => setShowInstall(true)} className="shrink-0">
              <Icon name="Plus" className="w-3.5 h-3.5" /> Install Plugin
            </Button>
          </div>
          <p className="text-sm text-muted-foreground mt-2 leading-relaxed max-w-xl">
            Plugins are self-describing tool packages — each one ships a manifest, an OpenAPI spec, and auth config.
            Install from any URL and agents auto-discover what the plugin can do.
          </p>
        </div>
      </div>

      {/* Installed plugins */}
      {plugins.length > 0 ? (
        <div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-3">Installed plugins</div>
          <div className="space-y-2">
            {plugins.map((p) => (
              <div key={p.id} className="p-4 rounded-xl border border-border bg-card hover:bg-secondary/10 transition-colors">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-sm text-foreground">{p.label || p.name}</span>
                      <span className="text-[10px] font-mono text-muted-foreground">{p.name}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary text-muted-foreground">v{p.version}</span>
                      {p.auth_type !== "none" && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-cat-12/10 text-cat-12 border border-cat-12/20">
                          {p.auth_type}
                        </span>
                      )}
                      {(p.tools_generated || []).length > 0 && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-success/10 text-success border border-success/20">
                          {(p.tools_generated || []).length} tool{(p.tools_generated || []).length !== 1 ? "s" : ""}
                        </span>
                      )}
                    </div>
                    {p.description && <p className="text-xs text-muted-foreground mt-1">{p.description}</p>}
                    <div className="text-[10px] font-mono text-muted-foreground mt-1 truncate">{p.manifest_url}</div>
                  </div>
                  <button onClick={() => handleDelete(p.id)}
                    className="px-2 py-1 rounded text-[10px] border border-border text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors shrink-0">
                    <Icon name="X" className="w-3 h-3" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : !loading ? (
        <div className="flex flex-col items-center justify-center gap-3 py-10 text-center">
          <div className="w-12 h-12 rounded-2xl bg-secondary border border-border flex items-center justify-center">
            <Icon name="Puzzle" className="w-6 h-6 text-muted-foreground" />
          </div>
          <div>
            <div className="text-sm font-medium text-foreground">No plugins installed</div>
            <div className="text-xs text-muted-foreground mt-0.5">Install a plugin from a manifest URL to give agents new SaaS capabilities.</div>
          </div>
          <Button size="none" layout="flex items-center" onClick={() => setShowInstall(true)} className="gap-1.5 px-4 py-2 text-xs">
            <Icon name="Plus" className="w-3.5 h-3.5" /> Install Plugin
          </Button>
        </div>
      ) : null}

      {/* Plugin capabilities grid */}
      <div>
        <div className="text-[10px] text-muted-foreground uppercase tracking-wider mb-3">Capabilities plugins unlock</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {PLUGIN_EXAMPLES.map((p) => (
            <div key={p.name} className="p-4 rounded-xl border border-border bg-card hover:bg-secondary/30 transition-colors">
              <div className="flex items-center gap-2 mb-2">
                <p.icon className="w-4 h-4 text-primary shrink-0" />
                <span className="text-xs font-semibold text-foreground">{p.name}</span>
              </div>
              <p className="text-xs text-muted-foreground leading-relaxed">{p.desc}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Plugin vs MCP */}
      <div className="p-5 rounded-2xl border border-info/20 bg-info/5 space-y-2">
        <div className="text-[10px] text-info uppercase tracking-wider font-semibold">Plugins vs MCPs — when to use which</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs text-muted-foreground">
          <div>
            <div className="text-foreground font-medium mb-1">Use a Plugin when…</div>
            <ul className="space-y-1 list-disc list-inside">
              <li>The tool has a REST API and an OpenAPI spec</li>
              <li>You want OAuth or per-user auth flows</li>
              <li>You want to share / distribute the integration</li>
            </ul>
          </div>
          <div>
            <div className="text-foreground font-medium mb-1">Use an MCP server when…</div>
            <ul className="space-y-1 list-disc list-inside">
              <li>You control the server and prefer the MCP protocol</li>
              <li>The tool needs streaming or binary data transport</li>
              <li>You want tight integration with local infrastructure</li>
            </ul>
          </div>
        </div>
      </div>

      {/* Install Plugin modal */}
      {showInstall && <InstallPluginModal onClose={() => setShowInstall(false)} onInstalled={() => { setShowInstall(false); void fetchPlugins(); }} />}
    </div>
  );
}

// ── Install Plugin modal ─────────────────────────────────────────────────

function InstallPluginModal({ onClose, onInstalled }: { onClose: () => void; onInstalled: () => void }) {
  const [manifestUrl, setManifestUrl] = useState("");
  const [installing, setInstalling] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const install = async () => {
    if (!manifestUrl.trim()) return;
    setInstalling(true); setErr(null); setResult(null);
    try {
      const r = await fetch("/api/integrations/plugins", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ manifest_url: manifestUrl.trim() }),
      });
      const d = await r.json();
      if (r.ok) { setResult(d); onInstalled(); }
      else { setErr(d.detail || d.error || `Error ${r.status}`); }
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setInstalling(false); }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center sm:p-4 bg-background/80 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-md bg-card border-t sm:border border-border rounded-t-2xl sm:rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[92vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <div className="font-semibold text-sm">Install Plugin</div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><Icon name="X" className="w-4 h-4" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {result ? (
            <div className="p-4 rounded-xl border border-success/20 bg-success/5 space-y-2">
              <div className="flex items-center gap-2">
                <Icon name="Check" className="w-4 h-4 text-success" />
                <span className="text-sm font-medium text-foreground">Installed: {result.label}</span>
              </div>
              <div className="text-xs text-muted-foreground">
                Plugin <span className="font-mono">{result.name}</span> registered with {result.tools_count} tool{result.tools_count !== 1 ? "s" : ""}.
                {result.auth_type !== "none" && <> Auth type: {result.auth_type}.</>}
              </div>
              <Button size="none" layout="" onClick={onClose} className="w-full py-2 text-sm">Done</Button>
            </div>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">Paste the URL of a plugin manifest (ai-plugin.json). The OpenAPI spec will be auto-fetched and tools generated.</p>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Manifest URL <span className="text-destructive">*</span></label>
                <input ref={inputRef} type="text" value={manifestUrl}
                  onChange={(e) => setManifestUrl(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void install()}
                  placeholder="https://my-plugin.example.com/.well-known/ai-plugin.json"
                  className="w-full px-3 py-2 rounded-lg bg-secondary border border-border text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:border-primary font-mono" />
              </div>
              {err && <p className="text-xs text-destructive bg-destructive/10 rounded-lg px-3 py-2 flex items-start gap-2"><Icon name="AlertCircle" className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {err}</p>}
              <div className="flex gap-2 pt-1">
                <button onClick={onClose} className="flex-1 py-2 rounded-lg border border-border text-sm text-muted-foreground hover:bg-secondary transition-colors">Cancel</button>
                <Button size="none" layout="flex items-center justify-center" onClick={install} disabled={!manifestUrl.trim() || installing} className="flex-1 py-2 text-sm gap-2">
                  {installing ? <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" /> : <Icon name="Plus" className="w-3.5 h-3.5" />}
                  {installing ? "Installing…" : "Install"}
                </Button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

