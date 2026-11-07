"use client";

/**
 * Settings → Agents → Guidance (WS-MCP SW-31b).
 *
 * Spec: `project-docs/specs/agent_coordination.md` SW-31. The org's written
 * rules for agents, at the whole organization, a space or a project. An agent
 * that works a task gets the blocks that apply, nearest first (SW-31a).
 *
 * The interaction: a member picks a scope, edits the Markdown text and
 * presses "Save guidance". The counter, then "Saved as version N" or the
 * reason of a refusal, shows under the editor, and the new version heads the
 * History list. History is append only: "Use this text" copies an old version
 * into the editor, and its save makes a new version.
 *
 * Who writes is the gateway's rule (`agent_guidance.py`). The org level needs
 * `admin:settings:manage`, and a space or a project needs the write floor of
 * a lead. A member who cannot write still reads the text, with Save off and a
 * short reason. The whole router is dark behind `AGENT_GUIDANCE_ORGS`, and
 * its 404 draws the "not turned on" state, never a broken page.
 */
import { useReducer, useState } from "react";

import { AppTopBar } from "@/components/AppTopBar";
import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import { Skeleton, SkeletonRows } from "@/components/ui/Skeleton";
import { hasCapability } from "@/lib/access";
import { buildProjectTree, type ProjectTree } from "@/lib/accessTree";
import { put } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import { GuidanceHistory } from "./GuidanceHistory";
import {
  EMPTY_EDITOR,
  GUIDANCE_MAX,
  HISTORY_LIMIT,
  HOW_IT_REACHES,
  ORG_SCOPE,
  ORG_WRITE_CAPABILITY,
  counter,
  editorReducer,
  editorText,
  historyKey,
  historyReadOf,
  projectIdOf,
  saveFailure,
  scopeOptions,
  writeRefusal,
  type GuidanceRead,
  type GuidanceVersion,
  type NodeFacts,
} from "./guidance";

async function readTree(): Promise<ProjectTree> {
  const res = await fetch("/api/projects/tree", { cache: "no-store" });
  if (!res.ok) throw new Error("Could not load your spaces and projects.");
  return buildProjectTree(await res.json());
}

async function readNode(projectId: string): Promise<NodeFacts> {
  const res = await fetch(`/api/projects/nodes/${encodeURIComponent(projectId)}`, { cache: "no-store" });
  if (!res.ok) throw new Error("Could not check who can change this project.");
  return (await res.json()) as NodeFacts;
}

async function readHistory(projectId: string | null): Promise<GuidanceRead> {
  const res = projectId
    ? await fetch(
        `/api/agent-guidance/history?project_id=${encodeURIComponent(projectId)}&limit=${HISTORY_LIMIT}`,
        { cache: "no-store" },
      )
    : await fetch(`/api/agent-guidance/history?limit=${HISTORY_LIMIT}`, { cache: "no-store" });
  return historyReadOf(res.status, await res.json().catch(() => null));
}

export default function AgentsSettingsPage() {
  const { access } = useAccess();
  const canManageOrg = hasCapability(access, ORG_WRITE_CAPABILITY);

  const [scope, setScope] = useState<string>(ORG_SCOPE);
  const [editor, dispatch] = useReducer(editorReducer, EMPTY_EDITOR);

  const projectId = projectIdOf(scope);
  const tree = useCachedResource("agent-guidance/tree", readTree);
  const node = useCachedResource(projectId ? `agent-guidance/node/${projectId}` : null, () =>
    readNode(projectId as string),
  );
  const history = useCachedResource(historyKey(projectId), () => readHistory(projectId));

  const options = scopeOptions(tree.data);
  const rows = history.data?.kind === "ok" ? history.data.rows : [];
  const { text, dirty } = editorText(editor, scope, rows);
  const count = counter(text);
  const refusal = writeRefusal({
    projectId,
    canManageOrg,
    node: node.data,
    nodeFailed: !!node.error && !node.data,
  });
  const off = history.data?.kind === "off";
  const ready = history.data?.kind === "ok";
  // The note and the "Saving…" belong to one scope. Another scope shows neither.
  const saving = editor.saving === scope;
  const note = editor.note?.scope === scope ? editor.note : null;

  const save = async () => {
    if (editor.saving || refusal || count.over || !dirty || !ready) return;
    const at = scope;
    const pid = projectId;
    const before = rows;
    dispatch({ type: "save-start", scope: at });
    try {
      const res = await fetch("/api/agent-guidance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: pid, body: text }),
      });
      const data = (await res.json().catch(() => null)) as (GuidanceVersion & { detail?: unknown }) | null;
      if (!res.ok || !data) {
        dispatch({ type: "save-failed", scope: at, text: saveFailure(res.status, data?.detail, text.length) });
        return;
      }
      // The new version heads the list at once. The next read confirms it.
      // The draft stays until the head shows this id (`editorText`).
      put<GuidanceRead>(historyKey(pid), { kind: "ok", rows: [data, ...before] });
      dispatch({ type: "save-done", scope: at, id: data.id, version: data.version });
    } catch {
      dispatch({ type: "save-failed", scope: at, text: saveFailure(0, null) });
    }
  };

  // An admin reaches this page from the Organisation bar. Any other member
  // reaches it from the account menu, and Organisation is admin-only.
  const back = access.is_admin
    ? { href: "/settings/organization", label: "Back to Organisation" }
    : { href: "/settings", label: "Back to Settings" };

  return (
    <div className="flex h-full flex-col">
      {/* The page's title bar (owner, 2026-10-10), with its way back. */}
      <AppTopBar
        back={back}
        title="Agents"
        icon="Bot"
        subtitle="The written rules that your agents follow"
      />

      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        <div className="mx-auto flex max-w-5xl flex-col gap-4">
          <div className="flex flex-col gap-1">
            <h2 className="text-sm font-semibold text-foreground">Guidance</h2>
            <p className="text-xs text-muted-foreground">{HOW_IT_REACHES}</p>
          </div>

          {off ? (
            <div className="flex flex-col items-start gap-2 rounded-lg border border-border bg-secondary/30 px-4 py-5">
              <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                <Icon name="PowerOff" size={15} className="text-muted-foreground" />
                Agent guidance is not turned on for this organization.
              </span>
              <p className="text-xs text-muted-foreground">
                Agents work with the built-in rules until it is on. When it is on, you write the rules here.
              </p>
            </div>
          ) : (
            <>
              <div className="flex max-w-md flex-col gap-1">
                <span className="text-xs text-muted-foreground">Scope</span>
                <SelectButton
                  label="Scope of the guidance"
                  value={scope}
                  defaultValue={ORG_SCOPE}
                  options={options}
                  onChange={setScope}
                  filterAbove={8}
                  widthClass="w-full sm:w-80"
                />
                {tree.error && !tree.data && (
                  <span className="text-xs text-muted-foreground">
                    {tree.error} You can still write the guidance of the whole organization.
                  </span>
                )}
              </div>

              {history.error && !history.data && (
                <div
                  role="alert"
                  className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
                >
                  <span className="flex-1">{history.error}</span>
                  <Button size="sm" variant="secondary" onClick={history.refresh}>
                    Try again
                  </Button>
                </div>
              )}

              {history.loading ? (
                <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]" aria-busy="true">
                  <Skeleton className="h-72 w-full" />
                  <SkeletonRows count={4} />
                </div>
              ) : ready ? (
                <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
                  <div className="flex min-w-0 flex-col gap-2">
                    <Textarea
                      id="guidance-body"
                      value={text}
                      onChange={(e) => dispatch({ type: "edit", scope, text: e.target.value })}
                      readOnly={!!refusal}
                      rows={16}
                      spellCheck
                      aria-label="Guidance text, in Markdown"
                      aria-invalid={count.over || undefined}
                      aria-describedby="guidance-count guidance-note"
                      placeholder={
                        refusal
                          ? "No guidance is written for this scope yet."
                          : "Write the rules for agents in Markdown. For example: Run the tests before you open a pull request."
                      }
                      className="font-mono text-xs"
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        id="guidance-count"
                        className={`text-xs ${count.over ? "text-destructive" : "text-muted-foreground"}`}
                      >
                        {count.label}
                      </span>
                      <span className="ml-auto flex items-center gap-2">
                        {dirty && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => dispatch({ type: "discard", scope })}
                            disabled={saving}
                          >
                            Discard changes
                          </Button>
                        )}
                        <Button
                          size="sm"
                          icon="Check"
                          onClick={() => void save()}
                          loading={saving}
                          disabled={!!refusal || count.over || !dirty}
                        >
                          {saving ? "Saving…" : "Save guidance"}
                        </Button>
                      </span>
                    </div>
                    <p id="guidance-note" role="status" className="min-h-4 text-xs">
                      {refusal ? (
                        <span className="text-muted-foreground">{refusal}</span>
                      ) : note ? (
                        <span className={note.kind === "error" ? "text-destructive" : "text-success"}>{note.text}</span>
                      ) : count.over ? (
                        <span className="text-destructive">
                          Make the text shorter than {GUIDANCE_MAX.toLocaleString("en-US")} characters to save it.
                        </span>
                      ) : null}
                    </p>
                  </div>
                  <GuidanceHistory
                    rows={rows}
                    openId={editor.open?.scope === scope ? editor.open.id : null}
                    onOpen={(id) => dispatch({ type: "open", scope, id })}
                    onUse={
                      refusal
                        ? undefined
                        : (body) => {
                            dispatch({ type: "use", scope, text: body });
                            document.getElementById("guidance-body")?.focus();
                          }
                    }
                  />
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
