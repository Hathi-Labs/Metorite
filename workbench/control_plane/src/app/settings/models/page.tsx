"use client";

/**
 * /settings/models — LLM Model Configuration
 *
 * Three-tab layout:
 *   1. Providers — compact tiles, click → slide-in side panel for key setup
 *   2. Models — all models from configured providers, ALL start hidden,
 *      click eye icon to enable in LiteLLM. Filter by provider.
 *   3. Tiers — compact rows, expand to edit model assignment
 */

import Button from "@/components/ui/Button";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { LLMConfig, ProviderInfo, ModelInfo, TierInfo } from "@/lib/model-types";
import { PROVIDER_GUIDES, PROVIDER_COLOURS, PROVIDER_ICONS } from "@/lib/model-types";
import Tabs from "@/components/Tabs";

// ── Icons ───────────────────────────────────────────────────────────────────

const EyeIcon = () => (<svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>);
const EyeOffIcon = () => (<svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" /><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" /><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24" /><line x1="1" y1="1" x2="23" y2="23" /></svg>);
const SearchIcon = () => (<svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5L14 14" /></svg>);
const ChevronRight = () => (<svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M6 3l5 5-5 5" /></svg>);

// ── Tier definitions ────────────────────────────────────────────────────────

const ALL_TIERS = [
  { id: "fast",       label: "Fast",      desc: "Quick responses for simple queries",              icon: "⚡", cat: "chat" },
  { id: "balanced",   label: "Balanced",  desc: "Best speed/capability balance for daily work",     icon: "⚖️", cat: "chat" },
  { id: "powerful",   label: "Powerful",  desc: "Maximum capability for complex reasoning",         icon: "🧠", cat: "chat" },
  { id: "embeddings", label: "Embeddings",desc: "Text embeddings for semantic search",              icon: "📝", cat: "embed" },
  { id: "vision",     label: "Vision",    desc: "Image understanding and multimodal inputs",        icon: "👁", cat: "vision" },
  { id: "tts",        label: "TTS",       desc: "Voice output for audio responses",                 icon: "🎤", cat: "tts" },
  { id: "stt",        label: "STT",       desc: "Transcribe voice input to text",                   icon: "🎧", cat: "stt" },
];

const STATIC_LABELS: Record<string, string> = {
  gemini: "Google Gemini", openai: "OpenAI", anthropic: "Anthropic",
  openrouter: "OpenRouter", github: "GitHub Copilot", groq: "Groq",
  mistral: "Mistral AI", together: "Together AI", deepseek: "DeepSeek",
  deepgram: "Deepgram (speech-to-text)", assemblyai: "AssemblyAI (speech-to-text)",
};
const STATIC_ENV: Record<string, string> = {
  gemini: "GEMINI_API_KEY", openai: "OPENAI_API_KEY", anthropic: "ANTHROPIC_API_KEY",
  openrouter: "OPENROUTER_API_KEY", github: "GITHUB_TOKEN", groq: "GROQ_API_KEY",
  deepseek: "DEEPSEEK_API_KEY", mistral: "MISTRAL_API_KEY", together: "TOGETHER_API_KEY",
  deepgram: "DEEPGRAM_API_KEY", assemblyai: "ASSEMBLYAI_API_KEY",
};

/** Tier metadata by tier_name ("tier-fast"/"tier-stt") → ALL_TIERS entry. */
const tierMeta = (tierName: string) =>
  ALL_TIERS.find((t) => t.id === tierName.replace(/^tier-/, ""));

// ── Page ────────────────────────────────────────────────────────────────────

export default function ModelsPage() {
  const [tab, setTab] = useState<"providers" | "models" | "tiers">("providers");
  const [config, setConfig] = useState<LLMConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [copilotScopeOk, setCopilotScopeOk] = useState<boolean | null>(null);

  // ── Tab 1: Provider selection ────────────────────────────────────────────
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [providerSearch, setProviderSearch] = useState("");
  const [providerFilter, setProvFilter] = useState<"all" | "connected" | "unset">("all");

  // ── Tab 2: Models — ALL models start disabled. Click for detail panel. ─
  const [providerModels, setProviderModels] = useState<Map<string, ModelInfo[]>>(new Map());
  const [enabledIds, setEnabledIds] = useState<Set<string>>(new Set());
  const [loadingModels, setLoadingModels] = useState(true);
  const [modelSearch, setModelSearch] = useState("");
  const [modelProvFilter, setModelProvFilter] = useState<string>("all");
  const [busyModel, setBusyModel] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<ModelInfo | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [cacheInfo, setCacheInfo] = useState<Record<string, string | null>>({});
  // lastSynced: most recent fetched_at across all cached providers
  const [lastSynced, setLastSynced] = useState<string | null>(null);

  // ── Tab 3: Editing tier ──────────────────────────────────────────────────
  const [editingTier, setEditingTier] = useState<string | null>(null);
  const [editModel, setEditModel] = useState("");
  const [editApiBase, setEditApiBase] = useState("");
  const [editProvider, setEditProvider] = useState("");
  const [savingTier, setSavingTier] = useState(false);
  const [testingTier, setTestingTier] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string; ms: number; model: string } | null>(null);
  const [testedTier, setTestedTier] = useState<string | null>(null);  // which tier the result is for

  // ── Load config ──────────────────────────────────────────────────────────
  const loadConfig = useCallback(async () => {
    setLoading(true); setLoadError(null);
    try {
      const [cfgRes, modelsRes] = await Promise.all([fetch("/api/settings/llm"), fetch("/api/models")]);
      if (cfgRes.ok) setConfig(await cfgRes.json());
      else { const err = await cfgRes.json().catch(() => ({})); setLoadError(String(err?.detail ?? err?.error ?? `Error ${cfgRes.status}`)); }
      if (modelsRes.ok) { const d = await modelsRes.json(); setCopilotScopeOk(d.copilot_scope_ok ?? null); }
    } catch (err) { setLoadError(String(err)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { loadConfig(); }, [loadConfig]);

  // ── Derived: merged providers ────────────────────────────────────────────
  const mergedProviders = useMemo(() => {
    if (!config) return [] as ProviderInfo[];
    const gatewayIds = new Set(config.providers.map((p) => p.id));
    const extra: ProviderInfo[] = (Object.keys(PROVIDER_GUIDES) as Array<keyof typeof PROVIDER_GUIDES>)
      .filter((id) => !gatewayIds.has(id as string))
      .map((id) => ({ id: id as string, label: STATIC_LABELS[id as string] ?? (id as string), configured: false, env_var: STATIC_ENV[id as string] ?? "", models: [] }));
    return [...config.providers, ...extra];
  }, [config]);

  const configuredProviderIds = useMemo(() => mergedProviders.filter((p) => p.configured).map((p) => p.id), [mergedProviders]);
  const connCount = mergedProviders.filter((p) => p.configured).length;

  // ── Tab 2: Load models from EACH configured provider ─────────────────────
  // Load cache-info (last-synced timestamps)
  const loadCacheInfo = useCallback(async () => {
    try {
      const r = await fetch("/api/settings/llm/provider-models/refresh");
      if (r.ok) {
        const data: { provider: string; fetched_at: string | null; count: number; fresh: boolean }[] = await r.json();
        const map: Record<string, string | null> = {};
        let newest: string | null = null;
        data.forEach((d) => {
          map[d.provider] = d.fetched_at;
          if (d.fetched_at && (!newest || d.fetched_at > newest)) newest = d.fetched_at;
        });
        setCacheInfo(map);
        setLastSynced(newest);
      }
    } catch { /* ok */ }
  }, []);

  const loadModelsTab = useCallback(async () => {
    const configured = configuredProviderIds;
    if (configured.length === 0) { setProviderModels(new Map()); setEnabledIds(new Set()); setLoadingModels(false); return; }
    setLoadingModels(true);
    try {
      const providerFetches = configured.map(async (pid) => {
        try {
          const r = await fetch(`/api/settings/llm/provider-models?provider=${encodeURIComponent(pid)}`);
          if (r.ok) {
            const data: ModelInfo[] = await r.json();
            return { provider: pid, models: Array.isArray(data) ? data : [] };
          }
        } catch { /* skip */ }
        return { provider: pid, models: [] as ModelInfo[] };
      });
      const results = await Promise.all(providerFetches);
      const newMap = new Map<string, ModelInfo[]>();
      results.forEach(({ provider, models }) => newMap.set(provider, models));

      let enabled = new Set<string>();
      try {
        const custRes = await fetch("/api/settings/llm/enabled-models");
        if (custRes.ok) {
          const custData = await custRes.json();
          const list = Array.isArray(custData) ? custData : ((custData as { custom?: { id: string }[] }).custom ?? []);
          enabled = new Set(list.map((m: { id: string }) => m.id));
        }
      } catch { /* ok */ }

      setProviderModels(newMap);
      setEnabledIds(enabled);
    } catch { /* ok */ } finally { setLoadingModels(false); }
  }, [configuredProviderIds]);
  useEffect(() => { if (tab === "models" || tab === "tiers") { loadModelsTab(); } if (tab === "models") loadCacheInfo(); }, [tab, loadModelsTab, loadCacheInfo]);

  // ── Tab 2: Refresh models from live provider APIs ───────────────────────
  const handleRefreshModels = useCallback(async () => {
    setRefreshing(true);
    try {
      // Refresh only configured providers
      const body = configuredProviderIds.length > 0 ? { providers: configuredProviderIds } : {};
      await fetch("/api/settings/llm/provider-models/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      await loadModelsTab();
      await loadCacheInfo();
    } finally { setRefreshing(false); }
  }, [configuredProviderIds, loadModelsTab, loadCacheInfo]);

  // ── Tab 2: Toggle model enabled/disabled (───────────────────────────────────────
  const toggleEnabled = async (m: ModelInfo) => {
    setBusyModel(m.id);
    try {
      if (enabledIds.has(m.id)) {
        await fetch(`/api/settings/llm/enabled-models/${encodeURIComponent(m.id)}`, { method: "DELETE" });
        setEnabledIds((prev) => { const s = new Set(prev); s.delete(m.id); return s; });
      } else {
        await fetch("/api/settings/llm/enabled-models", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: m.id, label: m.label, provider: m.provider }),
        });
        setEnabledIds((prev) => new Set([...prev, m.id]));
      }
    } finally { setBusyModel(null); }
  };

  // ── Handlers (providers, tiers) ──────────────────────────────────────────
  const handleKeySet = async (prov: string, key: string) => {
    const res = await fetch("/api/settings/llm/key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: prov, api_key: key }) });
    if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(String(err?.detail ?? "Save failed")); }
    await new Promise((r) => setTimeout(r, 2000));
    await loadConfig();
    setSelectedProvider(null);
  };
  const handleKeyDiscard = async (prov: string) => {
    const res = await fetch(`/api/settings/llm/key?provider=${encodeURIComponent(prov)}`, { method: "DELETE" });
    if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(String(err?.detail ?? "Discard failed")); }
    await new Promise((r) => setTimeout(r, 2000));
    await loadConfig();
  };
  const handleSaveTier = async (tierName: string, model: string, apiBase?: string) => {
    setSavingTier(true);
    const res = await fetch("/api/settings/llm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tier_name: tierName, model, api_base: apiBase ?? null }) });
    const data = await res.json();
    if (!res.ok) { setSaveMsg({ ok: false, text: String(data?.detail ?? data?.error ?? "Save failed") }); }
    else { setSaveMsg({ ok: true, text: `${tierName} → ${model}` }); setEditingTier(null); await loadConfig(); setTimeout(() => setSaveMsg(null), 4000); }
    setSavingTier(false);
  };
  const handleTestTier = async (tierName: string) => {
    setTestingTier(tierName); setTestResult(null); setTestedTier(tierName);
    try {
      const res = await fetch("/api/settings/llm/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tier_name: tierName }) });
      const data = await res.json();
      setTestResult({ ok: data.success, text: data.response, ms: data.latency_ms, model: data.model ?? "" });
    } catch (err) { setTestResult({ ok: false, text: String(err), ms: 0, model: "" }); }
    finally { setTestingTier(null); }
  };

  // ── Tab 2: Derived model lists ───────────────────────────────────────────
  const mq = modelSearch.trim().toLowerCase();
  const allProviderModels = useMemo(() => {
    const result: ModelInfo[] = [];
    providerModels.forEach((models) => { models.forEach((m) => result.push(m)); });
    return result;
  }, [providerModels]);

  // All enabled models (unfiltered) — used by the Tiers tab for assignment.
  // Excludes GitHub Copilot models: they run via the Copilot SDK, not LiteLLM
  // tier routing, so a bare Copilot id (e.g. "claude-sonnet-4.5") isn't a
  // routable tier model.  They remain enable-able in the Models tab for the
  // chat picker.
  const allEnabledModels = useMemo(() => allProviderModels.filter((m) => enabledIds.has(m.id) && m.provider !== "github"), [allProviderModels, enabledIds]);
  // Chat tiers route through completions — never a transcription model.
  // The STT tier is the opposite: only transcription models can be assigned.
  const chatEnabledModels = useMemo(() => allEnabledModels.filter((m) => !m.transcription), [allEnabledModels]);
  const sttEnabledModels = useMemo(() => allEnabledModels.filter((m) => m.transcription), [allEnabledModels]);
  // Enabled models filtered for the Models tab display
  const enabledModels = allProviderModels.filter((m) => enabledIds.has(m.id) && (modelProvFilter === "all" || m.provider === modelProvFilter) && (!mq || m.label.toLowerCase().includes(mq) || m.id.toLowerCase().includes(mq)));
  const disabledModels = allProviderModels.filter((m) => !enabledIds.has(m.id) && (modelProvFilter === "all" || m.provider === modelProvFilter) && (!mq || m.label.toLowerCase().includes(mq) || m.id.toLowerCase().includes(mq)));

  const filteredProviders = useMemo(() => mergedProviders.filter((p) => {
    if (providerFilter === "connected") return p.configured;
    if (providerFilter === "unset") return !p.configured;
    if (providerSearch) return p.label.toLowerCase().includes(providerSearch.toLowerCase()) || p.id.toLowerCase().includes(providerSearch.toLowerCase());
    return true;
  }), [mergedProviders, providerFilter, providerSearch]);

  const selectedProvData = mergedProviders.find((p) => p.id === selectedProvider);
  const guide = selectedProvider ? (PROVIDER_GUIDES[selectedProvider] ?? undefined) : undefined;

  // ── Tab 3: tier partition (chat completions vs speech-to-text) ────────────
  const chatTiers = config?.tiers.filter((t) => (tierMeta(t.tier_name)?.cat ?? "chat") === "chat") ?? [];
  const sttTiers = config?.tiers.filter((t) => tierMeta(t.tier_name)?.cat === "stt") ?? [];

  // One card renderer for both chat and STT tiers — the only difference is the
  // pool of assignable models (chat completions vs transcription) and that a
  // transcription model can't be "tested" via a chat prompt.
  const renderTierCard = (tier: TierInfo) => {
    const meta = tierMeta(tier.tier_name);
    const isStt = meta?.cat === "stt";
    const pickerModels = isStt ? sttEnabledModels : chatEnabledModels;
    const isEditing = editingTier === tier.tier_name;
    const isTesting = testingTier === tier.tier_name;
    const currentEnabled = pickerModels.find((m) => m.id === tier.model);
    const modelNotInEnabled = pickerModels.length > 0 && !currentEnabled;
    const colour = PROVIDER_COLOURS[tier.provider] ?? PROVIDER_COLOURS.unknown;

    return (
      <div key={tier.tier_name} className={`rounded-xl border bg-card overflow-hidden tech-transition ${
        isEditing ? "border-primary/40" : modelNotInEnabled ? "border-warning/30" : "border-border"
      }`}>
        {/* Compact header row */}
        <button
          onClick={() => {
            if (isEditing) { setEditingTier(null); setTestResult(null); setTestedTier(null); }
            else { setEditingTier(tier.tier_name); setEditModel(tier.model); setEditProvider(tier.provider); setEditApiBase(""); setTestResult(null); setTestedTier(null); }
          }}
          className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-secondary/20 tech-transition"
        >
          <span className="text-xl shrink-0">{meta?.icon ?? "●"}</span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-foreground">{meta?.label ?? tier.tier_name}</span>
              {tier.provider_configured ? (
                <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-success/10 text-success border border-success/20 font-medium">Live</span>
              ) : (
                <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground border border-border">No key</span>
              )}
              {modelNotInEnabled && (
                <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/20">⚠ Model not enabled</span>
              )}
            </div>
            <div className="flex items-center gap-1.5 mt-1 flex-wrap">
              <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[9px] font-medium ${colour}`}>
                <span>{PROVIDER_ICONS[tier.provider] ?? "?"}</span>{tier.provider}
              </span>
              <span className="font-mono text-[10px] text-muted-foreground truncate max-w-[200px]">{tier.model}</span>
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {!isStt && (
              <Button variant="secondary" size="none" layout="" onClick={(e) => { e.stopPropagation(); handleTestTier(tier.tier_name); }} disabled={isTesting || !tier.provider_configured} className="px-2.5 py-1 text-[11px]">{isTesting ? "…" : "Test"}</Button>
            )}
            <svg className={`w-4 h-4 text-muted-foreground/40 tech-transition ${isEditing ? "rotate-90" : ""}`} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M6 3l5 5-5 5" />
            </svg>
          </div>
        </button>

        {testResult && editingTier === null && testingTier === null && testedTier === tier.tier_name && (
          <div className={`mx-4 mb-3 rounded-md border px-3 py-1.5 text-[10px] ${testResult.ok ? "border-success/30 bg-success/10 text-success" : "border-destructive/20 bg-destructive/5 text-destructive"}`}>
            {testResult.ok ? "✓" : "✗"} {testResult.text} · {testResult.ms}ms{testResult.model ? ` · ${testResult.model}` : ""}
          </div>
        )}

        {/* Expanded edit panel */}
        {isEditing && (
          <div className="border-t border-border bg-secondary/10 p-4 space-y-3">
            {/* Model picker — only enabled models of the right kind */}
            <div>
              <label className="text-[11px] font-medium text-muted-foreground">
                Model
                {pickerModels.length === 0 && <span className="ml-1.5 text-warning">(no {isStt ? "speech-to-text" : ""} models enabled — go to Models tab first)</span>}
              </label>
              {isStt && (
                <p className="mt-1 text-[10px] text-muted-foreground/80">
                  Whisper models transcribe fast but can’t name speakers. Pick a <span className="text-foreground">Deepgram</span> model to label who said what.
                </p>
              )}
              {loadingModels ? (
                <div className="mt-2 text-xs text-muted-foreground flex items-center gap-2">
                  <span className="w-3 h-3 rounded-full border-2 border-muted border-t-primary animate-spin" />Loading enabled models…
                </div>
              ) : pickerModels.length === 0 ? (
                <div className="mt-2 rounded-lg border border-warning/20 bg-warning/5 p-2.5 text-[11px] text-warning/80">
                  {isStt ? (
                    <>Enable a speech-to-text model (Whisper on Groq/OpenAI, or Deepgram for named speakers) in the <button onClick={() => { setEditingTier(null); setTab("models"); }} className="underline">Models tab</button> first.</>
                  ) : (
                    <>Enable models in the <button onClick={() => { setEditingTier(null); setTab("models"); }} className="underline">Models tab</button> first.</>
                  )}
                </div>
              ) : (
                <div className="mt-2 space-y-1 max-h-48 overflow-y-auto">
                  {pickerModels.map((m) => (
                    <button
                      key={m.id}
                      onClick={() => { setEditModel(m.id); setEditProvider(m.provider); }}
                      className={`w-full flex items-center justify-between rounded-lg border px-3 py-2 text-left tech-transition ${
                        editModel === m.id
                          ? "border-primary bg-primary/10"
                          : "border-border hover:border-primary/40 hover:bg-secondary/30"
                      }`}
                    >
                      <div className="min-w-0">
                        <div className="text-xs font-medium text-foreground truncate">{m.label}</div>
                        <div className="text-[9px] text-muted-foreground font-mono truncate">{m.id}</div>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0 ml-2">
                        {isStt && (m.provider === "deepgram" || m.provider === "assemblyai") && <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/30">names speakers</span>}
                        <span className={`text-[9px] px-1.5 py-0.5 rounded border ${PROVIDER_COLOURS[m.provider] ?? PROVIDER_COLOURS.unknown}`}>{m.provider}</span>
                        {editModel === m.id && <span className="text-primary text-[10px]">&#10003;</span>}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Local base URL for ollama/vllm */}
            {(editProvider === "ollama" || editProvider === "vllm") && (
              <div>
                <label className="text-[11px] font-medium text-muted-foreground">Base URL</label>
                <input type="text" value={editApiBase} onChange={(e) => setEditApiBase(e.target.value)}
                  placeholder={editProvider === "ollama" ? "http://localhost:11434/v1" : "http://localhost:8000/v1"}
                  className="w-full mt-1 rounded-lg border border-border bg-card px-3 py-2 text-xs text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none" />
              </div>
            )}

            {/* Actions */}
            <div className="flex items-center gap-2 pt-1">
              <Button size="none" layout="" onClick={() => handleSaveTier(tier.tier_name, editModel, (editProvider === "ollama" || editProvider === "vllm") ? editApiBase || undefined : undefined)} disabled={savingTier || !editModel} className="px-4 py-2 text-xs">{savingTier ? "Saving…" : "Save"}</Button>
              <button onClick={() => { setEditingTier(null); setTestResult(null); setTestedTier(null); }}
                className="rounded-lg border border-border px-4 py-2 text-xs text-muted-foreground hover:text-foreground tech-transition"
              >Cancel</button>
            </div>

            {testResult && testedTier === tier.tier_name && (
              <div className={`rounded-md border px-3 py-1.5 text-[10px] ${testResult.ok ? "border-success/30 bg-success/10 text-success" : "border-destructive/20 bg-destructive/5 text-destructive"}`}>
                {testResult.ok ? "✓" : "✗"} {testResult.text} · {testResult.ms}ms{testResult.model ? ` · ${testResult.model}` : ""}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 sm:px-6 py-3 sm:py-4 border-b border-border shrink-0">
        <div>
          <h1 className="text-base sm:text-lg font-bold text-foreground">Models</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            {connCount} providers connected · {config?.tiers.filter((t) => t.provider_configured).length ?? 0}/{config?.tiers.length ?? 3} tiers configured
          </p>
        </div>
      </div>

      {saveMsg && (
        <div className={`shrink-0 mx-4 mt-2 rounded-lg border px-3 py-1.5 text-xs ${saveMsg.ok ? "border-success/20 bg-success/5 text-success" : "border-destructive/20 bg-destructive/5 text-destructive"}`}>
          {saveMsg.ok ? "✓ " : "✗ "}{saveMsg.text}
        </div>
      )}

      {/* Tabs — shared Tabs component (segmented variant) */}
      <Tabs
        tabs={[
          { id: "providers", label: "Providers" },
          { id: "models", label: "Models" },
          { id: "tiers", label: "Tiers" },
        ]}
        activeTab={tab}
        onTabChange={(t) => { setTab(t as typeof tab); setSelectedProvider(null); setEditingTier(null); }}
        variant="segmented"
      />

      {loading ? (
        <div className="flex-1 flex items-center justify-center"><span className="w-5 h-5 rounded-full border-2 border-muted border-t-primary animate-spin mr-2.5" /><span className="text-sm text-muted-foreground">Loading…</span></div>
      ) : loadError ? (
        <div className="flex-1 flex items-center justify-center"><div className="text-center"><p className="text-sm text-destructive mb-2">Failed to load config</p><p className="text-xs text-muted-foreground mb-4">{loadError}</p><button onClick={loadConfig} className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground tech-transition">Retry</button></div></div>
      ) : (
        <div className="flex-1 overflow-hidden flex">
          {/* ═══════════════════════════════════════════════════════════════
              TAB 1: PROVIDERS
              ═══════════════════════════════════════════════════════════ */}
          {tab === "providers" && (
            <div className="flex-1 flex overflow-hidden">
              <div className="flex-1 overflow-y-auto">
                <div className="px-4 pt-3 pb-2 flex items-center gap-2 flex-wrap">
                  <div className="flex items-center gap-1">
                    {([["all","All"],["connected","Connected"],["unset","Not set up"]] as const).map(([id, label]) => (
                      <button key={id} onClick={() => setProvFilter(id)}
                        className={`px-2.5 py-1 rounded-full text-[11px] font-medium tech-transition ${providerFilter === id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary"}`}>{label}</button>
                    ))}
                  </div>
                  <div className="relative flex-1 min-w-[140px] max-w-[220px] ml-auto">
                    <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"><SearchIcon /></span>
                    <input type="text" placeholder="Search…" value={providerSearch} onChange={(e) => setProviderSearch(e.target.value)}
                      className="w-full rounded-md border border-border bg-card pl-8 pr-3 py-1.5 text-[11px] text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none" />
                  </div>
                </div>
                <div className="p-4">
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
                    {filteredProviders.map((p) => (
                      <button key={p.id} onClick={() => setSelectedProvider(selectedProvider === p.id ? null : p.id)}
                        className={`text-left w-full p-3 rounded-xl border tech-transition ${selectedProvider === p.id ? "border-primary bg-primary/5 ring-1 ring-primary/20" : "border-border bg-card hover:border-primary/40 hover:bg-secondary/30"}`}>
                        <div className="flex items-start justify-between mb-2">
                          <span className="text-lg shrink-0">{PROVIDER_ICONS[p.id] ?? "?"}</span>
                          <span className={`w-2 h-2 mt-1 rounded-full shrink-0 ${p.configured ? "bg-success" : "bg-muted"}`} />
                        </div>
                        <div className="font-medium text-xs text-foreground leading-tight truncate">{p.label}</div>
                        <div className={`text-[10px] mt-0.5 ${p.configured ? "text-success" : "text-muted-foreground"}`}>
                          {p.configured ? "● Connected" : p.id === "ollama" || p.id === "vllm" ? "○ Local" : "○ No key"}
                        </div>
                        {p.env_var && <div className="text-[9px] text-muted-foreground font-mono mt-1 truncate opacity-60">{p.env_var}</div>}
                      </button>
                    ))}
                  </div>
                  {filteredProviders.length === 0 && <div className="text-center py-12 text-xs text-muted-foreground">No providers match your filter.</div>}
                </div>
              </div>
              {selectedProvider && selectedProvData && (
                <div className="hidden sm:flex w-[380px] border-l border-border bg-card shrink-0 flex-col overflow-hidden animate-fade-in">
                  <ProviderDetail provider={selectedProvData} guide={guide} copilotScopeOk={selectedProvData.id === "github" ? copilotScopeOk : undefined}
                    onKeySet={(key) => handleKeySet(selectedProvData.id, key)} onKeyDiscard={() => handleKeyDiscard(selectedProvData.id)} onClose={() => setSelectedProvider(null)} />
                </div>
              )}
              {selectedProvider && selectedProvData && (
                <div className="sm:hidden fixed inset-0 z-[60] pointer-events-none">
                  <div className="absolute inset-0 bg-black/50 pointer-events-auto" onClick={() => setSelectedProvider(null)} />
                  <aside className="absolute inset-x-0 bottom-14 pointer-events-auto flex max-h-[55%] flex-col rounded-t-2xl border-t border-border bg-card shadow-2xl chat-fade-in">
                    <ProviderDetail provider={selectedProvData} guide={guide} copilotScopeOk={selectedProvData.id === "github" ? copilotScopeOk : undefined}
                      onKeySet={(key) => handleKeySet(selectedProvData.id, key)} onKeyDiscard={() => handleKeyDiscard(selectedProvData.id)} onClose={() => setSelectedProvider(null)} />
                  </aside>
                </div>
              )}
            </div>
          )}

          {/* ═══════════════════════════════════════════════════════════════
              TAB 2: MODELS — per-provider models, click for detail panel
              ═══════════════════════════════════════════════════════════════ */}
          {tab === "models" && (
            <div className="flex-1 flex overflow-hidden">
              <div className="flex-1 overflow-y-auto">
                {/* Filter pills + refresh */}
                <div className="px-4 pt-3 pb-2 flex items-center gap-1.5 flex-wrap">
                  <button onClick={() => setModelProvFilter("all")}
                    className={`px-2.5 py-1 rounded-full text-[11px] font-medium tech-transition ${modelProvFilter === "all" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary"}`}>All</button>
                  {configuredProviderIds.map((pid) => (
                    <button key={pid} onClick={() => setModelProvFilter(pid)}
                      className={`px-2.5 py-1 rounded-full text-[11px] font-medium tech-transition ${modelProvFilter === pid ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-secondary"}`}>
                      {pid} <span className="opacity-50">{(providerModels.get(pid) ?? []).length}</span>
                    </button>
                  ))}
                  {/* Refresh button + last-synced */}
                  <div className="ml-auto flex items-center gap-2 shrink-0">
                    {lastSynced && (
                      <span className="text-[9px] text-muted-foreground hidden sm:inline" title={new Date(lastSynced).toLocaleString()}>
                        Synced {new Date(lastSynced).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                      </span>
                    )}
                    <Button variant="secondary" size="none" layout="flex items-center" onClick={handleRefreshModels} disabled={refreshing || configuredProviderIds.length === 0} title="Fetch latest models from all configured providers" className="gap-1 px-2.5 py-1 text-[11px]">
                      <svg className={`w-3 h-3 ${refreshing ? "animate-spin" : ""}`} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <path d="M14 8A6 6 0 1 1 8 2" /><path d="M14 2v4h-4" />
                      </svg>
                      {refreshing ? "Syncing…" : "Refresh"}
                    </Button>
                  </div>
                </div>
                <div className="px-4 pb-2">
                  <div className="relative">
                    <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"><SearchIcon /></span>
                    <input type="text" placeholder="Search models…" value={modelSearch} onChange={(e) => setModelSearch(e.target.value)}
                      className="w-full rounded-md border border-border bg-card pl-8 pr-3 py-1.5 text-[11px] text-foreground placeholder-muted-foreground focus:border-primary focus:outline-none" />
                  </div>
                </div>
                <div className="p-4 pt-0">
                  {loadingModels ? (
                    <div className="flex items-center justify-center py-12 text-xs text-muted-foreground"><span className="w-4 h-4 rounded-full border-2 border-muted border-t-primary animate-spin mr-2" />Loading…</div>
                  ) : configuredProviderIds.length === 0 ? (
                    <div className="text-center py-12 text-xs text-muted-foreground">Connect a provider in the Providers tab first.</div>
                  ) : allProviderModels.length === 0 ? (
                    <div className="text-center py-12 text-xs text-muted-foreground space-y-2">
                      <p>No models loaded yet.</p>
                      <button onClick={handleRefreshModels} disabled={refreshing}
                        className="rounded-lg border border-border px-3 py-1.5 text-xs text-foreground hover:border-primary/30 tech-transition disabled:opacity-40">
                        {refreshing ? "Fetching…" : "Fetch models from providers →"}
                      </button>
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-2">
                      {enabledModels.map((m) => (
                        <div key={m.id} className="flex items-center justify-between rounded-lg border border-success/20 bg-success/5 px-3 py-2 text-xs group">
                          <button onClick={() => setSelectedModel(m)} className="min-w-0 flex-1 text-left">
                            <div className="text-foreground font-medium truncate text-[11px] hover:text-primary tech-transition">{m.label}</div>
                            <div className="flex items-center gap-1.5 mt-0.5">
                              <span className="text-[9px] px-1 py-0.5 rounded border border-border text-muted-foreground truncate">{m.provider}</span>
                            </div>
                          </button>
                          <button onClick={(e) => { e.stopPropagation(); toggleEnabled(m); }} disabled={busyModel === m.id}
                            className="ml-1.5 shrink-0 text-success hover:text-warning tech-transition disabled:opacity-30 opacity-0 group-hover:opacity-100" title="Disable">
                            {busyModel === m.id ? <span className="text-[9px]">…</span> : <EyeIcon />}
                          </button>
                        </div>
                      ))}
                      {disabledModels.length > 0 && enabledModels.length > 0 && (
                        <div className="col-span-full pt-2 pb-1 flex items-center gap-2">
                          <div className="flex-1 h-px bg-border" />
                          <span className="text-[9px] text-muted-foreground font-medium shrink-0">NOT ENABLED ({disabledModels.length})</span>
                          <div className="flex-1 h-px bg-border" />
                        </div>
                      )}
                      {disabledModels.map((m) => (
                        <div key={m.id} className="flex items-center justify-between rounded-lg border border-border/30 bg-secondary/15 px-3 py-2 text-xs opacity-55 hover:opacity-100 tech-transition group">
                          <button onClick={() => setSelectedModel(m)} className="min-w-0 flex-1 text-left">
                            <div className="text-muted-foreground font-medium truncate text-[11px] hover:text-foreground tech-transition">{m.label}</div>
                            <div className="flex items-center gap-1.5 mt-0.5">
                              <span className="text-[9px] px-1 py-0.5 rounded border border-border/50 text-muted-foreground truncate">{m.provider}</span>
                            </div>
                          </button>
                          <button onClick={(e) => { e.stopPropagation(); toggleEnabled(m); }} disabled={busyModel === m.id}
                            className="ml-1.5 shrink-0 text-muted-foreground hover:text-success tech-transition disabled:opacity-30 opacity-0 group-hover:opacity-100" title="Enable">
                            {busyModel === m.id ? <span className="text-[9px]">…</span> : <EyeOffIcon />}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Desktop: model detail side panel */}
              {selectedModel && (
                <div className="hidden sm:flex w-[380px] border-l border-border bg-card shrink-0 flex-col overflow-hidden animate-fade-in">
                  <ModelDetailPanel model={selectedModel} enabled={enabledIds.has(selectedModel.id)} busy={busyModel === selectedModel.id}
                    onToggle={() => toggleEnabled(selectedModel)} onClose={() => setSelectedModel(null)} />
                </div>
              )}
              {selectedModel && (
                <div className="sm:hidden fixed inset-0 z-[60] pointer-events-none">
                  <div className="absolute inset-0 bg-black/50 pointer-events-auto" onClick={() => setSelectedModel(null)} />
                  <aside className="absolute inset-x-0 bottom-14 pointer-events-auto flex max-h-[55%] flex-col rounded-t-2xl border-t border-border bg-card shadow-2xl chat-fade-in">
                    <ModelDetailPanel model={selectedModel} enabled={enabledIds.has(selectedModel.id)} busy={busyModel === selectedModel.id}
                      onToggle={() => toggleEnabled(selectedModel)} onClose={() => setSelectedModel(null)} />
                  </aside>
                </div>
              )}
            </div>
          )}

          {/* ═══════════════════════════════════════════════════════════════
              TAB 3: TIERS — assign enabled models to routing tiers
              ═══════════════════════════════════════════════════════════════ */}
          {tab === "tiers" && config && (
            <div className="flex-1 overflow-y-auto">
              {/* Hint when no models are enabled */}
              {allEnabledModels.length === 0 && !loadingModels && (
                <div className="mx-4 mt-4 rounded-lg border border-warning/25 bg-warning/5 px-4 py-3 text-xs text-warning/80">
                  ⚠ No models enabled. Go to the <button onClick={() => setTab("models")} className="underline hover:text-warning tech-transition">Models tab</button> and turn on models with the eye icon before assigning tiers.
                </div>
              )}

              <div className="p-4 space-y-5">
                {/* Chat Tiers */}
                <div>
                  <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60 mb-3">Chat Tiers</div>
                  <div className="space-y-2">
                    {chatTiers.map(renderTierCard)}
                  </div>
                </div>

                {/* Speech-to-text — the Note Taker's transcription model */}
                {sttTiers.length > 0 && (
                  <div>
                    <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60 mb-1">Speech-to-text</div>
                    <p className="text-[11px] text-muted-foreground/80 mb-3">
                      Which model the Note Taker uses to transcribe recordings. AssemblyAI and Deepgram also name speakers (diarization) in multi-speaker meetings — AssemblyAI does it on live captions too.
                    </p>
                    {sttEnabledModels.length === 0 && !loadingModels && (
                      <div className="mb-2 rounded-lg border border-warning/25 bg-warning/5 px-4 py-3 text-xs text-warning/80">
                        ⚠ No speech-to-text models enabled. Add a <span className="font-medium">Groq</span>, <span className="font-medium">OpenAI</span>, or <span className="font-medium">Deepgram</span> key in Providers, then enable a Whisper/Nova model in the <button onClick={() => setTab("models")} className="underline hover:text-warning tech-transition">Models tab</button>.
                      </div>
                    )}
                    <div className="space-y-2">
                      {sttTiers.map(renderTierCard)}
                    </div>
                  </div>
                )}

                {/* Additional Capabilities (not yet wired) */}
                <div>
                  <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60 mb-3">Additional Capabilities</div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {ALL_TIERS.filter((td) => td.cat !== "chat" && td.cat !== "stt").map((td) => (
                      <div key={td.id} className="rounded-xl border border-border/40 bg-card/30 p-4 opacity-60 text-center">
                        <div className="text-2xl mb-2">{td.icon}</div>
                        <div className="text-xs font-medium text-muted-foreground">{td.label}</div>
                        <div className="text-[9px] text-muted-foreground/60 mt-1">{td.desc}</div>
                        <div className="mt-2 text-[9px] px-2 py-0.5 rounded-full bg-secondary text-muted-foreground inline-block">Coming soon</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// ProviderDetail — side panel for key setup
// ═══════════════════════════════════════════════════════════════════════════════

function ProviderDetail({ provider, guide, copilotScopeOk, onKeySet, onKeyDiscard, onClose }: {
  provider: ProviderInfo;
  guide: { description: string; setup_url: string; docs_url: string; instructions: string[] } | undefined;
  copilotScopeOk?: boolean | null;
  onKeySet: (key: string) => Promise<void>;
  onKeyDiscard: () => Promise<void>;
  onClose: () => void;
}) {
  const [keyVal, setKeyVal] = useState(""); const [show, setShow] = useState(false);
  const [saving, setSaving] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [discarding, setDiscarding] = useState(false); const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [deviceFlow, setDeviceFlow] = useState<{ deviceCode: string; userCode: string; verificationUri: string; interval: number } | null>(null);
  const [deviceStatus, setDeviceStatus] = useState("");
  const isLocal = provider.id === "ollama" || provider.id === "vllm";

  const handleSave = async () => { if (!keyVal.trim()) return; setSaving(true); setErr(null); try { await onKeySet(keyVal.trim()); setKeyVal(""); } catch (e) { setErr(String(e)); } finally { setSaving(false); } };
  const handleDiscard = async () => { setDiscarding(true); try { await onKeyDiscard(); setConfirmDiscard(false); onClose(); } catch (e) { setErr(String(e)); } finally { setDiscarding(false); } };

  const startDeviceFlow = async () => {
    setDeviceStatus("Starting…");
    try {
      const res = await fetch("/api/integrations/github/device/start", { method: "POST" });
      if (!res.ok) { const e = await res.json().catch(() => ({})); setDeviceStatus(String(e.error ?? e.detail ?? "Failed")); return; }
      const data = await res.json();
      setDeviceFlow({ deviceCode: data.device_code, userCode: data.user_code, verificationUri: data.verification_uri, interval: (data.interval ?? 5) * 1000 });
      setDeviceStatus("Waiting for authorization…");
      pollDeviceFlow(data.device_code, (data.interval ?? 5) * 1000);
    } catch (e) { setDeviceStatus(String(e)); }
  };
  const pollDeviceFlow = async (deviceCode: string, interval: number) => {
    try {
      const res = await fetch("/api/integrations/github/device/poll", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device_code: deviceCode }) });
      const data = await res.json();
      if (data.status === "authorized") { setDeviceStatus(`Connected as ${data.login ?? "GitHub user"}!`); setDeviceFlow(null); setTimeout(() => window.location.reload(), 1500); }
      else if (data.status === "pending") { setDeviceStatus("Waiting…"); setTimeout(() => pollDeviceFlow(deviceCode, interval), interval); }
      else if (data.status === "slow_down") { const next = (data.interval ?? 10) * 1000; setDeviceStatus("Please wait…"); setTimeout(() => pollDeviceFlow(deviceCode, next), next); }
      else { setDeviceStatus(`Failed: ${data.status}`); setDeviceFlow(null); }
    } catch (e) { setDeviceStatus(`Error: ${String(e)}`); setDeviceFlow(null); }
  };

  return (
    <>
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="text-lg shrink-0">{PROVIDER_ICONS[provider.id] ?? "?"}</span>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-foreground truncate">{provider.label}</div>
            <div className="flex items-center gap-2 mt-0.5">
              {provider.env_var && <span className="text-[9px] font-mono text-muted-foreground truncate">{provider.env_var}</span>}
              <span className={`text-[10px] font-medium ${provider.configured ? "text-success" : "text-muted-foreground"}`}>
                {provider.configured ? "● Configured" : provider.id === "ollama" || provider.id === "vllm" ? "○ Local" : "○ No key"}
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {provider.configured && !isLocal && (
            !confirmDiscard ? (
              <button onClick={() => setConfirmDiscard(true)} className="text-[10px] text-destructive/70 hover:text-destructive underline underline-offset-2 tech-transition">discard</button>
            ) : (
              <span className="flex items-center gap-1">
                <button onClick={handleDiscard} disabled={discarding} className="rounded border border-destructive/20 bg-destructive/5 px-2 py-0.5 text-[10px] text-destructive hover:bg-destructive/10 tech-transition disabled:opacity-40">{discarding ? "…" : "Confirm"}</button>
                <button onClick={() => setConfirmDiscard(false)} disabled={discarding} className="text-[10px] text-muted-foreground hover:text-foreground underline underline-offset-2 tech-transition">cancel</button>
              </span>
            )
          )}
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={onClose} className="rounded-md">
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 4l8 8M12 4l-8 8" /></svg>
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {provider.id === "github" && provider.configured && copilotScopeOk === false && (
          <div className="rounded-lg border border-warning/25 bg-warning/5 p-2.5 text-[10px] space-y-1">
            <span className="text-warning font-medium">⚠ Copilot scope missing</span>
            <p className="text-muted-foreground">Your token may lack the <code className="font-mono bg-secondary px-1 rounded">copilot</code> permission.</p>
            <details><summary className="text-primary cursor-pointer hover:underline text-[10px]">How to fix</summary>
              <ol className="mt-1 space-y-0.5 pl-3 text-muted-foreground">
                <li>Go to <a href="https://github.com/settings/tokens?type=beta" target="_blank" rel="noopener noreferrer" className="underline">github.com/settings/tokens</a></li>
                <li>Generate a fine-grained token with <strong>Copilot → Read-only</strong></li>
                <li>Paste the new token below</li>
              </ol>
            </details>
          </div>
        )}
        {guide && !isLocal && (
          <>
            <p className="text-xs text-muted-foreground leading-relaxed">{guide.description}</p>
            <div className="flex items-center gap-2">
              <a href={guide.setup_url} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-2.5 py-1 text-[10px] font-medium text-primary hover:bg-primary/20 tech-transition">Get API key ↗</a>
              <a href={guide.docs_url} target="_blank" rel="noopener noreferrer"
                className="text-[10px] text-muted-foreground hover:text-foreground underline underline-offset-2 tech-transition">Docs ↗</a>
            </div>
          </>
        )}
        {!provider.configured || !isLocal ? (
          <div className="space-y-2">
            {guide && !isLocal && (
              <ol className="space-y-1">
                {guide.instructions.map((step, i) => (
                  <li key={i} className="flex items-start gap-2 text-[10px] text-muted-foreground">
                    <span className="shrink-0 w-3.5 h-3.5 rounded-full bg-secondary text-muted-foreground flex items-center justify-center text-[8px] font-semibold mt-0.5">{i + 1}</span>{step}
                  </li>
                ))}
              </ol>
            )}
            <div className="relative">
              <input type={show ? "text" : "password"} value={keyVal} onChange={(e) => setKeyVal(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleSave()}
                placeholder={`Paste ${provider.env_var}…`}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 pr-14 text-xs text-foreground placeholder-muted-foreground font-mono focus:border-primary focus:outline-none" />
              <button onClick={() => setShow((s) => !s)} className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground hover:text-foreground">{show ? "hide" : "show"}</button>
            </div>
            {err && <p className="text-[10px] text-destructive">{err}</p>}
            <Button size="none" layout="" onClick={handleSave} disabled={saving || !keyVal.trim()} className="w-full px-3 py-2 text-xs">{saving ? "Saving & restarting LiteLLM…" : "Save & apply key"}</Button>
            {saving && <p className="text-[9px] text-muted-foreground text-center">Writing key and restarting (~25s)…</p>}
            {provider.id === "github" && (
              <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 space-y-2">
                <div className="flex items-center gap-1.5"><span className="text-xs">✦</span><span className="text-[10px] font-medium text-primary/80">GitHub OAuth Device Flow</span></div>
                <p className="text-[10px] text-muted-foreground">No token copying — enter a code on GitHub to authenticate automatically.</p>
                {!deviceFlow ? (
                  <button onClick={startDeviceFlow} disabled={!!deviceStatus && !deviceStatus.includes("Error")}
                    className="w-full rounded-lg border border-primary/30 bg-primary/10 px-3 py-2 text-[10px] font-medium text-primary/80 hover:bg-primary/20 tech-transition disabled:opacity-40">{deviceStatus || "Connect with GitHub →"}</button>
                ) : (
                  <div className="text-center space-y-1.5">
                    <div className="text-2xl font-bold tracking-[0.3em] text-primary/80 font-mono">{deviceFlow.userCode}</div>
                    <a href={deviceFlow.verificationUri} target="_blank" rel="noopener noreferrer" className="text-[10px] text-primary/80 underline block">Open {deviceFlow.verificationUri} →</a>
                    <p className="text-[9px] text-muted-foreground">{deviceStatus}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="rounded-lg border border-success/20 bg-success/5 p-3 text-center">
            <p className="text-xs text-success font-medium">✓ Connected</p>
            <p className="text-[10px] text-muted-foreground mt-0.5">Key is set and LiteLLM has access to this provider.</p>
          </div>
        )}
        {confirmDiscard && (
          <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-3 space-y-2">
            <p className="text-[10px] text-destructive">This will remove the key and disconnect the provider. Tiers using it will fall back to the next available.</p>
            <button onClick={handleDiscard} disabled={discarding}
              className="w-full rounded-lg bg-destructive px-3 py-2 text-xs font-medium text-destructive-foreground hover:opacity-90 disabled:opacity-40 tech-transition">{discarding ? "Removing key & restarting…" : "Yes, discard key"}</button>
            {discarding && <p className="text-[9px] text-muted-foreground text-center">Removing key and restarting (~25s)…</p>}
          </div>
        )}
      </div>
    </>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// ModelDetailPanel — side panel showing model capabilities
// ═══════════════════════════════════════════════════════════════════════════════

function ModelDetailPanel({ model, enabled, busy, onToggle, onClose }: {
  model: ModelInfo;
  enabled: boolean;
  busy: boolean;
  onToggle: () => void;
  onClose: () => void;
}) {
  const fmtCtx = (n: number) => n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(0)}K` : String(n);
  const fmtOut = (n: number) => n >= 1_000 ? `${(n / 1_000).toFixed(0)}K` : String(n);

  const caps: { icon: string; label: string; active: boolean }[] = [
    { icon: "👁", label: "Vision", active: model.vision },
    { icon: "🎤", label: "Audio", active: model.audio },
    { icon: "🧠", label: "Reasoning", active: model.reasoning },
  ];

  return (
    <>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-foreground truncate">{model.label}</div>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-[9px] px-1.5 py-0.5 rounded border border-border text-muted-foreground">{model.provider}</span>
            <span className="font-mono text-[10px] text-muted-foreground truncate">{model.id}</span>
          </div>
        </div>
        <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={onClose} className="rounded-md shrink-0 ml-2">
          <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 4l8 8M12 4l-8 8" /></svg>
        </Button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Description */}
        {model.desc && (
          <p className="text-xs text-muted-foreground leading-relaxed">{model.desc}</p>
        )}

        {/* Enable/disable button */}
        <button onClick={onToggle} disabled={busy}
          className={`w-full rounded-lg px-3 py-2 text-xs font-medium tech-transition disabled:opacity-40 ${
            enabled
              ? "border border-success/20 bg-success/5 text-success hover:bg-success/10"
              : "bg-primary text-primary-foreground hover:opacity-90"
          }`}>
          {busy ? "…" : enabled ? "✓ Enabled — click to disable" : "Enable in Metorite"}
        </button>

        {/* Capabilities */}
        <div>
          <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60 mb-2">Capabilities</div>
          <div className="grid grid-cols-3 gap-2">
            {caps.map((c) => (
              <div key={c.label} className={`rounded-lg border p-2 text-center ${c.active ? "border-primary/30 bg-primary/5" : "border-border/40 bg-secondary/20 opacity-40"}`}>
                <div className="text-base mb-0.5">{c.icon}</div>
                <div className={`text-[10px] font-medium ${c.active ? "text-foreground" : "text-muted-foreground"}`}>{c.label}</div>
                <div className="text-[9px] text-muted-foreground mt-0.5">{c.active ? "✓" : "—"}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Context & output */}
        {(model.context_window > 0 || model.max_output > 0) && (
          <div>
            <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60 mb-2">Specs</div>
            <div className="grid grid-cols-2 gap-2">
              {model.context_window > 0 && (
                <div className="rounded-lg border border-border/60 bg-card px-3 py-2">
                  <div className="text-[9px] text-muted-foreground">Context window</div>
                  <div className="text-xs font-mono font-medium text-foreground mt-0.5">{fmtCtx(model.context_window)} tokens</div>
                </div>
              )}
              {model.max_output > 0 && (
                <div className="rounded-lg border border-border/60 bg-card px-3 py-2">
                  <div className="text-[9px] text-muted-foreground">Max output</div>
                  <div className="text-xs font-mono font-medium text-foreground mt-0.5">{fmtOut(model.max_output)} tokens</div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}