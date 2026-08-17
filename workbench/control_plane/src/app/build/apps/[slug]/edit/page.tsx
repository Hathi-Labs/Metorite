"use client";

/**
 * /build/apps/[slug]/edit — the Workshop: sandboxed live preview on the left,
 * the app-builder chat pinned on the right (docs/app-workshop §4.2–4.3).
 *
 * The chat is a THIN wrapper around the shared <AgentChat> — the AssistantRail
 * pattern: one session per app (named `app:{slug}`), bound to the app's
 * workspace via PATCH /api/agent/workspace/{sessionId}, persona carrying the
 * workspace contract. Preview refreshes on artifact writes, on run-end
 * (onActivity → debounce → refetch + POST /sync), and a fallback poll.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import {
  Suspense,
  use,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMonacoTheme } from "@/lib/theme/surfaces";
import Editor from "@monaco-editor/react";
import AgentChat from "@/components/AgentChat";
import SandboxedHtml from "@/components/SandboxedHtml";
import Tabs from "@/components/Tabs";
import { useMobileDrawer } from "@/components/AppShell";
import { useViewMode } from "@/components/ViewModeProvider";
import {
  createSession,
  getSessions,
  upsertSession,
  type ChatSession,
} from "@/lib/sessions";
import {
  buildAppSrcDoc,
  extractCcIconNames,
  useCcBridge,
  type CcConsoleEvent,
  type CcToolConfirmDecision,
  type CcToolConfirmRequest,
} from "../../lib/ccBridge";
import { runAllScenarios, type TestResult, type TestScenario } from "../../lib/testRunner";
import type { AppFile, AppMeta, Checkpoint, GrantEntry } from "../../lib/types";

/** A pending `cc.tools.call()` confirm, waiting on the builder's decision. */
type PendingToolConfirm = CcToolConfirmRequest & {
  resolve: (decision: CcToolConfirmDecision) => void;
};

const BUILDER_AGENT = "app-builder";
const SESSION_KEY_PREFIX = "cc-app-builder-session-";
/** Fallback poll — run-end sync (onActivity) is the primary refresh path. */
const PREVIEW_POLL_MS = 30_000;
/** Settle time after an assistant turn lands before refetch + sync. */
const RUN_SYNC_DEBOUNCE_MS = 1_500;
/** Bound on captured console events (newest-first, oldest dropped). */
const CONSOLE_EVENT_CAP = 50;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatRelative(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days}d ago`;
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

/** Checkpoints list — shared between the desktop popover and the mobile
 * bottom sheet (the header's "Checkpoints" button opens one or the other,
 * same content either way). */
function CheckpointsPanel({
  checkpoints,
  confirmSha,
  setConfirmSha,
  restoringSha,
  restoreCheckpoint,
}: {
  checkpoints: Checkpoint[] | null;
  confirmSha: string | null;
  setConfirmSha: (sha: string | null) => void;
  restoringSha: string | null;
  restoreCheckpoint: (sha: string) => void;
}) {
  if (checkpoints === null) {
    return (
      <div className="px-2 py-1.5">
        <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (checkpoints.length === 0) {
    return (
      <p className="px-2 py-1.5 text-muted-foreground">
        No checkpoints yet — one is saved after each build turn.
      </p>
    );
  }
  return (
    <div className="max-h-64 overflow-y-auto flex flex-col gap-0.5">
      {checkpoints.map((c, i) => (
        <div
          key={c.sha}
          className="flex flex-col gap-1 rounded-lg px-2 py-1.5 hover:bg-secondary/50 tech-transition"
        >
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="text-xs text-foreground truncate">
                {c.message || c.sha.slice(0, 7)}
              </div>
              <div className="text-[10px] text-muted-foreground">
                {formatRelative(c.at)} · {c.files_changed}{" "}
                {c.files_changed === 1 ? "file" : "files"} changed
              </div>
            </div>
            {i === 0 ? (
              <span className="text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded-full text-success bg-success/10 shrink-0">
                Current
              </span>
            ) : confirmSha !== c.sha ? (
              <Button variant="secondary" size="none" radius="keep" layout="" onClick={() => setConfirmSha(c.sha)} className="text-[10px] rounded-md px-2 py-1 shrink-0">
                Restore
              </Button>
            ) : null}
          </div>
          {confirmSha === c.sha && (
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-muted-foreground flex-1">
                Restore this checkpoint?
              </span>
              <Button size="none" radius="keep" layout="flex items-center" onClick={() => restoreCheckpoint(c.sha)} disabled={restoringSha === c.sha} className="text-[10px] rounded-md px-2 py-1 gap-1">
                {restoringSha === c.sha && (
                  <Icon name="Loader2" className="w-3 h-3 animate-spin" />
                )}
                Confirm
              </Button>
              <button
                onClick={() => setConfirmSha(null)}
                className="text-[10px] rounded-md border border-border px-2 py-1 text-muted-foreground hover:text-foreground tech-transition"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** The `tool:`-prefixed manifest scopes — the ones gated by the Action Broker. */
function toolScopes(manifest: Record<string, unknown> | undefined): string[] {
  const raw = manifest?.scopes;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (s): s is string => typeof s === "string" && s.startsWith("tool:")
  );
}

/** The manifest's declared entry file — `index.html` for a T1 app, e.g.
 * `dist/bundle.html` once the builder upgrades an app to T2 (React). */
function manifestEntry(manifest: Record<string, unknown> | undefined): string {
  const raw = manifest?.entry;
  return typeof raw === "string" && raw.trim() ? raw : "index.html";
}

/** True for a T2 (build-based) app — same signal as the backend's
 * `_is_buildable_manifest` (gateway/routes/apps/files.py): resolved from
 * `entry`, never the `tier` display field. */
function isBuildBasedApp(manifest: Record<string, unknown> | undefined): boolean {
  return manifestEntry(manifest).startsWith("dist/");
}

/** Monaco's language id for a workspace file, by extension. Falls back to
 * plaintext for anything unrecognized rather than guessing. */
function monacoLanguage(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  switch (ext) {
    case "tsx":
    case "ts":
      return "typescript";
    case "jsx":
    case "js":
    case "mjs":
      return "javascript";
    case "json":
      return "json";
    case "html":
    case "htm":
      return "html";
    case "css":
      return "css";
    case "md":
      return "markdown";
    default:
      return "plaintext";
  }
}

/** One directory level of the Advanced view's file tree — `files` are leaf
 * paths at this level, `dirs` are nested subtrees keyed by directory name. */
interface FileTreeDir {
  dirs: Map<string, FileTreeDir>;
  files: AppFile[];
}

function emptyTreeDir(): FileTreeDir {
  return { dirs: new Map(), files: [] };
}

/** Groups a flat file list into a tree by path segments — today's apps are
 * shallow (index.html, src/*.tsx), but this scales correctly to any depth
 * without revisiting the grouping logic later. */
function buildFileTree(files: AppFile[]): FileTreeDir {
  const root = emptyTreeDir();
  for (const f of files) {
    const segments = f.path.split("/").filter(Boolean);
    let node = root;
    for (let i = 0; i < segments.length - 1; i++) {
      const name = segments[i];
      let child = node.dirs.get(name);
      if (!child) {
        child = emptyTreeDir();
        node.dirs.set(name, child);
      }
      node = child;
    }
    node.files.push(f);
  }
  return root;
}

const ADVANCED_VIEW_STORAGE_KEY = "cc-app-workshop-advanced-view";

/** Recursive file-tree render for the Advanced Code view. Renders `dir`'s
 * subdirectories (collapsible) then its own files, alphabetically within
 * each group — matches a conventional IDE explorer's ordering. */
function FileTreeView({
  dir,
  pathPrefix,
  selectedPath,
  onSelect,
  onDelete,
  collapsedDirs,
  onToggleDir,
}: {
  dir: FileTreeDir;
  pathPrefix: string;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  onDelete: (path: string) => void;
  collapsedDirs: Set<string>;
  onToggleDir: (path: string) => void;
}) {
  const dirNames = Array.from(dir.dirs.keys()).sort();
  const sortedFiles = [...dir.files].sort((a, b) => a.path.localeCompare(b.path));
  return (
    <>
      {dirNames.map((name) => {
        const childPath = pathPrefix ? `${pathPrefix}/${name}` : name;
        const collapsed = collapsedDirs.has(childPath);
        const child = dir.dirs.get(name)!;
        return (
          <div key={childPath}>
            <button
              onClick={() => onToggleDir(childPath)}
              className="w-full flex items-center gap-1.5 px-2 py-1 rounded-lg text-left font-mono text-[11.5px] text-muted-foreground hover:bg-secondary tech-transition"
            >
              {collapsed ? (
                <Icon name="Folder" className="w-3.5 h-3.5 shrink-0" />
              ) : (
                <Icon name="FolderOpen" className="w-3.5 h-3.5 shrink-0" />
              )}
              <span className="truncate">{name}</span>
            </button>
            {!collapsed && (
              <div className="pl-3.5">
                <FileTreeView
                  dir={child}
                  pathPrefix={childPath}
                  selectedPath={selectedPath}
                  onSelect={onSelect}
                  onDelete={onDelete}
                  collapsedDirs={collapsedDirs}
                  onToggleDir={onToggleDir}
                />
              </div>
            )}
          </div>
        );
      })}
      {sortedFiles.map((f) => (
        <div
          key={f.path}
          className={`group flex items-center gap-1 rounded-lg tech-transition ${
            selectedPath === f.path
              ? "bg-primary/10 text-foreground"
              : "text-muted-foreground hover:bg-secondary"
          }`}
        >
          <button
            onClick={() => onSelect(f.path)}
            title={`${f.path} · ${formatBytes(f.size)}`}
            className="flex-1 min-w-0 flex items-center gap-2 px-2 py-1.5 text-left font-mono text-[11.5px]"
          >
            <Icon name="FileCode" className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate">{f.path.split("/").pop()}</span>
          </button>
          <button
            onClick={() => onDelete(f.path)}
            title={`Delete ${f.path}`}
            className="p-1 mr-1 rounded opacity-0 group-hover:opacity-100 hover:text-destructive tech-transition shrink-0"
          >
            <Icon name="Trash2" className="w-3 h-3" />
          </button>
        </div>
      ))}
    </>
  );
}

// ─── Test scenarios (RFC §4.9) — plain-English descriptions for the Tests
// panel and the "✦ Fix with AI" seed message. Terse by design: only failing
// steps/assertions get spelled out, passing ones just show a checkmark. ────

function describeStep(step: TestScenario["steps"][number]): string {
  switch (step.action) {
    case "click":
      return `click ${step.selector}`;
    case "type":
      return `type "${step.text}" into ${step.selector}`;
    case "select":
      return `select "${step.value}" in ${step.selector}`;
    case "wait":
      return `wait ${step.ms}ms`;
  }
}

function describeAssertion(a: TestScenario["assertions"][number]): string {
  switch (a.kind) {
    case "storage":
      return `${a.table}.${a.key}${a.path ? `.${a.path}` : ""} ${a.op}${
        a.value !== undefined ? ` ${JSON.stringify(a.value)}` : ""
      }`;
    case "dom-text":
      return `${a.selector} text ${a.op} ${JSON.stringify(a.value)}`;
    case "dom-exists":
      return `${a.selector} exists = ${a.expect}`;
  }
}

/** Short first-failure summary for a result — used in the row detail and the
 * "✦ Fix with AI" seed message alike. */
function describeFailure(result: TestResult): string {
  const failedStep = result.steps.find((s) => !s.ok);
  if (failedStep) {
    return failedStep.error
      ? `${describeStep(failedStep.step)} — ${failedStep.error}`
      : `${describeStep(failedStep.step)} failed`;
  }
  const failedAssertion = result.assertions.find((a) => !a.passed);
  if (failedAssertion) {
    return `${describeAssertion(failedAssertion.assertion)} — got ${JSON.stringify(
      failedAssertion.actual
    )}`;
  }
  return result.error ?? "unknown failure";
}

/** Find-or-create the ONE builder chat session for this app. */
function ensureBuilderSession(slug: string): ChatSession {
  const name = `app:${slug}`;
  const sessions = getSessions();
  // 1. Stable id remembered per app.
  const storedId =
    typeof window !== "undefined"
      ? localStorage.getItem(SESSION_KEY_PREFIX + slug)
      : null;
  if (storedId) {
    const existing = sessions.find((s) => s.id === storedId);
    if (existing) return existing;
  }
  // 2. A session already named for this app (e.g. from another device merge).
  const named = sessions.find(
    (s) => s.agentName === BUILDER_AGENT && s.name === name
  );
  if (named) {
    try {
      localStorage.setItem(SESSION_KEY_PREFIX + slug, named.id);
    } catch {}
    return named;
  }
  // 3. Fresh session, named for the app.
  const s = createSession(BUILDER_AGENT);
  s.name = name;
  s.title = name;
  upsertSession(s);
  try {
    localStorage.setItem(SESSION_KEY_PREFIX + slug, s.id);
  } catch {}
  return s;
}

// ─── Publish modal ────────────────────────────────────────────────────────

function PublishModal({
  app,
  testStatus,
  onClose,
  onPublished,
}: {
  app: AppMeta;
  /** Current aggregate test result ({passed, total}), or null when there are
   * no scenarios yet — informational only, never blocks Publish (RFC §4.9). */
  testStatus: { passed: number; total: number } | null;
  onClose: () => void;
  onPublished: () => void;
}) {
  const [notes, setNotes] = useState("");
  const [visibility, setVisibility] = useState<"private" | "org" | "people">(
    () => (app.visibility === "people" ? "people" : "private")
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Org-wide publish with a write scope goes to the Approvals inbox instead
  // of going live (docs/app-workshop/README.md §5) — a distinct success
  // state so we don't navigate to a run page that would 404 or show the
  // stale live version.
  const [pendingReview, setPendingReview] = useState(false);

  // "Specific people…" sharing — a chip list of viewer emails granted `use`
  // (§4.8). Prefilled from existing grants so re-opening Publish shows who
  // already has access; editor/owner grants (role !== "use") are a
  // deliberate scope cut, this picker only manages viewer-level sharing.
  const [shareEmails, setShareEmails] = useState<string[]>([]);
  const [emailInput, setEmailInput] = useState("");
  // Set after a successful publish if one or more invite POSTs failed —
  // shown instead of navigating away so the note is actually visible.
  const [inviteWarning, setInviteWarning] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/apps/${encodeURIComponent(app.slug)}/grants`
        );
        if (!res.ok) return;
        const data = (await res.json()) as GrantEntry[];
        const subjects = Array.isArray(data)
          ? data.filter((g) => g.role === "use").map((g) => g.subject)
          : [];
        if (!cancelled && subjects.length > 0) setShareEmails(subjects);
      } catch {
        // Best-effort — prefill isn't critical, the owner can just re-add.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [app.slug]);

  const addEmailFromInput = useCallback(() => {
    const value = emailInput.trim();
    setEmailInput("");
    if (!value || !value.includes("@")) return;
    setShareEmails((cur) => (cur.includes(value) ? cur : [...cur, value]));
  }, [emailInput]);

  const removeShareEmail = useCallback(
    async (email: string) => {
      try {
        const res = await fetch(
          `/api/apps/${encodeURIComponent(app.slug)}/grants/${encodeURIComponent(email)}`,
          { method: "DELETE" }
        );
        // Best-effort: on failure just leave the chip, the user can retry.
        if (res.ok) {
          setShareEmails((cur) => cur.filter((e) => e !== email));
        }
      } catch {
        // Leave the chip in place.
      }
    },
    [app.slug]
  );

  /** POST a `use` grant for every current chip — an upsert, so redundant
   * re-POSTs of already-shared emails are harmless. Returns the failure
   * count (Promise.allSettled — one bad invite doesn't block the rest). */
  const syncShareGrants = useCallback(async (): Promise<number> => {
    if (shareEmails.length === 0) return 0;
    const results = await Promise.allSettled(
      shareEmails.map(async (email) => {
        const res = await fetch(
          `/api/apps/${encodeURIComponent(app.slug)}/grants`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ subject: email, role: "use" }),
          }
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
      })
    );
    return results.filter((r) => r.status === "rejected").length;
  }, [shareEmails, app.slug]);

  const publish = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/apps/${encodeURIComponent(app.slug)}/publish`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ notes: notes || undefined, visibility }),
        }
      );
      const body = (await res.json().catch(() => ({}))) as {
        error?: string;
        detail?: { error?: string; errors?: string[] } | string;
        review?: "pending" | "auto";
      };
      if (!res.ok) {
        // The platform-contract scan (RFC §4.0) rejects with a FastAPI-wrapped
        // detail listing each deviation — show them, they name the fix.
        const detail =
          typeof body.detail === "object" && body.detail ? body.detail : null;
        if (detail?.error === "conformance_failed" && detail.errors?.length) {
          setError(
            `This app deviates from the platform architecture:\n• ${detail.errors.join(
              "\n• "
            )}\nAsk the build chat to fix it — everything goes through window.cc.`
          );
          return;
        }
        setError(
          body.error ??
            (typeof body.detail === "string" ? body.detail : null) ??
            `Publish failed (HTTP ${res.status})`
        );
        return;
      }
      if (body.review === "pending") {
        // Not live yet — stay on this modal and show the review state
        // instead of navigating to a run page with nothing new to show.
        setPendingReview(true);
        return;
      }
      // Invite sync only ever matters for "people" visibility — send the
      // chips along now that the version they're being shared on exists.
      const failedInvites =
        visibility === "people" ? await syncShareGrants() : 0;
      if (failedInvites > 0) {
        setInviteWarning(
          `${failedInvites} invite${
            failedInvites === 1 ? "" : "s"
          } may not have gone through — you can retry from Share settings.`
        );
        return;
      }
      onPublished();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (pendingReview) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div className="w-full max-w-md rounded-2xl border border-border bg-card shadow-lg p-5 flex flex-col items-center gap-3 text-center">
          <div className="w-11 h-11 rounded-xl bg-warning/10 flex items-center justify-center">
            <Icon name="Clock" className="w-5 h-5 text-warning" />
          </div>
          <div>
            <h2 className="text-base font-bold text-foreground">
              Sent for admin review
            </h2>
            <p className="text-xs text-muted-foreground mt-1 max-w-xs">
              Teammates will see it once approved — you can keep building in
              the Workshop meanwhile.
            </p>
          </div>
          <Button size="none" layout="" onClick={onClose} className="mt-1 px-4 py-2 text-sm">
            Done
          </Button>
        </div>
      </div>
    );
  }

  if (inviteWarning) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
        onClick={(e) => {
          if (e.target === e.currentTarget) onPublished();
        }}
      >
        <div className="w-full max-w-md rounded-2xl border border-border bg-card shadow-lg p-5 flex flex-col items-center gap-3 text-center">
          <div className="w-11 h-11 rounded-xl bg-warning/10 flex items-center justify-center">
            <Icon name="AlertTriangle" className="w-5 h-5 text-warning" />
          </div>
          <div>
            <h2 className="text-base font-bold text-foreground">
              Published
            </h2>
            <p className="text-xs text-muted-foreground mt-1 max-w-xs">
              {inviteWarning}
            </p>
          </div>
          <Button size="none" layout="" onClick={onPublished} className="mt-1 px-4 py-2 text-sm">
            Done
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-md rounded-2xl border border-border bg-card shadow-lg p-5 flex flex-col gap-4">
        <div>
          <h2 className="text-base font-bold text-foreground">
            Publish {app.name}
          </h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Snapshots the current draft as an immutable version and serves it
            from Custom Apps.
          </p>
        </div>

        <div>
          <label className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
            Release notes
          </label>
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="What changed?"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50 tech-transition"
          />
        </div>

        <div>
          <label className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
            Who can use it
          </label>
          <div className="flex flex-col gap-1.5">
            {(
              [
                ["private", "Only me", "Private — stays in your workshop"],
                [
                  "people",
                  "Specific people…",
                  "Pick teammates, like sharing a doc",
                ],
                [
                  "org",
                  "Everyone at Fracktal",
                  "Listed in Custom Apps for the whole team",
                ],
              ] as const
            ).map(([value, label, hint]) => (
              <label
                key={value}
                className={`flex items-center gap-2.5 rounded-lg border px-3 py-2.5 cursor-pointer tech-transition ${
                  visibility === value
                    ? "border-primary/50 bg-primary/5"
                    : "border-border hover:border-primary/30"
                }`}
              >
                <input
                  type="radio"
                  name="visibility"
                  checked={visibility === value}
                  onChange={() => setVisibility(value)}
                  className="accent-current"
                />
                <span className="text-sm text-foreground">{label}</span>
                <span className="ml-auto text-[11px] text-muted-foreground">
                  {hint}
                </span>
              </label>
            ))}
          </div>

          {/* Email-chip picker — revealed only for "people" visibility
              (mockup-workshop.html's publish modal, "share a doc" model). */}
          {visibility === "people" && (
            <div className="mt-2 flex flex-col gap-1.5 rounded-lg border border-border bg-secondary/40 p-2.5">
              {shareEmails.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {shareEmails.map((email) => (
                    <span
                      key={email}
                      className="inline-flex items-center gap-1 rounded-full border border-border bg-secondary px-2.5 py-1 text-xs text-foreground"
                    >
                      {email}
                      <Button variant="text" size="none" radius="keep" layout="" type="button" onClick={() => removeShareEmail(email)} aria-label={`Remove ${email}`} className="rounded-full p-0.5">
                        <Icon name="X" className="w-3 h-3" />
                      </Button>
                    </span>
                  ))}
                </div>
              )}
              <input
                value={emailInput}
                onChange={(e) => setEmailInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    addEmailFromInput();
                  }
                }}
                placeholder="Add an email, press Enter"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50 tech-transition"
              />
            </div>
          )}
        </div>

        {/* Test status — informational only, never blocks Publish. */}
        {testStatus && testStatus.total > 0 && (
          <div
            className={`flex items-center gap-1.5 text-xs rounded-lg px-3 py-2 ${
              testStatus.passed === testStatus.total
                ? "text-success bg-success/10"
                : "text-warning bg-warning/10"
            }`}
          >
            {testStatus.passed === testStatus.total ? (
              <Icon name="CheckCircle2" className="w-3.5 h-3.5 shrink-0" />
            ) : (
              <Icon name="AlertTriangle" className="w-3.5 h-3.5 shrink-0" />
            )}
            <span>
              {testStatus.passed === testStatus.total
                ? `All ${testStatus.total} test scenario${
                    testStatus.total === 1 ? "" : "s"
                  } passing`
                : `${testStatus.total - testStatus.passed} of ${
                    testStatus.total
                  } test scenarios failing — you can still publish`}
            </span>
          </div>
        )}

        {error && (
          <div className="flex items-start gap-1.5 text-xs text-destructive whitespace-pre-line">
            <Icon name="X" className="w-3.5 h-3.5 shrink-0 mt-0.5" /> <span>{error}</span>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="none" layout="" onClick={onClose} className="px-3 sm:px-4 py-2 text-sm">
            Cancel
          </Button>
          <Button size="lg" layout="flex items-center" onClick={publish} disabled={busy}>
            {busy ? (
              <Icon name="Loader2" className="w-4 h-4 animate-spin" />
            ) : (
              <Icon name="Rocket" className="w-4 h-4" />
            )}
            Publish
          </Button>
        </div>
      </div>
    </div>
  );
}

// ─── Workshop ─────────────────────────────────────────────────────────────

function Workshop({ slug }: { slug: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const monacoTheme = useMonacoTheme();

  const [app, setApp] = useState<AppMeta | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [files, setFiles] = useState<AppFile[]>([]);
  const [view, setView] = useState<"preview" | "code" | "tests">("preview");
  // Preview device — desktop (fills the pane) or a phone-width frame, so an
  // app can be checked at both sizes without leaving the Workshop.
  const [previewDevice, setPreviewDevice] = useState<"desktop" | "mobile">(
    "desktop",
  );
  const [showPublish, setShowPublish] = useState(false);

  // Simple (chat + preview only) vs Advanced (adds Code/Tests + the file
  // editor) — a standing per-browser preference, not per-app, so it carries
  // across every app the viewer opens.
  const [advanced, setAdvanced] = useState(false);
  // Reading a client-only external system (localStorage) on mount; SSR has
  // no access to it, so a lazy useState initializer isn't an option here
  // (same rationale as the fetchFiles-on-mount carve-out below).
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setAdvanced(localStorage.getItem(ADVANCED_VIEW_STORAGE_KEY) === "1");
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */
  const toggleAdvanced = useCallback(() => {
    setAdvanced((cur) => {
      const next = !cur;
      localStorage.setItem(ADVANCED_VIEW_STORAGE_KEY, next ? "1" : "0");
      if (!next) setView("preview");
      return next;
    });
  }, []);

  // Mobile: chat and the preview/code/tests pane are full-screen alternatives
  // (desktop shows both side by side), switched via AppShell's bottom nav —
  // same "one active pane at a time, driven by a shared window event" pattern
  // as the email/tasks/whatsapp mobile layouts (AppShell.tsx's
  // MobileBottomNavInner dispatches "cc-mobile-nav"; this page owns the
  // "workshop-*" detail values). Defaults to chat: that's where a session
  // starts (there's nothing to preview yet on a fresh app).
  const { isMobile } = useViewMode();
  const { open: openMobileDrawer, close: closeMobileDrawer } = useMobileDrawer();
  const [mobilePane, setMobilePane] = useState<"chat" | "main">("chat");
  useEffect(() => {
    const onNav = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail;
      if (detail === "workshop-chat") {
        setMobilePane("chat");
      } else if (detail === "workshop-preview") {
        setMobilePane("main");
        setView("preview");
      } else if (detail === "workshop-code") {
        setMobilePane("main");
        setAdvanced(true);
        setView("code");
      } else if (detail === "workshop-tests") {
        setMobilePane("main");
        setAdvanced(true);
        setView("tests");
      }
    };
    window.addEventListener("cc-mobile-nav", onNav);
    return () => window.removeEventListener("cc-mobile-nav", onNav);
  }, []);

  // Builder chat session (one per app).
  const [chatSession, setChatSession] = useState<ChatSession | null>(null);
  const [workspaceBound, setWorkspaceBound] = useState(false);
  const [pendingInput, setPendingInput] = useState<string | undefined>(() => {
    const seed = searchParams.get("seed");
    return seed ? seed : undefined;
  });

  // Draft preview.
  const [draftBundle, setDraftBundle] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const lastBundleRef = useRef<string | null>(null);

  // Console drawer (frame errors mirrored by the cc SDK, newest-first).
  const [consoleEvents, setConsoleEvents] = useState<CcConsoleEvent[]>([]);
  const [consoleOpen, setConsoleOpen] = useState(false);

  // Checkpoints popover (topbar History icon).
  const [checkpoints, setCheckpoints] = useState<Checkpoint[] | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [confirmSha, setConfirmSha] = useState<string | null>(null);
  const [restoringSha, setRestoringSha] = useState<string | null>(null);
  const historyRef = useRef<HTMLDivElement | null>(null);

  // Code view (Advanced) — fileContent is the last-loaded/-saved value,
  // editedContent is the editor's live buffer; they diverge while dirty.
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [editedContent, setEditedContent] = useState<string | null>(null);
  const [savingFile, setSavingFile] = useState(false);
  const [buildStatus, setBuildStatus] = useState<"idle" | "building" | "error">(
    "idle"
  );
  const [buildError, setBuildError] = useState<string | null>(null);
  const [showNewFile, setShowNewFile] = useState(false);
  const [newFileName, setNewFileName] = useState("");
  const [deletePath, setDeletePath] = useState<string | null>(null);
  const [collapsedDirs, setCollapsedDirs] = useState<Set<string>>(new Set());
  const fileDirty = editedContent !== null && editedContent !== fileContent;

  // Tests panel (RFC §4.9) — scenarios authored conversationally by the
  // builder into tests.json, run client-side by testRunner.ts against an
  // ephemeral in-memory `cc` store. testResults is keyed by scenario id so a
  // partial re-run (single "Run" click) only touches the rows involved.
  const [testScenarios, setTestScenarios] = useState<TestScenario[]>([]);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>(
    {}
  );
  const testResultsRef = useRef<Record<string, TestResult>>({});
  const [runningTestIds, setRunningTestIds] = useState<Set<string>>(
    new Set()
  );
  const [expandedTestId, setExpandedTestId] = useState<string | null>(null);
  // Regressions surfaced since the last run — a scenario that was passing
  // (or never run) and is now failing, mirroring the console drawer's
  // "✦ Fix with AI" card (dismissible, re-surfaces on a fresh regression
  // even if an earlier one for the same scenario was dismissed).
  const [newlyFailingTests, setNewlyFailingTests] = useState<
    Record<string, TestResult>
  >({});
  const [dismissedFailingTests, setDismissedFailingTests] = useState<
    Set<string>
  >(new Set());

  // New `tool:` scopes the builder has added to app.json since the Workshop
  // opened, surfaced as a dismissible "New capability requested" card above
  // the composer (no dedicated AG-UI event for this in Phase 2a — derived
  // purely from re-fetching app meta on the existing refresh paths).
  const seenToolScopesRef = useRef<Set<string> | null>(null);
  const [newCapabilities, setNewCapabilities] = useState<string[]>([]);
  const [dismissedCapabilities, setDismissedCapabilities] = useState<
    Set<string>
  >(new Set());
  const noteManifestScopes = useCallback(
    (manifest: Record<string, unknown> | undefined) => {
      const scopes = toolScopes(manifest);
      const prev = seenToolScopesRef.current;
      seenToolScopesRef.current = new Set(scopes);
      if (prev === null) return; // first observation is the baseline, not "new"
      const fresh = scopes.filter((s) => !prev.has(s));
      if (fresh.length === 0) return;
      setNewCapabilities((cur) => Array.from(new Set([...cur, ...fresh])));
    },
    []
  );
  const dismissCapability = useCallback((scope: string) => {
    setDismissedCapabilities((cur) => {
      const next = new Set(cur);
      next.add(scope);
      return next;
    });
  }, []);
  const visibleCapabilities = useMemo(
    () => newCapabilities.filter((s) => !dismissedCapabilities.has(s)),
    [newCapabilities, dismissedCapabilities]
  );

  // Tool-confirm toast (bottom-right) — mirrors the run page (RFC §4.4): a
  // cc.tools.call() in the DRAFT preview hit a destructive tool with no
  // remembered grant. Without this, testing a just-added scope inside the
  // Workshop would hard-fail instead of letting the builder try it.
  const [pendingConfirm, setPendingConfirm] = useState<PendingToolConfirm | null>(
    null
  );
  const [rememberTool, setRememberTool] = useState(false);
  const onToolConfirm = useCallback(
    (req: CcToolConfirmRequest) =>
      new Promise<CcToolConfirmDecision>((resolve) => {
        setRememberTool(false);
        setPendingConfirm({ ...req, resolve });
      }),
    []
  );

  // Broker the DRAFT frame's cc.* calls — same bridge as production so the
  // preview behaves identically to the published app (RFC §4.3). Console
  // notifications feed the drawer under the preview.
  const onConsoleEvent = useCallback((e: CcConsoleEvent) => {
    setConsoleEvents((prev) => [e, ...prev].slice(0, CONSOLE_EVENT_CAP));
  }, []);
  useCcBridge(slug, { mode: "draft", onConsoleEvent, onToolConfirm });

  // ── App meta + files ────────────────────────────────────────────────
  // Fetch-on-mount wiring: same pattern (and lint carve-out) as
  // tasks/components/AssistantRail.tsx.
  /* eslint-disable react-hooks/set-state-in-effect */
  const fetchFiles = useCallback(async () => {
    try {
      const res = await fetch(`/api/apps/${encodeURIComponent(slug)}/files`);
      if (!res.ok) return;
      // GET /apps/{slug}/files returns a bare array (no {files: [...]}
      // envelope — see gateway/routes/apps/files.py).
      const data = (await res.json()) as AppFile[];
      setFiles(Array.isArray(data) ? data : []);
    } catch {}
  }, [slug]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/apps/${encodeURIComponent(slug)}`);
        if (!res.ok) {
          if (!cancelled)
            setLoadError(
              res.status === 404 ? "App not found." : `HTTP ${res.status}`
            );
          return;
        }
        // GET /apps/{slug} returns a bare AppDetail (no {app: ...} envelope).
        const data = (await res.json()) as AppMeta;
        if (!cancelled && data) {
          setApp(data);
          noteManifestScopes(data.manifest);
        }
      } catch (e) {
        if (!cancelled) setLoadError(String(e));
      }
    })();
    fetchFiles();
    return () => {
      cancelled = true;
    };
  }, [slug, fetchFiles, noteManifestScopes]);

  // ── Tests: tests.json read (write stays conversational — the builder
  // edits the file directly, this component only ever reads it) ─────────
  const fetchTestScenarios = useCallback(async (): Promise<TestScenario[]> => {
    try {
      const res = await fetch(
        `/api/apps/${encodeURIComponent(slug)}/files/content?path=tests.json`
      );
      // 404 (no tests.json yet) is not an error — just no scenarios.
      if (!res.ok) return [];
      const text = await res.text();
      try {
        const data: unknown = JSON.parse(text);
        return Array.isArray(data) ? (data as TestScenario[]) : [];
      } catch {
        return []; // malformed tests.json — never throw, just show none
      }
    } catch {
      return [];
    }
  }, [slug]);

  useEffect(() => {
    let cancelled = false;
    fetchTestScenarios().then((scenarios) => {
      if (!cancelled) setTestScenarios(scenarios);
    });
    return () => {
      cancelled = true;
    };
  }, [fetchTestScenarios]);

  /** Re-fetch app meta and check for newly-declared `tool:` scopes. */
  const refreshAppMeta = useCallback(async () => {
    try {
      const res = await fetch(`/api/apps/${encodeURIComponent(slug)}`);
      if (!res.ok) return;
      // GET /apps/{slug} returns a bare AppDetail (no {app: ...} envelope).
      const data = (await res.json()) as AppMeta;
      if (!data) return;
      setApp(data);
      noteManifestScopes(data.manifest);
    } catch {
      // Best-effort — the next refresh (poll or activity) will catch it.
    }
  }, [slug, noteManifestScopes]);

  // ── Builder session wiring (critical) ───────────────────────────────
  // Only editors receive workspace_path from the API; without it the chat is
  // replaced by a read-only notice.
  useEffect(() => {
    if (!app) return;
    if (!app.workspace_path) return;
    const s = ensureBuilderSession(slug);
    setChatSession(s);
    // Bind the builder's working directory to the app workspace (idempotent).
    fetch(`/api/agent/workspace/${s.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace_path: app.workspace_path }),
    })
      .then((res) => setWorkspaceBound(res.ok))
      .catch(() => setWorkspaceBound(false));
  }, [app, slug]);

  // ── Draft preview fetch + poll ──────────────────────────────────────
  const refreshPreview = useCallback(async () => {
    setPreviewBusy(true);
    try {
      const res = await fetch(
        `/api/apps/${encodeURIComponent(slug)}/bundle?version=draft`
      );
      if (res.ok) {
        const text = await res.text();
        if (lastBundleRef.current !== text) {
          lastBundleRef.current = text;
          // New bundle reloads the frame — each rebuild starts with a clean
          // console (stale errors from the previous build would mislead).
          setConsoleEvents([]);
          setDraftBundle(text);
        }
      }
    } catch {
      // Preview refresh is best-effort.
    } finally {
      setPreviewBusy(false);
    }
  }, [slug]);

  useEffect(() => {
    refreshPreview();
    // Fallback poll so agent edits show up even if run-end sync misses; only
    // while the tab is visible.
    const t = setInterval(() => {
      if (document.visibilityState === "visible") refreshPreview();
    }, PREVIEW_POLL_MS);
    return () => clearInterval(t);
  }, [refreshPreview]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const onArtifact = useCallback(() => {
    refreshPreview();
    fetchFiles();
    refreshAppMeta();
  }, [refreshPreview, fetchFiles, refreshAppMeta]);

  // ── Checkpoints + run-end sync ──────────────────────────────────────
  const refreshCheckpoints = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/apps/${encodeURIComponent(slug)}/checkpoints`
      );
      if (!res.ok) return;
      const data = (await res.json()) as { checkpoints?: Checkpoint[] };
      setCheckpoints(Array.isArray(data.checkpoints) ? data.checkpoints : []);
    } catch {
      // Best-effort.
    }
  }, [slug]);

  /** Mirror the draft to Postgres + git checkpoint (best-effort). */
  const syncDraft = useCallback(async () => {
    try {
      await fetch(`/api/apps/${encodeURIComponent(slug)}/sync`, {
        method: "POST",
      });
    } catch {
      // Sync is best-effort — the 30 s poll still covers the preview.
    }
  }, [slug]);

  // ── Tests: run + merge results ────────────────────────────────────────
  /** Merge fresh results into state and surface any newly-broken scenario —
   * one that was passing (or never run) before this run and is failing now.
   * A dismissed pill re-surfaces if the scenario regresses again later. */
  const applyTestResults = useCallback((results: TestResult[]) => {
    const prev = testResultsRef.current;
    const freshlyFailing = results.filter((r) => {
      if (r.passed) return false;
      const prior = prev[r.scenarioId];
      return !prior || prior.passed;
    });
    const next = { ...prev };
    for (const r of results) next[r.scenarioId] = r;
    testResultsRef.current = next;
    setTestResults(next);
    if (freshlyFailing.length > 0) {
      setNewlyFailingTests((cur) => {
        const nextFailing = { ...cur };
        for (const r of freshlyFailing) nextFailing[r.scenarioId] = r;
        return nextFailing;
      });
      setDismissedFailingTests((cur) => {
        if (freshlyFailing.every((r) => !cur.has(r.scenarioId))) return cur;
        const nextDismissed = new Set(cur);
        for (const r of freshlyFailing) nextDismissed.delete(r.scenarioId);
        return nextDismissed;
      });
    }
  }, []);

  /** Manual run — one row's "Run" button, or "Run all" — against the
   * currently-loaded draft bundle. */
  const runScenarios = useCallback(
    async (scenariosToRun: TestScenario[]) => {
      if (scenariosToRun.length === 0 || !draftBundle) return;
      const ids = scenariosToRun.map((s) => s.id);
      setRunningTestIds((prev) => new Set([...prev, ...ids]));
      try {
        const results = await runAllScenarios(draftBundle, scenariosToRun, {
          slug,
        });
        applyTestResults(results);
      } catch {
        // Best-effort — leave prior results in place, the row still shows
        // its last known status.
      } finally {
        setRunningTestIds((prev) => {
          const next = new Set(prev);
          ids.forEach((id) => next.delete(id));
          return next;
        });
      }
    },
    [draftBundle, slug, applyTestResults]
  );

  /** Auto-run after a builder turn — fetches a FRESH bundle + tests.json
   * directly (not the debounced setTimeout's stale closures) so the run
   * reflects what the builder just wrote. */
  const runTestsAfterSync = useCallback(async () => {
    try {
      const [bundleRes, scenarios] = await Promise.all([
        fetch(`/api/apps/${encodeURIComponent(slug)}/bundle?version=draft`),
        fetchTestScenarios(),
      ]);
      setTestScenarios(scenarios);
      if (scenarios.length === 0 || !bundleRes.ok) return;
      const bundleText = await bundleRes.text();
      const ids = scenarios.map((s) => s.id);
      setRunningTestIds((prev) => new Set([...prev, ...ids]));
      try {
        const results = await runAllScenarios(bundleText, scenarios, {
          slug,
        });
        applyTestResults(results);
      } finally {
        setRunningTestIds((prev) => {
          const next = new Set(prev);
          ids.forEach((id) => next.delete(id));
          return next;
        });
      }
    } catch {
      // Best-effort — the next activity or a manual run will catch it.
    }
  }, [slug, fetchTestScenarios, applyTestResults]);

  // When an assistant turn lands (messageCount grows), give the workspace a
  // moment to settle, then refetch the preview and mirror the draft. This is
  // the primary refresh path; the poll above is only the fallback.
  const lastMessageCountRef = useRef(0);
  const syncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleActivity = useCallback(
    (info: { messageCount: number }) => {
      if (info.messageCount <= lastMessageCountRef.current) {
        lastMessageCountRef.current = info.messageCount;
        return;
      }
      lastMessageCountRef.current = info.messageCount;
      if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
      syncTimerRef.current = setTimeout(() => {
        syncTimerRef.current = null;
        refreshPreview();
        fetchFiles();
        syncDraft().then(refreshCheckpoints);
        // The builder mentions new tool: scopes in chat, not a dedicated
        // event (Phase 2a) — catch them by re-fetching app meta here too.
        refreshAppMeta();
        // Re-run test scenarios (if any) against the freshly-synced draft —
        // same trigger as the checkpoint/preview refresh above (RFC §4.9).
        runTestsAfterSync();
      }, RUN_SYNC_DEBOUNCE_MS);
    },
    [
      refreshPreview,
      fetchFiles,
      syncDraft,
      refreshCheckpoints,
      refreshAppMeta,
      runTestsAfterSync,
    ]
  );
  useEffect(
    () => () => {
      if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    },
    []
  );

  const toggleHistory = useCallback(() => {
    const next = !showHistory;
    setShowHistory(next);
    if (next) {
      setConfirmSha(null);
      refreshCheckpoints();
    }
  }, [showHistory, refreshCheckpoints]);

  // Close the checkpoints popover on outside click — desktop only. The
  // mobile bottom sheet isn't nested under historyRef (it's a fixed overlay,
  // not anchored to the button) and closes itself via its own backdrop tap;
  // this listener would otherwise treat every tap inside the sheet as
  // "outside" and close it before the tap's own handler ever ran.
  useEffect(() => {
    if (!showHistory || isMobile) return;
    const onDown = (e: MouseEvent) => {
      if (historyRef.current && !historyRef.current.contains(e.target as Node)) {
        setShowHistory(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [showHistory, isMobile]);

  const restoreCheckpoint = useCallback(
    async (sha: string) => {
      setRestoringSha(sha);
      try {
        const res = await fetch(
          `/api/apps/${encodeURIComponent(slug)}/restore`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sha }),
          }
        );
        if (res.ok) {
          setConfirmSha(null);
          await Promise.all([
            refreshPreview(),
            fetchFiles(),
            refreshCheckpoints(),
          ]);
        }
      } catch {
        // Leave the popover open so the user can retry.
      } finally {
        setRestoringSha(null);
      }
    },
    [slug, refreshPreview, fetchFiles, refreshCheckpoints]
  );

  // "✦ Fix with AI" — seed the build chat composer with the newest errors.
  const fixWithAi = useCallback(() => {
    const recent = consoleEvents.slice(0, 3).reverse();
    if (recent.length === 0) return;
    const lines = recent.map((e) =>
      e.stack ? `${e.message}\n${e.stack}` : e.message
    );
    setPendingInput(`Preview errors:\n${lines.join("\n")}\nPlease fix them.`);
  }, [consoleEvents]);

  // Aggregate pass/fail across all known scenarios — null hides the topbar
  // pill and the Publish modal banner when there are no scenarios at all.
  // A scenario with no result yet counts as not-passing (converges to real
  // numbers on the first auto-run / manual run).
  const testAggregate = useMemo(() => {
    if (testScenarios.length === 0) return null;
    const total = testScenarios.length;
    const passed = testScenarios.filter((s) => testResults[s.id]?.passed).length;
    return { passed, total };
  }, [testScenarios, testResults]);

  const visibleFailingTests = useMemo(
    () =>
      Object.values(newlyFailingTests).filter(
        (r) => !dismissedFailingTests.has(r.scenarioId)
      ),
    [newlyFailingTests, dismissedFailingTests]
  );
  const dismissFailingTest = useCallback((scenarioId: string) => {
    setDismissedFailingTests((cur) => {
      const next = new Set(cur);
      next.add(scenarioId);
      return next;
    });
  }, []);
  // "✦ Fix with AI" on a regressed scenario — same setPendingInput mechanism
  // as fixWithAi above, seeded with which scenario broke and why.
  const fixTestWithAi = useCallback(
    (result: TestResult) => {
      const scenario = testScenarios.find((s) => s.id === result.scenarioId);
      const name = scenario?.name ?? result.scenarioId;
      setPendingInput(
        `Test "${name}" is now failing: ${describeFailure(result)}. Please fix it.`
      );
    },
    [testScenarios]
  );

  // ── Code view file content (Advanced) ────────────────────────────────
  const selectFile = useCallback(
    async (path: string) => {
      if (
        fileDirty &&
        !window.confirm(
          `Discard unsaved changes to ${selectedPath}?`
        )
      ) {
        return;
      }
      setSelectedPath(path);
      setFileContent(null);
      setEditedContent(null);
      setBuildStatus("idle");
      setBuildError(null);
      try {
        const res = await fetch(
          `/api/apps/${encodeURIComponent(slug)}/files/content?path=${encodeURIComponent(path)}`
        );
        const text = res.ok ? await res.text() : `(failed to load ${path})`;
        setFileContent(text);
        setEditedContent(text);
      } catch (e) {
        const text = String(e);
        setFileContent(text);
        setEditedContent(text);
      }
    },
    [slug, fileDirty, selectedPath]
  );

  /** Save the editor's buffer, checkpoint, and rebuild/refresh the preview.
   * T2 apps (entry under dist/) need a rebuild before the preview reflects a
   * src/ edit; T1 apps are live the instant the write lands. */
  const saveFile = useCallback(async () => {
    if (selectedPath === null || editedContent === null || savingFile) return;
    setSavingFile(true);
    setBuildError(null);
    try {
      const res = await fetch(
        `/api/apps/${encodeURIComponent(slug)}/files/content`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: selectedPath, content: editedContent }),
        }
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        setBuildStatus("error");
        setBuildError(detail?.detail || `Save failed (HTTP ${res.status})`);
        return;
      }
      setFileContent(editedContent);
      fetchFiles();

      // Only src/ edits change the bundle — app.json/tests.json on a T2 app
      // don't need a rebuild to take effect.
      if (isBuildBasedApp(app?.manifest) && selectedPath.startsWith("src/")) {
        setBuildStatus("building");
        try {
          const buildRes = await fetch(
            `/api/apps/${encodeURIComponent(slug)}/build`,
            { method: "POST" }
          );
          const result = await buildRes.json().catch(() => ({}));
          if (buildRes.ok && result.built) {
            setBuildStatus("idle");
            await syncDraft();
            refreshPreview();
          } else {
            setBuildStatus("error");
            setBuildError(result.error || "Build failed");
          }
        } catch (e) {
          setBuildStatus("error");
          setBuildError(String(e));
        }
      } else {
        setBuildStatus("idle");
        await syncDraft();
        refreshPreview();
      }
    } catch (e) {
      setBuildStatus("error");
      setBuildError(String(e));
    } finally {
      setSavingFile(false);
    }
  }, [
    slug,
    selectedPath,
    editedContent,
    savingFile,
    app,
    fetchFiles,
    syncDraft,
    refreshPreview,
  ]);

  // Cmd/Ctrl+S saves the open file instead of triggering the browser's save
  // dialog, only while Advanced + Code view + a dirty file is actually open.
  useEffect(() => {
    if (!advanced || view !== "code") return;
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        if (fileDirty) saveFile();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [advanced, view, fileDirty, saveFile]);

  const createFile = useCallback(async () => {
    const path = newFileName.trim();
    if (!path) return;
    try {
      const res = await fetch(
        `/api/apps/${encodeURIComponent(slug)}/files/content`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, content: "" }),
        }
      );
      if (res.ok) {
        setShowNewFile(false);
        setNewFileName("");
        await fetchFiles();
        selectFile(path);
      }
    } catch {
      // Best-effort — the empty-editor state makes a silent failure obvious.
    }
  }, [slug, newFileName, fetchFiles, selectFile]);

  const deleteFile = useCallback(
    async (path: string) => {
      try {
        const res = await fetch(
          `/api/apps/${encodeURIComponent(slug)}/files/content?path=${encodeURIComponent(path)}`,
          { method: "DELETE" }
        );
        if (res.ok) {
          if (selectedPath === path) {
            setSelectedPath(null);
            setFileContent(null);
            setEditedContent(null);
          }
          await fetchFiles();
          await syncDraft();
        }
      } finally {
        setDeletePath(null);
      }
    },
    [slug, selectedPath, fetchFiles, syncDraft]
  );

  const toggleDir = useCallback((dirPath: string) => {
    setCollapsedDirs((cur) => {
      const next = new Set(cur);
      if (next.has(dirPath)) next.delete(dirPath);
      else next.add(dirPath);
      return next;
    });
  }, []);

  const fileTree = useMemo(() => buildFileTree(files), [files]);

  // ── Persona: workspace contract for the builder ─────────────────────
  const persona = useMemo(() => {
    if (!app) return undefined;
    const fileList =
      files.length > 0
        ? files.map((f) => f.path).join(", ")
        : "(empty workspace)";
    return [
      `You are the app-builder for the Metorite custom app "${app.name}" (slug: ${app.slug}).`,
      `You are building the app in this workspace. Entry file: ${manifestEntry(app.manifest)}.`,
      `Workspace files: ${fileList}.`,
    ].join("\n");
  }, [app, files]);

  const srcDoc = useMemo(
    () =>
      draftBundle ? buildAppSrcDoc(draftBundle, { slug, mode: "draft" }) : null,
    [draftBundle, slug]
  );
  // The sandboxed frame can't fetch its own icons (no network) — pre-resolve
  // whatever `ccIcon(...)`/`data-cc-icon` names the app's own HTML asks for
  // into inline SVG here, same mechanism the chat-artifacts renderer already
  // uses for generative UI (GenerativeUINode.tsx).
  const previewIcons = useMemo(
    () => (srcDoc ? extractCcIconNames(srcDoc) : []),
    [srcDoc]
  );

  // ── Render ──────────────────────────────────────────────────────────
  if (loadError) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <p className="text-sm text-destructive">{loadError}</p>
        <Button variant="secondary" size="none" layout="" onClick={() => router.push("/build/apps")} className="px-3 sm:px-4 py-2 text-sm">
          Back to Custom Apps
        </Button>
      </div>
    );
  }

  if (!app) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <Icon name="Loader2" className="w-5 h-5 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Opening Workshop…</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* ── Topbar ──────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-3 sm:px-4 py-2 border-b border-border bg-card shrink-0">
        <Button variant="ghost" size="none" layout="flex items-center" onClick={() => router.push("/build/apps")} className="gap-1.5 px-2 py-1.5 text-sm">
          <Icon name="ArrowLeft" className="w-4 h-4" /> Apps
        </Button>
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-7 h-7 rounded-lg border border-border bg-gradient-to-br from-primary/20 to-accent/15 flex items-center justify-center text-sm shrink-0">
            {app.icon || "▦"}
          </div>
          <span className="text-sm font-bold text-foreground truncate">
            {app.name}
          </span>
          <span className="text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full text-warning bg-warning/10 shrink-0">
            Draft
          </span>
        </div>

        {/* The bottom nav's Chat/Preview/Code/Tests tabs cover this switch on
            mobile — a second, redundant control here would just eat space
            that phones don't have. */}
        <div className={isMobile ? "flex-1" : "flex-1 flex justify-center"}>
          {!isMobile && (
          <Tabs
            tabs={
              advanced
                ? [
                    { id: "preview", label: "Preview" },
                    { id: "code", label: "Code" },
                    { id: "tests", label: "Tests", icon: "FlaskConical" },
                  ]
                : [{ id: "preview", label: "Preview" }]
            }
            activeTab={view}
            onTabChange={(id) => setView(id as "preview" | "code" | "tests")}
            variant="segmented"
            className="border-b-0! px-0! sm:px-0! pt-0! pb-0!"
          />
          )}
        </div>

        {/* Redundant on mobile — tapping Code/Tests in the bottom nav already
            switches into Advanced; one more control here is just clutter a
            phone-width header doesn't have room for. */}
        {!isMobile && (
        <button
          onClick={toggleAdvanced}
          title={
            advanced
              ? "Switch to Simple (chat + preview only)"
              : "Switch to Advanced (code editor, tests, file access)"
          }
          className={`flex items-center gap-1.5 px-2 py-1.5 rounded-lg border border-border text-xs font-medium tech-transition shrink-0 ${
            advanced
              ? "text-primary bg-primary/10"
              : "text-muted-foreground hover:bg-secondary"
          }`}
        >
          <Icon name="Wrench" className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">
            {advanced ? "Advanced" : "Simple"}
          </span>
        </button>
        )}

        <div className="relative shrink-0" ref={historyRef}>
          <button
            onClick={toggleHistory}
            title="Checkpoints"
            className={`p-2 rounded-lg border border-border tech-transition ${
              showHistory
                ? "text-primary bg-primary/10"
                : "text-muted-foreground hover:bg-secondary"
            }`}
          >
            <Icon name="History" className="w-4 h-4" />
          </button>

          {/* Desktop: a popover anchored to this button. On mobile this
              button can sit anywhere in the header, not just near the right
              edge — an absolute-positioned popover anchored `right-0` to it
              can render mostly off-screen (it did: reported as a broken
              "confirm" step that barely rendered). A bottom sheet has no
              anchor-point math to get wrong, so mobile gets one instead. */}
          {showHistory && !isMobile && (
            <div className="absolute right-0 top-full mt-2 w-80 rounded-xl border border-border bg-card shadow-lg z-40 p-3 flex flex-col gap-1 text-xs">
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground px-2 py-1">
                Checkpoints
              </span>
              <CheckpointsPanel
                checkpoints={checkpoints}
                confirmSha={confirmSha}
                setConfirmSha={setConfirmSha}
                restoringSha={restoringSha}
                restoreCheckpoint={restoreCheckpoint}
              />
            </div>
          )}
        </div>

        {showHistory && isMobile && (
          <div className="fixed inset-0 z-[70]">
            <div
              className="absolute inset-0 bg-black/60"
              onClick={() => setShowHistory(false)}
            />
            <aside className="absolute inset-x-0 bottom-0 flex max-h-[75%] flex-col rounded-t-2xl border-t border-border bg-card shadow-2xl">
              <div className="flex justify-center pt-2 pb-1">
                <div className="w-10 h-1 rounded-full bg-muted-foreground/30" />
              </div>
              <span className="px-4 pb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                Checkpoints
              </span>
              <div className="flex-1 min-h-0 overflow-y-auto px-3 pb-safe text-xs">
                <CheckpointsPanel
                  checkpoints={checkpoints}
                  confirmSha={confirmSha}
                  setConfirmSha={setConfirmSha}
                  restoringSha={restoringSha}
                  restoreCheckpoint={restoreCheckpoint}
                />
              </div>
            </aside>
          </div>
        )}

        {/* Aggregate test badge — hidden with zero scenarios, click jumps to
            the Tests view (RFC §4.9's "compact pass/fail badge near
            Publish"). Redundant on mobile — the bottom nav's Tests tab
            already covers this, and the header has no room to spare. */}
        {!isMobile && advanced && testAggregate && (
          <button
            onClick={() => setView("tests")}
            title="Open the Tests view"
            className={`font-mono text-[10.5px] px-2 py-1 rounded-full border tech-transition shrink-0 ${
              testAggregate.passed === testAggregate.total
                ? "text-success border-success/30 bg-success/10 hover:bg-success/20"
                : "text-destructive border-destructive/30 bg-destructive/10 hover:bg-destructive/20"
            }`}
          >
            {testAggregate.passed === testAggregate.total ? "✓" : "✗"}{" "}
            {testAggregate.passed}/{testAggregate.total} tests
          </button>
        )}

        <Button size="lg" layout="flex items-center" onClick={() => setShowPublish(true)} className="shrink-0">
          <Icon name="Rocket" className="w-4 h-4" /> Publish
        </Button>
      </div>

      {/* ── Split main ──────────────────────────────────────────────── */}
      {/* Desktop: both panes side by side. Mobile: one full-screen pane at a
          time (mobilePane), switched via the bottom nav — `hidden` rather
          than unmounting so the chat session/editor/preview iframe stay
          alive underneath instead of resetting on every tab switch. */}
      <div className="flex-1 flex min-h-0">
        {/* Left: preview / code */}
        <div
          className={`flex-1 min-w-0 flex-col min-h-0 ${
            isMobile ? (mobilePane === "main" ? "flex" : "hidden") : "flex"
          }`}
        >
          {view === "preview" ? (
            <>
              {/* Preview toolbar */}
              <div className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0">
                <button
                  onClick={refreshPreview}
                  title="Reload preview"
                  className="p-1.5 rounded-lg border border-border text-muted-foreground hover:bg-secondary tech-transition"
                >
                  <Icon name="RefreshCw"
                    className={`w-3.5 h-3.5 ${previewBusy ? "animate-spin" : ""}`}
                  />
                </button>
                <span className="font-mono text-[10.5px] px-2 py-0.5 rounded-full border border-border text-warning">
                  draft
                </span>
                {app.live_version ? (
                  <span className="font-mono text-[10.5px] px-2 py-0.5 rounded-full border border-border text-success">
                    v{app.live_version} live
                  </span>
                ) : null}
                <div className="flex-1" />
                {/* Desktop/mobile preview — same srcDoc, just a narrower
                    frame; lets the app be checked at both sizes without
                    leaving the Workshop. */}
                <div className="flex items-center rounded-lg border border-border p-0.5 shrink-0">
                  <button
                    onClick={() => setPreviewDevice("desktop")}
                    title="Preview at desktop width"
                    className={`p-1 rounded-md tech-transition ${
                      previewDevice === "desktop"
                        ? "text-primary bg-primary/10"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Icon name="Monitor" className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => setPreviewDevice("mobile")}
                    title="Preview at phone width (390px)"
                    className={`p-1 rounded-md tech-transition ${
                      previewDevice === "mobile"
                        ? "text-primary bg-primary/10"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Icon name="Smartphone" className="w-3.5 h-3.5" />
                  </button>
                </div>
                <span className="font-mono text-[10px] text-muted-foreground hidden sm:block">
                  sandboxed · opaque origin
                </span>
              </div>
              <div
                className={`flex-1 min-h-0 flex flex-col ${
                  previewDevice === "mobile" ? "items-center bg-secondary/30 overflow-y-auto py-3" : ""
                }`}
              >
                {srcDoc ? (
                  previewDevice === "mobile" ? (
                    <div className="w-[390px] max-w-full shrink-0 h-[780px] max-h-full rounded-[2rem] border-4 border-border overflow-hidden shadow-lg bg-background">
                      <SandboxedHtml chromeless html={srcDoc} iconNames={previewIcons} />
                    </div>
                  ) : (
                    <SandboxedHtml chromeless html={srcDoc} iconNames={previewIcons} />
                  )
                ) : (
                  <div className="flex flex-col items-center justify-center flex-1 gap-3 text-center px-6">
                    <Icon name="Sparkles" className="w-6 h-6 text-muted-foreground/50" />
                    <p className="text-sm font-medium text-foreground">
                      No preview yet
                    </p>
                    <p className="text-xs text-muted-foreground max-w-xs">
                      Ask the builder to scaffold the app — the preview appears
                      as soon as it writes the first draft.
                    </p>
                  </div>
                )}
              </div>

              {/* Console drawer — frame errors mirrored by the cc SDK. */}
              <div className="border-t border-border bg-card shrink-0">
                <div className="flex items-center gap-2 px-3 py-1.5">
                  <Button variant="text" size="none" layout="flex items-center" onClick={() => setConsoleOpen((o) => !o)} className="gap-1.5 font-mono text-[11px]">
                    {consoleOpen ? (
                      <Icon name="ChevronDown" className="w-3 h-3 shrink-0" />
                    ) : (
                      <Icon name="ChevronRight" className="w-3 h-3 shrink-0" />
                    )}
                    {consoleEvents.length === 0 ? (
                      <span className="text-success">Console · clean</span>
                    ) : (
                      <span className="text-destructive">
                        Console · {consoleEvents.length}{" "}
                        {consoleEvents.length === 1 ? "error" : "errors"}
                      </span>
                    )}
                  </Button>
                  <div className="flex-1" />
                  {consoleEvents.length > 0 && (
                    <button
                      onClick={fixWithAi}
                      className="font-mono text-[11px] text-primary hover:opacity-80 tech-transition shrink-0"
                    >
                      ✦ Fix with AI
                    </button>
                  )}
                </div>
                {consoleOpen && (
                  <div className="max-h-40 overflow-auto border-t border-border px-3 py-2 flex flex-col gap-2">
                    {consoleEvents.length === 0 ? (
                      <p className="font-mono text-[11px] text-muted-foreground">
                        No errors captured since the last rebuild.
                      </p>
                    ) : (
                      consoleEvents.map((e, i) => (
                        <div key={i} className="font-mono text-[11px]">
                          <div className="text-destructive break-words">
                            {e.message}
                          </div>
                          {e.stack && (
                            <pre className="text-[10px] text-muted-foreground whitespace-pre-wrap break-words mt-0.5">
                              {e.stack}
                            </pre>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            </>
          ) : view === "code" ? (
            <div className="relative flex-1 min-h-0 flex">
              {/* File tree — inline column on desktop; on mobile it's reached
                  via the "Files" button below (a drawer, same secondary-panel
                  pattern every other mobile page uses), not shown inline —
                  there isn't room for a fixed column next to the editor. */}
              <div
                className={
                  isMobile
                    ? "hidden"
                    : "w-56 shrink-0 border-r border-border overflow-y-auto p-2 flex flex-col"
                }
              >
                <div className="flex items-center justify-between px-2 py-1.5">
                  <span className="text-[10px] uppercase tracking-wide text-muted-foreground truncate">
                    {app.slug}
                  </span>
                  <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={() => setShowNewFile((s) => !s)} title="New file" className="rounded shrink-0">
                    <Icon name="Plus" className="w-3.5 h-3.5" />
                  </Button>
                </div>
                {showNewFile && (
                  <div className="flex items-center gap-1 px-2 pb-1.5">
                    <input
                      autoFocus
                      value={newFileName}
                      onChange={(e) => setNewFileName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") createFile();
                        if (e.key === "Escape") setShowNewFile(false);
                      }}
                      placeholder="src/Widget.tsx"
                      className="flex-1 min-w-0 rounded-md border border-border bg-background px-1.5 py-1 font-mono text-[11px] text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                    <Button size="none" radius="keep" layout="" onClick={createFile} className="text-[10px] rounded-md px-1.5 py-1 shrink-0">
                      Add
                    </Button>
                  </div>
                )}
                {files.length === 0 ? (
                  <p className="text-[11px] text-muted-foreground px-2 py-1">
                    No files yet.
                  </p>
                ) : (
                  <FileTreeView
                    dir={fileTree}
                    pathPrefix=""
                    selectedPath={selectedPath}
                    onSelect={selectFile}
                    onDelete={setDeletePath}
                    collapsedDirs={collapsedDirs}
                    onToggleDir={toggleDir}
                  />
                )}
              </div>
              {/* Editor */}
              <div className="flex-1 min-w-0 flex flex-col min-h-0">
                <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border shrink-0">
                  {isMobile && (
                    <Button variant="secondary" size="none" radius="keep" layout="flex items-center" onClick={() =>
                        openMobileDrawer(
                          <div className="flex flex-col p-2">
                            <div className="px-2 py-1.5 text-[10px] uppercase tracking-wide text-muted-foreground truncate">
                              {app.slug} — files
                            </div>
                            {files.length === 0 ? (
                              <p className="text-[11px] text-muted-foreground px-2 py-1">
                                No files yet.
                              </p>
                            ) : (
                              <FileTreeView
                                dir={fileTree}
                                pathPrefix=""
                                selectedPath={selectedPath}
                                onSelect={(p) => {
                                  selectFile(p);
                                  closeMobileDrawer();
                                }}
                                onDelete={setDeletePath}
                                collapsedDirs={collapsedDirs}
                                onToggleDir={toggleDir}
                              />
                            )}
                          </div>
                        )
                      } title="Files" className="gap-1.5 text-[11px] rounded-md px-2 py-1 shrink-0">
                      <Icon name="Folder" className="w-3 h-3" />
                      Files
                    </Button>
                  )}
                  {selectedPath ? (
                    <>
                      <Icon name="FileCode" className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                      <span className="font-mono text-[11.5px] text-foreground truncate">
                        {selectedPath}
                      </span>
                      {fileDirty && (
                        <span
                          title="Unsaved changes"
                          className="w-1.5 h-1.5 rounded-full bg-warning shrink-0"
                        />
                      )}
                    </>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      Select or create a file
                    </span>
                  )}
                  <div className="flex-1" />
                  {buildStatus === "building" && (
                    <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground shrink-0">
                      <Icon name="Loader2" className="w-3 h-3 animate-spin" /> Building…
                    </span>
                  )}
                  {selectedPath && (
                    <Button variant="secondary" size="none" radius="keep" layout="flex items-center" onClick={saveFile} disabled={!fileDirty || savingFile} title="Save (⌘S / Ctrl+S)" className="gap-1.5 text-[11px] rounded-md px-2 py-1 shrink-0">
                      {savingFile ? (
                        <Icon name="Loader2" className="w-3 h-3 animate-spin" />
                      ) : (
                        <Icon name="Save" className="w-3 h-3" />
                      )}
                      Save
                    </Button>
                  )}
                </div>
                <div className="flex-1 min-h-0">
                  {selectedPath === null ? (
                    <div className="flex flex-col items-center justify-center h-full gap-2 text-center px-6">
                      <Icon name="FileCode" className="w-6 h-6 text-muted-foreground/50" />
                      <p className="text-xs text-muted-foreground max-w-xs">
                        Select a file to edit, or use{" "}
                        <Icon name="Plus" className="w-3 h-3 inline" /> to create one.
                        Uploaded assets (via the chat&apos;s attach button)
                        land under <code>inputs/</code>.
                      </p>
                    </div>
                  ) : editedContent === null ? (
                    <div className="p-4">
                      <Icon name="Loader2" className="w-4 h-4 animate-spin text-muted-foreground" />
                    </div>
                  ) : (
                    <Editor
                      height="100%"
                      language={monacoLanguage(selectedPath)}
                      theme={monacoTheme}
                      value={editedContent}
                      onChange={(v) => setEditedContent(v ?? "")}
                      options={{
                        minimap: { enabled: false },
                        fontSize: 12.5,
                        wordWrap: "on",
                        scrollBeyondLastLine: false,
                        automaticLayout: true,
                      }}
                    />
                  )}
                </div>
                {buildError && (
                  <div className="flex items-start gap-2 px-3 py-2 border-t border-border bg-destructive/5 text-[11px] text-destructive shrink-0">
                    <Icon name="AlertTriangle" className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    <pre className="whitespace-pre-wrap break-words">
                      {buildError}
                    </pre>
                  </div>
                )}
              </div>

              {/* Delete confirm */}
              {deletePath && (
                <div className="absolute inset-0 z-50 flex items-center justify-center bg-background/60">
                  <div className="rounded-xl border border-border bg-card shadow-lg p-4 flex flex-col gap-3 max-w-sm">
                    <p className="text-sm text-foreground">
                      Delete <code className="font-mono">{deletePath}</code>?
                    </p>
                    <div className="flex items-center gap-2 justify-end">
                      <button
                        onClick={() => setDeletePath(null)}
                        className="text-xs rounded-md border border-border px-3 py-1.5 text-muted-foreground hover:text-foreground tech-transition"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => deleteFile(deletePath)}
                        className="flex items-center gap-1.5 text-xs rounded-md bg-destructive px-3 py-1.5 font-medium text-destructive-foreground hover:opacity-90 tech-transition"
                      >
                        <Icon name="Trash2" className="w-3 h-3" /> Delete
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : testScenarios.length === 0 ? (
            /* Tests — empty state. Authoring stays conversational (RFC
               §4.9) — no form/editor here, just a nudge toward chat. */
            <div className="flex flex-col items-center justify-center flex-1 gap-3 text-center px-6">
              <Icon name="FlaskConical" className="w-6 h-6 text-muted-foreground/50" />
              <p className="text-sm font-medium text-foreground">
                No test scenarios yet
              </p>
              <p className="text-xs text-muted-foreground max-w-xs">
                Ask the build chat to add one, e.g. &quot;test that logging
                usage decreases stock&quot;
              </p>
            </div>
          ) : (
            <div className="flex-1 min-h-0 flex flex-col">
              <div className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0">
                <span className="text-xs text-muted-foreground">
                  {testScenarios.filter((s) => testResults[s.id]?.passed).length}/
                  {testScenarios.length} passing
                </span>
                <div className="flex-1" />
                <Button variant="secondary" layout="flex items-center" onClick={() => runScenarios(testScenarios)} disabled={runningTestIds.size > 0} title="Run all scenarios" className="shrink-0">
                  {runningTestIds.size > 0 ? (
                    <Icon name="Loader2" className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Icon name="Play" className="w-3.5 h-3.5" />
                  )}
                  Run all
                </Button>
              </div>
              <div className="flex-1 min-h-0 overflow-y-auto p-3 flex flex-col gap-2">
                {testScenarios.map((scenario) => {
                  const result = testResults[scenario.id];
                  const status: "pass" | "fail" | "not-run" = !result
                    ? "not-run"
                    : result.passed
                      ? "pass"
                      : "fail";
                  const running = runningTestIds.has(scenario.id);
                  const expanded = expandedTestId === scenario.id;
                  return (
                    <div
                      key={scenario.id}
                      className="rounded-lg border border-border"
                    >
                      <div className="flex items-center gap-2 px-3 py-2">
                        <button
                          onClick={() =>
                            setExpandedTestId((cur) =>
                              cur === scenario.id ? null : scenario.id
                            )
                          }
                          className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
                        >
                          {expanded ? (
                            <Icon name="ChevronDown" className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                          ) : (
                            <Icon name="ChevronRight" className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                          )}
                          <span className="text-sm text-foreground truncate">
                            {scenario.name}
                          </span>
                        </button>
                        <span
                          className={`text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full shrink-0 ${
                            status === "pass"
                              ? "text-success bg-success/10"
                              : status === "fail"
                                ? "text-destructive bg-destructive/10"
                                : "text-muted-foreground bg-muted"
                          }`}
                        >
                          {status === "pass"
                            ? "Pass"
                            : status === "fail"
                              ? "Fail"
                              : "Not run"}
                        </span>
                        <Button variant="secondary" size="none" radius="keep" layout="flex items-center" onClick={() => runScenarios([scenario])} disabled={running} title="Run this scenario" className="gap-1 rounded-md px-2 py-1 text-[10px] shrink-0">
                          {running ? (
                            <Icon name="Loader2" className="w-3 h-3 animate-spin" />
                          ) : (
                            <Icon name="Play" className="w-3 h-3" />
                          )}
                          Run
                        </Button>
                      </div>
                      {expanded && (
                        <div className="border-t border-border px-3 py-2.5 text-[11px] flex flex-col gap-1.5">
                          {!result ? (
                            <p className="text-muted-foreground">
                              Not run yet.
                            </p>
                          ) : result.passed ? (
                            <div className="flex items-center gap-1.5 text-success">
                              <Icon name="CheckCircle2" className="w-3.5 h-3.5 shrink-0" />
                              All {result.steps.length} steps and{" "}
                              {result.assertions.length} assertions passed.
                            </div>
                          ) : (
                            <>
                              <div className="text-muted-foreground">
                                {result.steps.filter((s) => s.ok).length}/
                                {result.steps.length} steps ok ·{" "}
                                {result.assertions.filter((a) => a.passed).length}/
                                {result.assertions.length} assertions passed
                              </div>
                              {result.steps
                                .filter((s) => !s.ok)
                                .map((s, i) => (
                                  <div
                                    key={`step-${i}`}
                                    className="flex items-start gap-1.5"
                                  >
                                    <Icon name="XCircle" className="w-3 h-3 text-destructive shrink-0 mt-0.5" />
                                    <span className="text-destructive break-words">
                                      {describeStep(s.step)}
                                      {s.error ? `: ${s.error}` : ""}
                                    </span>
                                  </div>
                                ))}
                              {result.assertions
                                .filter((a) => !a.passed)
                                .map((a, i) => (
                                  <div
                                    key={`assertion-${i}`}
                                    className="flex items-start gap-1.5"
                                  >
                                    <Icon name="XCircle" className="w-3 h-3 text-destructive shrink-0 mt-0.5" />
                                    <span className="text-destructive break-words">
                                      {describeAssertion(a.assertion)} — got{" "}
                                      {JSON.stringify(a.actual)}
                                      {a.error ? ` (${a.error})` : ""}
                                    </span>
                                  </div>
                                ))}
                              {result.error && (
                                <div className="flex items-start gap-1.5">
                                  <Icon name="XCircle" className="w-3 h-3 text-destructive shrink-0 mt-0.5" />
                                  <span className="text-destructive break-words">
                                    {result.error}
                                  </span>
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Right: build chat */}
        <div
          className={`${isMobile ? "flex-1" : "w-[400px] shrink-0"} border-l border-border flex-col min-h-0 bg-card ${
            isMobile ? (mobilePane === "chat" ? "flex" : "hidden") : "flex"
          }`}
        >
          <div className="flex items-center gap-2 px-4 h-10 border-b border-border shrink-0">
            <Icon name="Sparkles" className="w-4 h-4 text-accent" />
            <div className="min-w-0">
              <div className="text-xs font-semibold text-foreground">
                Build chat
              </div>
              <div className="text-[10px] text-muted-foreground truncate">
                app-builder · app:{app.slug}
              </div>
            </div>
          </div>

          {/* New capability requested — a tool: scope the builder just added
              to app.json, above the composer (not in the chat transcript). */}
          {visibleCapabilities.length > 0 && (
            <div className="flex flex-col gap-1.5 px-3 py-2 border-b border-border shrink-0">
              {visibleCapabilities.map((scope) => (
                <div
                  key={scope}
                  className="flex items-start gap-2.5 rounded-lg border border-border bg-secondary px-3 py-2.5"
                >
                  <Icon name="Plug" className="w-4 h-4 text-primary shrink-0 mt-0.5" />
                  <div className="min-w-0 flex-1">
                    <p className="text-[12px] font-semibold text-foreground leading-snug">
                      New capability requested:{" "}
                      <span className="font-mono font-normal">
                        {scope.replace(/^tool:/, "")}
                      </span>
                    </p>
                    <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">
                      Runs through the Action Broker with per-use approval
                      until an admin grants it at publish.
                    </p>
                  </div>
                  <Button variant="text" size="none" radius="keep" layout="" onClick={() => dismissCapability(scope)} aria-label="Dismiss" className="shrink-0 p-0.5 rounded">
                    <Icon name="X" className="w-3.5 h-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          )}

          {/* Regressed test scenarios — a scenario that was passing (or
              never run) and just failed, above the composer like the
              capability card above (RFC §4.9's "Fix with AI" one-click
              loop). */}
          {visibleFailingTests.length > 0 && (
            <div className="flex flex-col gap-1.5 px-3 py-2 border-b border-border shrink-0">
              {visibleFailingTests.map((result) => {
                const scenario = testScenarios.find(
                  (s) => s.id === result.scenarioId
                );
                return (
                  <div
                    key={result.scenarioId}
                    className="flex items-start gap-2.5 rounded-lg border border-border bg-secondary px-3 py-2.5"
                  >
                    <Icon name="FlaskConical" className="w-4 h-4 text-destructive shrink-0 mt-0.5" />
                    <div className="min-w-0 flex-1">
                      <p className="text-[12px] font-semibold text-foreground leading-snug">
                        Test failing:{" "}
                        <span className="font-normal">
                          {scenario?.name ?? result.scenarioId}
                        </span>
                      </p>
                      <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed break-words">
                        {describeFailure(result)}
                      </p>
                      <button
                        onClick={() => fixTestWithAi(result)}
                        className="mt-1 font-mono text-[11px] text-primary hover:opacity-80 tech-transition"
                      >
                        ✦ Fix with AI
                      </button>
                    </div>
                    <Button variant="text" size="none" radius="keep" layout="" onClick={() => dismissFailingTest(result.scenarioId)} aria-label="Dismiss" className="shrink-0 p-0.5 rounded">
                      <Icon name="X" className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                );
              })}
            </div>
          )}

          <div className="flex-1 min-h-0">
            {!app.workspace_path ? (
              <div className="flex flex-col items-center justify-center h-full gap-3 px-6 text-center">
                <Icon name="Lock" className="w-5 h-5 text-muted-foreground/60" />
                <p className="text-sm font-medium text-foreground">Read-only</p>
                <p className="text-xs text-muted-foreground max-w-[16rem]">
                  You can browse this app&apos;s preview and code, but only its
                  editors can talk to the builder.
                </p>
              </div>
            ) : chatSession ? (
              <AgentChat
                key={chatSession.id}
                agentName={BUILDER_AGENT}
                sessionId={chatSession.id}
                compact
                persona={persona}
                pendingInput={pendingInput}
                onPendingInputConsumed={() => setPendingInput(undefined)}
                onActivity={handleActivity}
                onArtifact={onArtifact}
              />
            ) : (
              <div className="flex items-center justify-center h-full">
                <Icon name="Loader2" className="w-4 h-4 animate-spin text-muted-foreground" />
              </div>
            )}
          </div>
          {app.workspace_path && chatSession && !workspaceBound && (
            <div className="px-4 py-1.5 border-t border-border text-[10px] text-muted-foreground shrink-0">
              Binding workspace…
            </div>
          )}
        </div>
      </div>

      {/* ── Publish modal ───────────────────────────────────────────── */}
      {showPublish && (
        <PublishModal
          app={app}
          testStatus={testAggregate}
          onClose={() => setShowPublish(false)}
          onPublished={() => router.push(`/build/apps/${slug}`)}
        />
      )}

      {/* ── Tool-confirm toast — a destructive cc.tools.call() in the DRAFT
          preview awaiting the builder's approve/deny (mirrors the run page,
          RFC §4.4). ─────────────────────────────────────────────────────── */}
      {pendingConfirm && (
        <div className="fixed bottom-5 right-5 z-40 w-[360px] rounded-2xl border border-border bg-popover shadow-lg p-3.5 flex flex-col gap-2.5">
          <div className="flex items-start gap-2.5">
            <Icon name="AlertTriangle" className="w-4 h-4 text-warning shrink-0 mt-0.5" />
            <div className="min-w-0">
              <p className="text-[12.5px] font-semibold text-foreground leading-snug">
                Preview wants to use{" "}
                <span className="font-mono font-normal">
                  {pendingConfirm.tool}
                </span>
              </p>
              <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">
                Testing this scope as you — this runs through the Action
                Broker.
              </p>
            </div>
          </div>
          <pre className="rounded-lg border border-border bg-secondary px-2.5 py-2 font-mono text-[11px] text-muted-foreground overflow-auto max-h-40">
            {JSON.stringify(pendingConfirm.args, null, 2)}
          </pre>
          <div className="flex items-center gap-2">
            <label className="mr-auto flex items-center gap-1.5 text-[11px] text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                checked={rememberTool}
                onChange={(e) => setRememberTool(e.target.checked)}
                className="accent-current"
              />
              Always allow for this app
            </label>
            <Button variant="secondary" size="none" layout="" onClick={() => {
                pendingConfirm.resolve({
                  approved: false,
                  remember: rememberTool,
                });
                setPendingConfirm(null);
              }} className="px-3 py-1.5 text-xs">
              Deny
            </Button>
            <Button size="none" layout="" onClick={() => {
                pendingConfirm.resolve({
                  approved: true,
                  remember: rememberTool,
                });
                setPendingConfirm(null);
              }} className="px-3 py-1.5 text-xs">
              Approve
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Page (Suspense boundary for useSearchParams — Next 16) ──────────────

export default function WorkshopPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-full">
          <Icon name="Loader2" className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <Workshop slug={slug} />
    </Suspense>
  );
}
