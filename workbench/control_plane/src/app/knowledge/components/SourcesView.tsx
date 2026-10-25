"use client";

/**
 * The Sources view (WS-61 KC-1b, `knowledge_curation.md` §7.1 and §7.2):
 * `/knowledge/sources`.
 *
 * A source is a drive that a member connected. Metorite reads the names and
 * the folders first, and no file text. A member connects Google Drive, sees
 * the folder tree that discovery stored, and disconnects. A manager of
 * Knowledge sees every drive of the org with its owner. Only the owner can
 * connect a drive again, because the consent is for the owner's own Google
 * account. A manager can still disconnect it.
 *
 * The Google return lands on `/knowledge`, so the layout shows the toast and
 * brings the member here. A 404 of the list means connected drives are off
 * for the org (`listSources` gives it as `off`).
 *
 * The tree only shows what discovery stored. A folder that discovery did not
 * reach shows "Not yet explored". Opening such a folder on demand needs a
 * gateway route that does not exist yet: follow-up KC-1c, Metorite task #441.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";

import { useAccess } from "@/components/AccessProvider";
import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { hasCapability } from "@/lib/access";
import { invalidate } from "@/lib/dataCache";
import { formatBytes } from "@/lib/orgBranding";
import { relativeTime } from "@/lib/taskCard";
import { useCachedResource } from "@/lib/useCachedResource";

import { KNOWLEDGE_CACHE, KnowledgeError } from "../lib/api";
import { MANAGE_CAPABILITY } from "../lib/gaps";
import { KNOWLEDGE_HOME } from "../lib/links";
import {
  POLL_LIMIT_MS,
  POLL_MS,
  SOURCES_KEY,
  connectFailure,
  connectTarget,
  disconnectCopy,
  disconnectSource,
  driveLink,
  isMine,
  listSources,
  shouldPoll,
  sourceTree,
  startConnect,
  stateLabel,
  stateTone,
  truncatedNotice,
  typeIcon,
  typeWord,
  visibleSources,
  type Source,
  type SourceList,
  type SourceNode,
} from "../lib/sources";

export default function SourcesView() {
  const router = useRouter();
  const toast = useToast();
  const { access } = useAccess();
  const manager = hasCapability(access, MANAGE_CAPABILITY);
  const read = useCachedResource<SourceList>(SOURCES_KEY, listSources);
  const list = read.data;
  const sources = visibleSources(list);
  const [connecting, setConnecting] = useState(false);
  const connectingRef = useRef(false);
  const [leaving, setLeaving] = useState<Source | null>(null);
  const [leaveBusy, setLeaveBusy] = useState(false);

  // While a drive is being read, ask again every few seconds. The poll stops
  // when no drive is being read, after five minutes, and when the view closes.
  const polling = shouldPoll(sources);
  const refreshRef = useRef(read.refresh);
  useEffect(() => {
    refreshRef.current = read.refresh;
  });
  useEffect(() => {
    if (!polling) return;
    const started = Date.now();
    const timer = setInterval(() => {
      if (Date.now() - started >= POLL_LIMIT_MS) {
        clearInterval(timer);
        return;
      }
      refreshRef.current();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [polling]);

  const connect = useCallback(async () => {
    if (connectingRef.current) return;
    connectingRef.current = true;
    setConnecting(true);
    try {
      const target = connectTarget(await startConnect());
      if (!target) throw new Error("The consent address is not a Google address.");
      window.location.assign(target);
    } catch (err) {
      connectingRef.current = false;
      setConnecting(false);
      toast.show({
        key: "kb-source-connect",
        variant: "error",
        title: connectFailure(err),
      });
    }
  }, [toast]);

  async function leave() {
    const target = leaving;
    if (!target) return;
    setLeaveBusy(true);
    try {
      await disconnectSource(target.id);
      setLeaving(null);
      invalidate(KNOWLEDGE_CACHE);
      toast.show({ key: `kb-source:${target.id}`, title: `Disconnected ${target.account_email ?? "Google Drive"}` });
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      setLeaving(null);
      toast.show({
        key: `kb-source:${target.id}`,
        variant: "error",
        title: "Could not disconnect the drive",
        description: reason,
      });
    } finally {
      setLeaveBusy(false);
    }
  }

  const copy = leaving ? disconnectCopy(leaving) : null;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6">
        <header className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <Icon name="HardDrive" size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-foreground">Sources</h2>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {manager
                ? "The drives that members of your organization connected. Knowledge reads names and folders first."
                : "The drives you connected. Knowledge reads names and folders first, and no file text."}
            </p>
          </div>
        </header>

        <section className="mt-5" aria-label="Connected drives">
          {read.loading ? (
            <SkeletonRows count={3} />
          ) : list?.off ? (
            <EmptyState
              icon="PowerOff"
              message="Connected drives are not on for your organization yet."
              hint="When they are on, you can connect Google Drive here."
              action={{ label: "All spaces", icon: "LayoutGrid", onClick: () => router.push(KNOWLEDGE_HOME) }}
            />
          ) : read.error && !list ? (
            <EmptyState
              icon="CloudOff"
              message="The sources did not load."
              hint="The server did not answer. Try again in a moment."
              action={{ label: "Try again", icon: "RefreshCw", onClick: read.refresh }}
            />
          ) : sources.length === 0 ? (
            <EmptyState
              icon="HardDrive"
              message="Connect a drive so Knowledge can see your folders."
              hint="Metorite reads names and folders first. It reads no file text."
              action={{
                label: connecting ? "Connecting" : "Connect Google Drive",
                icon: "Plug",
                onClick: () => void connect(),
                disabled: connecting,
              }}
            />
          ) : (
            <ul className="flex flex-col gap-3">
              {sources.map((source) => (
                <SourceCard
                  key={source.id}
                  source={source}
                  me={access.email}
                  manager={manager}
                  connecting={connecting}
                  onReconnect={() => void connect()}
                  onDisconnect={() => setLeaving(source)}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
      <ConfirmDialog
        open={leaving !== null}
        title={copy?.title ?? "Disconnect Google Drive?"}
        subject={copy?.subject ?? null}
        body={copy?.body ?? ""}
        confirmLabel="Disconnect"
        cancelLabel="Keep the drive"
        confirmVariant="destructive"
        defaultFocus="cancel"
        icon="Unplug"
        busy={leaveBusy}
        onCancel={() => setLeaving(null)}
        onConfirm={() => void leave()}
      />
    </div>
  );
}

function SourceCard({
  source,
  me,
  manager,
  connecting,
  onReconnect,
  onDisconnect,
}: {
  source: Source;
  me: string;
  manager: boolean;
  connecting: boolean;
  onReconnect: () => void;
  onDisconnect: () => void;
}) {
  const mine = isMine(source, me);
  const broken = source.state === "needs_reconnect" || source.state === "failed";
  const read = relativeTime(source.discovered_at);
  const items = `${source.node_count.toLocaleString("en-US")} ${source.node_count === 1 ? "item" : "items"}`;
  const owner = (source.owner_email || "").trim();
  return (
    <li className="rounded-lg border border-border bg-card" data-source-id={source.id}>
      <div className="flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-start sm:gap-3">
        <span className="flex min-w-0 flex-1 items-start gap-3">
          <Icon name="HardDrive" size={16} className="mt-0.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-foreground">Google Drive</span>
              <Badge tone={stateTone(source.state)}>{stateLabel(source.state)}</Badge>
            </span>
            <span className="block truncate text-sm text-foreground" title={source.account_email ?? undefined}>
              {source.account_email ?? "Account not known"}
            </span>
            <span className="block text-xs text-muted-foreground">
              {items}
              {read ? ` · Read ${read}` : ""}
              {manager ? ` · Owner: ${mine ? "You" : owner}` : ""}
            </span>
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2 pl-7 sm:pl-0">
          {broken && mine ? (
            <Button size="sm" variant="secondary" icon="RefreshCw" loading={connecting} onClick={onReconnect}>
              Reconnect
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            icon="Unplug"
            aria-label={`Disconnect ${source.account_email ?? "Google Drive"}`}
            onClick={onDisconnect}
          >
            Disconnect
          </Button>
        </span>
      </div>
      {source.state === "needs_reconnect" ? (
        <p className="border-t border-border px-3 py-2 text-sm text-muted-foreground">
          {mine
            ? "Google stopped the access. Reconnect to read this drive again."
            : `Waiting for ${owner} to reconnect.`}
        </p>
      ) : null}
      {source.state === "failed" ? (
        <p className="border-t border-border px-3 py-2 text-sm text-muted-foreground">
          {mine ? "Metorite could not read this drive." : `Waiting for ${owner} to reconnect.`}
        </p>
      ) : null}
      {source.state === "discovering" ? (
        <p className="border-t border-border px-3 py-2 text-sm text-muted-foreground">
          Reading your folders. This page updates itself.
        </p>
      ) : null}
      {source.state === "ready" || source.state === "discovering" ? (
        // Remount when the read ends, so a folder that was open early is read again.
        <SourceTree key={`${source.id}:${source.state}`} source={source} />
      ) : null}
    </li>
  );
}

const ROOT = "";

type Branch =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; nodes: SourceNode[]; truncated: boolean };

/**
 * The folder tree of one drive. The roots load on mount and again as the
 * count grows during discovery. A folder loads its children once, on the
 * first open, and the answer stays in state for the next open.
 */
function SourceTree({ source }: { source: Source }) {
  const [branches, setBranches] = useState<Record<string, Branch>>({});
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const asked = useRef<Set<string>>(new Set());
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const load = useCallback(
    async (parent: string) => {
      if (asked.current.has(parent)) return;
      asked.current.add(parent);
      try {
        const out = await sourceTree(source.id, parent || null);
        if (alive.current) {
          setBranches((prev) => ({ ...prev, [parent]: { status: "ready", nodes: out.nodes, truncated: out.truncated } }));
        }
      } catch {
        asked.current.delete(parent);
        if (alive.current) setBranches((prev) => ({ ...prev, [parent]: { status: "error" } }));
      }
    },
    [source.id],
  );

  // The roots: on mount, and again when discovery stored more nodes. The
  // answer lands in a callback, never in the body of the effect.
  useEffect(() => {
    let current = true;
    sourceTree(source.id, null)
      .then((out) => {
        if (current) setBranches((prev) => ({ ...prev, [ROOT]: { status: "ready", nodes: out.nodes, truncated: out.truncated } }));
      })
      .catch(() => {
        if (current) setBranches((prev) => (prev[ROOT]?.status === "ready" ? prev : { ...prev, [ROOT]: { status: "error" } }));
      });
    return () => {
      current = false;
    };
  }, [source.id, source.node_count]);

  // A folder with no answer yet draws the skeleton, so the first open sets nothing.
  // A retry clears the error first, so the folder draws the skeleton again.
  function retry(parent: string) {
    setBranches((prev) => {
      const next = { ...prev };
      delete next[parent];
      return next;
    });
    void load(parent);
  }

  function toggle(node: SourceNode) {
    const next = new Set(open);
    if (next.has(node.ext_id)) {
      next.delete(node.ext_id);
    } else {
      next.add(node.ext_id);
      void load(node.ext_id);
    }
    setOpen(next);
  }

  function rows(parent: string, level: number): ReactNode[] {
    const branch = branches[parent];
    if (!branch || branch.status === "loading") {
      return [
        <li key={`${parent}:loading`} role="none" style={{ paddingLeft: `${level}rem` }} className="py-1">
          <SkeletonRows count={1} />
        </li>,
      ];
    }
    if (branch.status === "error") {
      return [
        <li key={`${parent}:error`} role="none" style={{ paddingLeft: `${level}rem` }} className="flex items-center gap-2 py-1 text-xs text-muted-foreground">
          Could not load this folder. Try again.
          <Button size="sm" variant="ghost" icon="RefreshCw" onClick={() => retry(parent)}>
            Try again
          </Button>
        </li>,
      ];
    }
    const out: ReactNode[] = [];
    if (branch.nodes.length === 0) {
      out.push(
        <li key={`${parent}:empty`} role="none" style={{ paddingLeft: `${level}rem` }} className="py-1 text-xs text-muted-foreground">
          {parent === ROOT ? "No folders or files were found yet." : "Nothing in this folder."}
        </li>,
      );
    }
    for (const node of branch.nodes) {
      out.push(<NodeRow key={node.ext_id} node={node} level={level} open={open.has(node.ext_id)} onToggle={toggle} />);
      if (open.has(node.ext_id)) out.push(...rows(node.ext_id, level + 1));
    }
    if (branch.truncated) {
      out.push(
        <li key={`${parent}:truncated`} role="none" style={{ paddingLeft: `${level}rem` }} className="py-1 text-xs text-muted-foreground">
          {truncatedNotice(branch.nodes.length)}
        </li>,
      );
    }
    return out;
  }

  return (
    <div className="border-t border-border px-2 py-2">
      <ul role="tree" aria-label={`Folders of ${source.account_email ?? "Google Drive"}`} className="flex flex-col">
        {rows(ROOT, 0)}
      </ul>
    </div>
  );
}

function NodeRow({
  node,
  level,
  open,
  onToggle,
}: {
  node: SourceNode;
  level: number;
  open: boolean;
  onToggle: (node: SourceNode) => void;
}) {
  const folder = node.kind === "folder";
  // KC-1c (Metorite task #441): a folder that discovery did not reach and that
  // holds nothing stored has no children to show, and the gateway has no route
  // to read it on demand yet. It shows as "Not yet explored", with no chevron.
  const expandable = folder && node.has_children;
  const notExplored = folder && !node.explored && !node.has_children;
  const word = folder ? null : typeWord(node.mime);
  const link = folder ? null : driveLink(node.web_url);
  const modified = relativeTime(node.modified_at);
  const detail = [word, node.size !== null && !folder ? formatBytes(node.size) : null, modified ? `Modified ${modified}` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <li
      role="treeitem"
      aria-level={level + 1}
      aria-selected={false}
      aria-expanded={expandable ? open : undefined}
      data-node-id={node.ext_id}
      style={{ paddingLeft: `${level}rem` }}
      className="flex items-center gap-1 py-0.5"
    >
      {expandable ? (
        <Button
          size="icon-xs"
          variant="ghost"
          icon={open ? "ChevronDown" : "ChevronRight"}
          aria-label={`${open ? "Close" : "Open"} ${node.name}`}
          onClick={() => onToggle(node)}
        />
      ) : (
        <span className="inline-block h-6 w-6 shrink-0" aria-hidden="true" />
      )}
      <Icon
        name={folder ? "Folder" : typeIcon(word ?? "File")}
        size={15}
        className="shrink-0 text-muted-foreground"
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-foreground" title={node.name}>
          {node.name}
        </span>
        {notExplored ? (
          <span className="block text-xs text-muted-foreground">Not yet explored</span>
        ) : detail ? (
          <span className="block truncate text-xs text-muted-foreground">{detail}</span>
        ) : null}
      </span>
      {link ? (
        <Button
          size="sm"
          variant="ghost"
          icon="ExternalLink"
          href={link}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open ${node.name} in Google Drive`}
        >
          <span className="hidden sm:inline">Open in Google Drive</span>
        </Button>
      ) : null}
    </li>
  );
}
