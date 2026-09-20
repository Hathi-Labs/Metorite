"use client";

/**
 * Artifacts — file explorer for all agent-generated files.
 *
 * Agents are the top-level folders.  Inside each agent, files and folders
 * are shown together like a file explorer (Google Drive / VS Code style).
 * Click a folder to navigate in; breadcrumbs let you navigate back.
 * Grid / List views change how the current directory's contents appear.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useMemo, useState } from "react";
import ArtifactViewerModal from "@/components/ArtifactViewerModal";
import type { FileEntry } from "@/components/ArtifactSidebar";

// ─── Types ────────────────────────────────────────────────────────────────

interface ArtifactEntry {
  agent_name: string;
  path: string;
  name: string;
  size: number;
  modified_at: string;
  mime_type: string;
  category: "inputs" | "outputs" | "agent-data";
  is_dir?: boolean;
}

interface AgentOption {
  name: string;
  status?: string;
}

type ViewMode = "grid" | "list";
type SortKey = "newest" | "oldest" | "name" | "largest" | "smallest";

// ─── Agent colour palette ─────────────────────────────────────────────────

const AGENT_COLORS = [
  "border-l-blue-500",
  "border-l-emerald-500",
  "border-l-purple-500",
  "border-l-amber-500",
  "border-l-rose-500",
  "border-l-cyan-500",
  "border-l-orange-500",
  "border-l-violet-500",
  "border-l-teal-500",
  "border-l-pink-500",
];

function agentAccent(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  return AGENT_COLORS[Math.abs(hash) % AGENT_COLORS.length];
}

// ─── Helpers ──────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatRelative(iso: string): string {
  try {
    const d = new Date(iso);
    const now = Date.now();
    const diff = now - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "Just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days}d ago`;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch { return iso; }
}

function fileIconEl(entry: ArtifactEntry | { name: string; mime_type: string; is_dir?: boolean }, size = 16) {
  if ((entry as ArtifactEntry).is_dir) {
    return <Icon name="FolderClosed" size={size} className="shrink-0 text-amber-400" />;
  }
  const ext = entry.name.split(".").pop()?.toLowerCase() ?? "";
  const mime = entry.mime_type;
  if (["png","jpg","jpeg","gif","webp","svg","ico"].includes(ext) || mime.startsWith("image/"))
    return <Icon name="FileImage" size={size} className="shrink-0 text-purple-400" />;
  if (["py","ts","tsx","js","jsx","sh","yaml","yml","toml","json","sql","rs","go","java"].includes(ext))
    return <Icon name="FileCode" size={size} className="shrink-0 text-blue-400" />;
  if (["md","txt","log","rst"].includes(ext) || mime.startsWith("text/"))
    return <Icon name="FileText" size={size} className="shrink-0 text-green-400" />;
  if (["pdf"].includes(ext) || mime === "application/pdf")
    return <Icon name="FileText" size={size} className="shrink-0 text-red-400" />;
  if (["docx","doc"].includes(ext))
    return <Icon name="FileText" size={size} className="shrink-0 text-cyan-400" />;
  if (["xlsx","xls","csv"].includes(ext))
    return <Icon name="FileSpreadsheet" size={size} className="shrink-0 text-emerald-400" />;
  return <Icon name="File" size={size} className="shrink-0 text-muted-foreground" />;
}

function isImage(entry: ArtifactEntry): boolean {
  const ext = entry.name.split(".").pop()?.toLowerCase() ?? "";
  return ["png","jpg","jpeg","gif","webp","svg"].includes(ext) || entry.mime_type.startsWith("image/");
}

function toFileEntry(a: ArtifactEntry): FileEntry {
  return { path: a.path, name: a.name, size: a.size, modified_at: a.modified_at, mime_type: a.mime_type, is_dir: a.is_dir ?? false };
}

// ─── Explorer items (files + folders together) ────────────────────────────

interface ExplorerItem {
  name: string;
  path: string;
  isDir: boolean;
  entry?: ArtifactEntry;
  count?: number;
}

/** Build the list of items (files + folders) for a given path within an agent. */
function buildExplorerItems(
  artifacts: ArtifactEntry[],
  agentName: string,
  currentPath: string | null,
): ExplorerItem[] {
  const agentFiles = artifacts.filter((a) => a.agent_name === agentName);
  const prefix = currentPath ? currentPath + "/" : "";

  // Map: direct-child-name -> { isDir, entry?, fileCount }
  const directChildren = new Map<string, { isDir: boolean; entry?: ArtifactEntry; count: number }>();

  for (const a of agentFiles) {
    if (!a.path.startsWith(prefix)) continue;
    const rest = a.path.slice(prefix.length);
    const slashIdx = rest.indexOf("/");

    if (a.is_dir) {
      const dirName = slashIdx > 0 ? rest.substring(0, slashIdx) : rest;
      if (!directChildren.has(dirName)) {
        directChildren.set(dirName, { isDir: true, count: 0 });
      }
    } else if (slashIdx > 0) {
      const dirName = rest.substring(0, slashIdx);
      const existing = directChildren.get(dirName);
      if (existing) { existing.count++; }
      else { directChildren.set(dirName, { isDir: true, count: 1 }); }
    } else {
      directChildren.set(rest, { isDir: false, entry: a, count: 0 });
    }
  }

  // Recount from dir entries
  for (const a of agentFiles) {
    if (!a.is_dir) continue;
    if (!a.path.startsWith(prefix)) continue;
    const rest = a.path.slice(prefix.length);
    const slashIdx = rest.indexOf("/");
    const dirName = slashIdx > 0 ? rest.substring(0, slashIdx) : rest;
    let count = 0;
    for (const f of agentFiles) {
      if (!f.is_dir && f.path.startsWith(a.path + "/")) count++;
    }
    if (directChildren.has(dirName)) {
      directChildren.get(dirName)!.count = Math.max(directChildren.get(dirName)!.count, count);
    }
  }

  const items: ExplorerItem[] = [];
  for (const [name, info] of directChildren) {
    items.push({ name, path: prefix ? prefix + name : name, isDir: info.isDir, entry: info.entry, count: info.count });
  }

  items.sort((a, b) => {
    if (a.isDir && !b.isDir) return -1;
    if (!a.isDir && b.isDir) return 1;
    return a.name.localeCompare(b.name);
  });

  return items;
}

// ─── File Card (grid) ─────────────────────────────────────────────────────

function FileCard({ artifact, onView, index }: { artifact: ArtifactEntry; onView: () => void; index: number }) {
  const [imgError, setImgError] = useState(false);
  const showThumb = isImage(artifact) && !imgError;

  return (
    <div
      className="group relative rounded-xl border border-border bg-card hover:border-primary/20 hover:shadow-lg hover:shadow-primary/5 tech-transition overflow-hidden animate-fade-in cursor-pointer"
      style={{ animationDelay: `${Math.min(index * 40, 600)}ms` }}
      onClick={onView}
      title={`${artifact.name}\n${artifact.path}\nClick to view`}
    >
      <div className="flex items-center justify-center h-28 bg-secondary/40 border-b border-border/50">
        {showThumb ? (
          <img
            src={`/api/agent/artifacts/file?agent=${encodeURIComponent(artifact.agent_name)}&path=${encodeURIComponent(artifact.path)}`}
            alt={artifact.name} className="max-w-full max-h-full object-contain p-2"
            onError={() => setImgError(true)} loading="lazy"
          />
        ) : (
          <div className="flex flex-col items-center gap-1.5 text-muted-foreground/50">
            {fileIconEl(artifact, 32)}
            <span className="text-[10px] font-mono uppercase tracking-wider">{artifact.name.split(".").pop() ?? "file"}</span>
          </div>
        )}
      </div>
      <div className="p-3">
        <p className="text-xs font-medium text-foreground truncate leading-tight mb-1" title={artifact.name}>{artifact.name}</p>
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-muted-foreground">{formatBytes(artifact.size)}</span>
          <span className="text-[10px] text-muted-foreground/50">{formatRelative(artifact.modified_at)}</span>
        </div>
      </div>
      <div className="absolute inset-0 bg-background/80 reveal-on-hover tech-transition flex items-center justify-center gap-3">
        <button onClick={(e) => { e.stopPropagation(); onView(); }} className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 tech-transition shadow-lg">
          <Icon name="Eye" size={13} /> View
        </button>
      </div>
    </div>
  );
}

// ─── Folder Card (grid) ───────────────────────────────────────────────────

function FolderCard({ item, onNavigate, index }: { item: ExplorerItem; onNavigate: () => void; index: number }) {
  return (
    <div
      className="group relative rounded-xl border border-border bg-card hover:border-amber-500/30 hover:shadow-lg tech-transition overflow-hidden animate-fade-in cursor-pointer"
      style={{ animationDelay: `${Math.min(index * 40, 600)}ms` }}
      onClick={onNavigate}
      title={`${item.name}\nClick to open`}
    >
      <div className="flex items-center justify-center h-28 bg-amber-500/5 border-b border-border/30">
        <Icon name="FolderClosed" size={40} className="text-amber-400/70" />
      </div>
      <div className="p-3">
        <p className="text-xs font-medium text-foreground truncate leading-tight mb-1">{item.name}</p>
        <span className="text-[10px] text-muted-foreground">{item.count ?? 0} file{(item.count ?? 0) !== 1 ? "s" : ""}</span>
      </div>
    </div>
  );
}

// ─── List Row ─────────────────────────────────────────────────────────────

function ListRow({ item, onNavigate, onView, index }: {
  item: ExplorerItem; onNavigate: () => void; onView: () => void; index: number;
}) {
  return (
    <div
      className="grid grid-cols-[1fr_80px_80px] sm:grid-cols-[1fr_100px_100px] gap-x-3 px-4 py-2 border-b border-border/40 last:border-b-0 hover:bg-secondary/20 tech-transition cursor-pointer animate-fade-in"
      style={{ animationDelay: `${Math.min(index * 20, 400)}ms` }}
      onClick={() => { if (item.isDir) onNavigate(); else onView(); }}
    >
      <div className="flex items-center gap-2.5 min-w-0">
        {item.isDir
          ? <Icon name="FolderClosed" size={15} className="shrink-0 text-amber-400" />
          : fileIconEl(item.entry ?? { name: item.name, mime_type: "" }, 15)}
        <div className="min-w-0">
          <div className="truncate text-xs font-medium text-foreground">{item.name}</div>
          {!item.isDir && <div className="truncate text-[10px] text-muted-foreground">{item.path}</div>}
        </div>
      </div>
      <div className="flex items-center text-[11px] text-muted-foreground tabular-nums">
        {item.isDir ? `${item.count ?? 0} files` : item.entry ? formatBytes(item.entry.size) : ""}
      </div>
      <div className="flex items-center text-[10px] text-muted-foreground/60">
        {item.isDir ? "" : item.entry ? formatRelative(item.entry.modified_at) : ""}
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────

export default function ArtifactsPage() {
  const [artifacts, setArtifacts] = useState<ArtifactEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentOption[]>([]);

  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [currentPath, setCurrentPath] = useState<string | null>(null);

  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [sortKey, setSortKey] = useState<SortKey>("newest");
  const [agentFilter, setAgentFilter] = useState<string>("");
  const [fileTypeFilter, setFileTypeFilter] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");

  const [viewerEntry, setViewerEntry] = useState<FileEntry | null>(null);
  const [viewerUrl, setViewerUrl] = useState("");

  useEffect(() => {
    fetch("/api/agent/list")
      .then((r) => r.json())
      .then((data: AgentOption[]) => { if (Array.isArray(data)) setAgents(data); })
      .catch(() => {});
  }, []);

  const fetchArtifacts = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const params = new URLSearchParams();
      if (agentFilter) params.set("agent", agentFilter);
      const res = await fetch(`/api/agent/artifacts?${params.toString()}`);
      if (res.status === 503 || res.status === 502) {
        setError("Gateway offline."); setArtifacts([]); return;
      }
      if (!res.ok) { setError(`HTTP ${res.status}`); setArtifacts([]); return; }
      const data = await res.json();
      setArtifacts(Array.isArray(data.artifacts) ? data.artifacts : []);
    } catch (e) { setError(String(e)); setArtifacts([]); }
    finally { setLoading(false); }
  }, [agentFilter]);

  useEffect(() => { fetchArtifacts(); }, [fetchArtifacts]);

  const availableAgents = useMemo(() => {
    // Only show agents that are registered AND active (status === "live").
    // This prevents stale clone directories and deactivated agents from
    // appearing as duplicate agent folders on the artifacts page.
    const liveNames = new Set(
      agents.filter((a) => !a.status || a.status === "live").map((a) => a.name)
    );
    return Array.from(new Set(artifacts.map((a) => a.agent_name)))
      .filter((name) => liveNames.has(name))
      .sort();
  }, [artifacts, agents]);

  const filteredFiles = useMemo(() => {
    let list = artifacts.filter((a) => !a.is_dir);
    if (fileTypeFilter) {
      list = list.filter((a) => {
        const ext = a.name.split(".").pop()?.toLowerCase() ?? "";
        const mime = a.mime_type;
        switch (fileTypeFilter) {
          case "document": return ["md","docx","doc","pdf","txt","rst","log"].includes(ext) || mime.startsWith("text/") || mime === "application/pdf";
          case "spreadsheet": return ["csv","xlsx","xls","tsv"].includes(ext);
          case "image": return ["png","jpg","jpeg","gif","webp","svg","ico"].includes(ext) || mime.startsWith("image/");
          case "code": return ["py","ts","tsx","js","jsx","sh","yaml","yml","toml","json","sql","rs","go","java","html","css","xml"].includes(ext);
          case "other": return !["md","docx","doc","pdf","txt","rst","log","csv","xlsx","xls","tsv","png","jpg","jpeg","gif","webp","svg","ico","py","ts","tsx","js","jsx","sh","yaml","yml","toml","json","sql","rs","go","java","html","css","xml"].includes(ext);
          default: return true;
        }
      });
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter((a) => a.name.toLowerCase().includes(q) || a.path.toLowerCase().includes(q) || a.agent_name.toLowerCase().includes(q));
    }
    list.sort((a, b) => {
      switch (sortKey) {
        case "newest": return new Date(b.modified_at).getTime() - new Date(a.modified_at).getTime();
        case "oldest": return new Date(a.modified_at).getTime() - new Date(b.modified_at).getTime();
        case "name": return a.name.localeCompare(b.name);
        case "largest": return b.size - a.size;
        case "smallest": return a.size - b.size;
        default: return 0;
      }
    });
    return list;
  }, [artifacts, searchQuery, sortKey, fileTypeFilter]);

  // ── Explorer items for current directory ─────────────────────────────
  // Uses filtered files + all directory entries so filters apply to files
  // but folder structure is preserved.
  const explorerItems = useMemo(() => {
    if (!selectedAgent) return [];

    // Combine: filtered files + all directory entries (dirs are never filtered out)
    const filteredFilePaths = new Set(filteredFiles.map((f) => f.path));
    const combined = artifacts.filter(
      (a) => a.agent_name === selectedAgent && (a.is_dir || filteredFilePaths.has(a.path))
    );
    return buildExplorerItems(combined, selectedAgent, currentPath);
  }, [artifacts, filteredFiles, selectedAgent, currentPath]);

  const stats = useMemo(() => ({
    total: artifacts.filter((a) => !a.is_dir).length,
    totalSize: artifacts.reduce((s, a) => s + (a.is_dir ? 0 : a.size), 0),
  }), [artifacts]);

  const clearFilters = () => { setAgentFilter(""); setFileTypeFilter(""); setSearchQuery(""); };
  const hasFilters = !!(agentFilter || fileTypeFilter || searchQuery);

  const makeFileUrl = (a: ArtifactEntry) =>
    `/api/agent/artifacts/file?agent=${encodeURIComponent(a.agent_name)}&path=${encodeURIComponent(a.path)}`;

  const openViewer = (a: ArtifactEntry) => {
    setViewerUrl(makeFileUrl(a));
    setViewerEntry(toFileEntry(a));
  };

  return (
    <div className="flex flex-col h-full">
      {/* ═══ Header ══════════════════════════════════════════════════════ */}
      <div className="shrink-0 border-b border-border px-4 sm:px-6 py-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/15">
              <Icon name="FolderOpen" size={20} className="text-primary" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-foreground">Artifacts</h1>
              <p className="text-[11px] text-muted-foreground">
                {`${availableAgents.length} agent${availableAgents.length !== 1 ? "s" : ""} · ${stats.total} files`}
              </p>
            </div>
          </div>
          <Button variant="ghost" size="icon" layout="" onClick={fetchArtifacts} title="Refresh">
            <Icon name="RefreshCw" size={15} className={loading ? "animate-spin" : ""} />
          </Button>
        </div>

        {/* Stats */}
        <div className="flex items-center gap-2 mb-3 text-[11px] text-muted-foreground">
          <span className="font-semibold text-foreground">{stats.total}</span> files · {formatBytes(stats.totalSize)}
        </div>

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-2">
          <select value={fileTypeFilter} onChange={(e) => setFileTypeFilter(e.target.value)}
            className="rounded-lg border border-border bg-secondary px-3 py-1.5 text-[11px] text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50">
            <option value="">All file types</option>
            <option value="document">📄 Documents</option>
            <option value="spreadsheet">📊 Spreadsheets</option>
            <option value="image">🖼️ Images</option>
            <option value="code">💻 Code</option>
            <option value="other">📦 Other</option>
          </select>

          <select value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}
            className="rounded-lg border border-border bg-secondary px-3 py-1.5 text-[11px] text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50">
            <option value="">All agents</option>
            {agents.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
          </select>

          <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="rounded-lg border border-border bg-secondary px-3 py-1.5 text-[11px] text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50">
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="name">Name A–Z</option>
            <option value="largest">Largest first</option>
            <option value="smallest">Smallest first</option>
          </select>

          <div className="flex-1" />

          <div className="relative w-full sm:w-56">
            <Icon name="Search" size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Search files…"
              className="w-full rounded-lg border border-border bg-secondary pl-8 pr-8 py-1.5 text-[11px] text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50" />
            {searchQuery && (
              <button onClick={() => setSearchQuery("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"><Icon name="X" size={12} /></button>
            )}
          </div>

          {hasFilters && (
            <button onClick={clearFilters} className="rounded-lg border border-border px-2.5 py-1.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-secondary tech-transition flex items-center gap-1">
              <Icon name="X" size={11} /> Clear
            </button>
          )}
        </div>
      </div>

      {/* ═══ Content ══════════════════════════════════════════════════════ */}
      <div className="flex-1 overflow-auto">
        {loading && (
          <div className="flex flex-col items-center justify-center h-48 gap-3">
            <Icon name="RefreshCw" size={18} className="animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground animate-pulse">Loading artifacts…</p>
          </div>
        )}

        {error && !loading && (
          <div className="flex flex-col items-center justify-center h-48 gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-red-500/10"><Icon name="X" size={18} className="text-red-400" /></div>
            <p className="text-sm text-red-400">{error}</p>
            <button onClick={fetchArtifacts} className="text-xs text-muted-foreground hover:text-foreground underline">Retry</button>
          </div>
        )}

        {!loading && !error && filteredFiles.length === 0 && availableAgents.length === 0 && (
          <div className="flex flex-col items-center justify-center h-64 gap-4">
            <Icon name="Sparkles" size={28} className="text-muted-foreground/40" />
            <p className="text-sm font-medium text-foreground">No artifacts yet</p>
            <p className="text-xs text-muted-foreground mt-1 max-w-xs">Files appear here as agents create them.</p>
          </div>
        )}

        {/* ── Agent cards (accordion) ─────────────────────────────── */}
        {!loading && availableAgents.length > 0 && (
          <div className="p-4 sm:p-6 flex flex-col gap-3">
            {availableAgents.map((name) => {
              const isOpen = selectedAgent === name;
              const agentFiles = artifacts.filter((a) => a.agent_name === name && !a.is_dir);
              const accent = agentAccent(name);
              const items = isOpen ? explorerItems : [];
              return (
                <div key={name} className={`rounded-xl border border-border overflow-hidden ${isOpen ? "shadow-md" : ""}`}>
                  {/* Agent header */}
                  <button
                    onClick={() => {
                      if (isOpen) { setSelectedAgent(null); setCurrentPath(null); }
                      else { setSelectedAgent(name); setCurrentPath(null); }
                    }}
                    className={`w-full flex items-center gap-3 px-4 py-3 bg-card hover:bg-secondary/40 tech-transition text-left border-l-2 ${accent}`}
                  >
                    {isOpen ? <Icon name="ChevronDown" size={15} className="text-muted-foreground shrink-0" /> : <Icon name="ChevronRight" size={15} className="text-muted-foreground shrink-0" />}
                    <Icon name="Bot" size={16} className="text-muted-foreground shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-foreground truncate">{name}</div>
                      <div className="text-[11px] text-muted-foreground">{agentFiles.length} file{agentFiles.length !== 1 ? "s" : ""}</div>
                    </div>
                  </button>

                  {/* Expanded content */}
                  {isOpen && (
                    <div className="border-t border-border">
                      {/* Breadcrumb */}
                      <div className="flex items-center gap-1.5 px-4 py-2.5 bg-secondary/20 border-b border-border">
                        <button onClick={() => setCurrentPath(null)}
                          className={`text-xs px-1.5 py-0.5 rounded transition-colors ${!currentPath ? "font-semibold text-foreground bg-secondary" : "text-blue-400 hover:text-blue-300 hover:bg-secondary"}`}>
                          {name}
                        </button>
                        {currentPath && currentPath.split("/").map((seg, i, arr) => (
                          <span key={i} className="flex items-center gap-1.5">
                            <Icon name="ChevronRight" size={12} className="text-muted-foreground" />
                            {i === arr.length - 1 ? (
                              <span className="text-xs font-semibold text-foreground px-1.5 py-0.5 rounded bg-secondary">{seg}</span>
                            ) : (
                              <button onClick={() => setCurrentPath(arr.slice(0, i + 1).join("/"))}
                                className="text-xs text-blue-400 hover:text-blue-300 hover:bg-secondary px-1.5 py-0.5 rounded transition-colors">
                                {seg}
                              </button>
                            )}
                          </span>
                        ))}
                        <span className="text-[10px] text-muted-foreground ml-2">
                          {items.filter((i) => !i.isDir).length} files · {items.filter((i) => i.isDir).length} folders
                        </span>
                        <div className="flex-1" />
                        {/* View toggle inside agent card */}
                        <div className="flex items-center rounded-md border border-border/60 bg-secondary/40 p-0.5">
                          <button onClick={() => setViewMode("grid")}
                            className={`rounded p-1 tech-transition ${viewMode === "grid" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                            title="Grid view"><Icon name="LayoutGrid" size={12} /></button>
                          <button onClick={() => setViewMode("list")}
                            className={`rounded p-1 tech-transition ${viewMode === "list" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                            title="List view"><Icon name="List" size={12} /></button>
                        </div>
                      </div>

                      {/* Files + folders */}
                      <div className="p-3 max-h-[60vh] overflow-auto">
                        {items.length === 0 ? (
                          <div className="flex flex-col items-center justify-center h-24 gap-2 text-muted-foreground">
                            <Icon name="FolderOpen" size={20} className="opacity-30" />
                            <p className="text-xs">This folder is empty</p>
                          </div>
                        ) : viewMode === "grid" ? (
                          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-2">
                            {items.map((item, i) =>
                              item.isDir ? (
                                <FolderCard key={item.path} item={item} index={i}
                                  onNavigate={() => setCurrentPath(item.path)} />
                              ) : (
                                <FileCard key={item.path} artifact={item.entry!} index={i}
                                  onView={() => openViewer(item.entry!)} />
                              )
                            )}
                          </div>
                        ) : (
                          <div className="rounded-lg border border-border overflow-hidden bg-card">
                            <div className="hidden sm:grid grid-cols-[1fr_100px_100px] gap-x-3 px-4 py-2 bg-secondary/40 border-b border-border text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                              <span>Name</span><span>Size</span><span>Modified</span>
                            </div>
                            {items.map((item, i) => (
                              <ListRow key={item.path} item={item} index={i}
                                onNavigate={() => setCurrentPath(item.path)}
                                onView={() => item.entry && openViewer(item.entry)}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {viewerEntry && (
        <ArtifactViewerModal
          sessionId="artifacts" entry={viewerEntry}
          downloadUrl={viewerUrl} saveUrl={viewerUrl}
          onClose={() => setViewerEntry(null)}
        />
      )}
    </div>
  );
}
