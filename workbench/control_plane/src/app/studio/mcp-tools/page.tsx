"use client";

/**
 * /studio/mcp-tools — the approval of the MCP tools of the org (WS-STU B1b-4,
 * `ai_studio_rows/B1b-4.md`).
 *
 * A member reads the tools of each connected MCP server. A member with
 * `integrations:manage` approves the text of one tool at a time. The page
 * sits under the same `/studio` gate as the library: the nav pane `feature`
 * and the `STUDIO_ORGS` switch of the gateway. It is a sub-page of Studio,
 * with no nav entry of its own.
 *
 * ⚠️ The description of a tool is untrusted text from another party. It draws
 * as plain text in a `<p>`. It never goes through HTML or markdown. Approve
 * sends the hash that the row SHOWED, and never a hash that the browser made.
 */

import { useCallback, useEffect, useState } from "react";

import { useAccess } from "@/components/AccessProvider";
import { AppTopBar } from "@/components/AppTopBar";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { hasCapability } from "@/lib/access";
import { approveTool, listMcpTools, refreshServer } from "@/lib/studio/mcpToolsApi";
import {
  NO_MANAGE,
  NO_SERVERS,
  STALE_NOTE,
  approveOutcome,
  canApprove,
  clip,
  confirmCopy,
  groupByServer,
  hintWords,
  stateWords,
  type McpTool,
} from "@/lib/studio/mcpToolsModel";

type Load =
  | { kind: "loading" }
  | { kind: "refused"; detail: string }
  | { kind: "error"; detail: string }
  | { kind: "ready"; tools: McpTool[]; stale: boolean };

export default function McpToolsPage() {
  const { access } = useAccess();
  const manage = hasCapability(access, "integrations:manage");
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [asking, setAsking] = useState<McpTool | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [offerRefresh, setOfferRefresh] = useState<string | null>(null);

  const read = useCallback(async () => {
    const r = await listMcpTools();
    if (r.ok) setLoad({ kind: "ready", tools: r.data.tools, stale: r.data.stale });
    else if (r.status === 403 || r.status === 404) setLoad({ kind: "refused", detail: r.detail });
    else setLoad({ kind: "error", detail: r.detail });
  }, []);

  useEffect(() => {
    let live = true;
    void listMcpTools().then((r) => {
      if (!live) return;
      if (r.ok) setLoad({ kind: "ready", tools: r.data.tools, stale: r.data.stale });
      else if (r.status === 403 || r.status === 404) setLoad({ kind: "refused", detail: r.detail });
      else setLoad({ kind: "error", detail: r.detail });
    });
    return () => { live = false; };
  }, []);

  async function refresh(server: string) {
    setRefreshing(server);
    setNotice(null);
    setRefusal(null);
    setOfferRefresh(null);
    const r = await refreshServer(server);
    setRefreshing(null);
    if (r.ok) setNotice(`${clip(server, 60)}: ${r.data.tools} tools read.`);
    else setRefusal(r.detail);
    await read();
  }

  async function confirm() {
    if (!asking) return;
    const tool = asking;
    setBusy(true);
    // The hash that the row showed. The page never makes a hash.
    const r = await approveTool(tool.server, tool.tool, tool.textHash);
    setBusy(false);
    setAsking(null);
    setNotice(null);
    setRefusal(null);
    setOfferRefresh(null);
    if (r.ok) {
      setNotice(`${clip(tool.tool, 64)} is approved.`);
      await read();
      return;
    }
    const out = approveOutcome(r.status, r.code, r.detail);
    setRefusal(out.message);
    if (out.kind === "refresh") setOfferRefresh(tool.server);
    if (out.kind === "reread") await read();
  }

  const copy = asking ? confirmCopy(asking) : null;
  const groups = load.kind === "ready" ? groupByServer(load.tools) : [];

  return (
    <div className="flex h-full flex-col">
      <AppTopBar title="MCP tools" subtitle="Studio" back={{ href: "/studio", label: "Back to Studio" }} />
      <div className="flex-1 overflow-y-auto">
        {notice ? <p role="status" className="px-3 pt-3 text-xs text-foreground sm:px-4">{notice}</p> : null}
        {refusal ? (
          <div className="flex flex-wrap items-center gap-2 px-3 pt-3 sm:px-4">
            <p role="alert" className="text-xs text-destructive">{refusal}</p>
            {offerRefresh ? (
              <Button variant="secondary" size="sm" icon="RefreshCw" disabled={refreshing !== null} onClick={() => void refresh(offerRefresh)}>
                Refresh {clip(offerRefresh, 60)}
              </Button>
            ) : null}
          </div>
        ) : null}
        {load.kind === "loading" ? <SkeletonRows count={4} /> : null}
        {load.kind === "refused" ? <p role="alert" className="p-3 text-sm text-foreground sm:p-4">{load.detail}</p> : null}
        {load.kind === "error" ? (
          <div className="flex flex-col items-center gap-2 p-6 text-center">
            <p role="alert" className="text-sm text-destructive">{load.detail}</p>
            <Button variant="secondary" size="sm" onClick={() => { setLoad({ kind: "loading" }); void read(); }}>Try again</Button>
          </div>
        ) : null}
        {load.kind === "ready" && load.stale ? <p className="px-3 pt-3 text-xs text-muted-foreground sm:px-4">{STALE_NOTE}</p> : null}
        {load.kind === "ready" && !manage ? <p className="px-3 pt-3 text-xs text-muted-foreground sm:px-4">{NO_MANAGE}</p> : null}
        {load.kind === "ready" && groups.length === 0 ? (
          <EmptyState icon="Plug" message="No MCP tools to show." hint={NO_SERVERS} />
        ) : null}
        {groups.map((g) => (
          <section key={g.server} aria-label={`Server ${clip(g.server, 60)}`} className="flex flex-col gap-2 p-3 sm:p-4">
            <PageHeader
              title={clip(g.server, 60)}
              subtitle={`${g.tools.length} ${g.tools.length === 1 ? "tool" : "tools"}`}
              actions={
                <Button variant="secondary" size="sm" icon="RefreshCw" disabled={refreshing !== null} onClick={() => void refresh(g.server)}>
                  {refreshing === g.server ? "Refreshing" : "Refresh"}
                </Button>
              }
            />
            <ul aria-label={`Tools of ${clip(g.server, 60)}`} className="flex flex-col divide-y divide-border rounded-xl border border-border bg-card">
              {g.tools.map((t) => (
                <li key={t.id} className="flex flex-col gap-1.5 px-3 py-2 sm:px-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="break-all text-sm font-medium text-foreground">{clip(t.tool, 64)}</span>
                    <Badge tone={t.changed ? "warning" : "success"} size="xs">{stateWords(t)}</Badge>
                    <Badge tone="neutral" size="xs">{`Class ${t.klass}`}</Badge>
                  </div>
                  <p className="break-words text-xs text-muted-foreground">{clip(t.description, 280) || "No description."}</p>
                  <p className="break-words text-xs text-muted-foreground">{hintWords(t.hints).join(". ")}.</p>
                  {canApprove(t, access.capabilities) ? (
                    <div>
                      <Button variant="primary" size="sm" icon="Check" onClick={() => { setRefusal(null); setNotice(null); setOfferRefresh(null); setAsking(t); }}>
                        Approve
                      </Button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      {copy ? (
        <ConfirmDialog
          open
          title={copy.title}
          subject={copy.subject}
          body={copy.body}
          confirmLabel={copy.confirmLabel}
          confirmVariant="primary"
          icon="Check"
          busy={busy}
          onConfirm={() => void confirm()}
          onCancel={() => setAsking(null)}
        >
          <p className="whitespace-pre-line break-words rounded-md border border-border bg-secondary/50 p-2 text-xs text-foreground">
            {copy.description}
          </p>
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
