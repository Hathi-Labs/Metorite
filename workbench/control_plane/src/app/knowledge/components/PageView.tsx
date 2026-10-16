"use client";

/**
 * One Knowledge page (WS-61 KB-2a, `knowledge_base.md` §4.2 and §11.1).
 *
 * The title, the state, the body as Markdown, and a strip with the author,
 * the review date and the versions. A member who can write edits the page in
 * a Markdown field with a Write and Preview switch, publishes a draft, makes
 * a sub-page, and reverts to an older version. Each act is one request, and
 * each one re-reads what is on screen, whatever the answer was.
 *
 * ⚠️ `can_write` comes from the gateway and every write control hides
 * without it. Hiding is a courtesy, never the boundary: the gateway refuses
 * a write from a reader with 403 (`core.can_write_page`).
 *
 * The preview is the chat's one renderer, `MarkdownBody`, with no entity
 * pills and no fences, inside `cc-prose prose`. No second renderer, and no
 * HTML sink (`htmlSinks.test.ts`). Each heading of the body moves one level
 * down first (`lib/markdown.ts`), so the app bar keeps the one `<h1>`.
 *
 * ⚠️ An edit is never lost in silence (fix round 1). The edit takes a
 * SNAPSHOT when it begins, and the save diffs against it, never against the
 * live page that the cache refreshes. The save reads the page again first.
 * A newer version stops it with "Someone saved a newer version", and the
 * member saves anyway or reloads (`lib/draft.ts`). While the draft is dirty,
 * a `beforeunload` warns, every way out in the app asks through the leave
 * guard of the layout, and the draft waits in session storage. So the view
 * that the shell remounts at 767px gets it back.
 *
 * A 404 on this page means "not found". It never means "off": the layout
 * decides "off" from the spaces read alone (`lib/phase.ts`).
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { useAccess } from "@/components/AccessProvider";
import { EmptyState } from "@/components/EmptyState";
import { MarkdownBody } from "@/components/MarkdownMessage";
import Tabs from "@/components/Tabs";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input, Textarea } from "@/components/ui/Input";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import { useAssistantFacts } from "@/lib/shell/assistant";
import { chatScope } from "@/lib/sessions";
import { relativeTime } from "@/lib/taskCard";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  KNOWLEDGE_CACHE,
  KnowledgeError,
  getPage,
  pageKey,
  publishPage,
  revertPage,
  updatePage,
} from "../lib/api";
import { knowledgeAssistantFacts } from "../lib/assistantFacts";
import { demoteHeadings } from "../lib/markdown";
import {
  clearDraft,
  draftPatch,
  isDirty,
  isStale,
  readDraft,
  snapshotOf,
  writeDraft,
  type Draft,
  type EditSnapshot,
} from "../lib/draft";
import { dayLabel, publishBody, reasonLabel, stateLabel, stateTone, STATE_LABELS } from "../lib/labels";
import { KNOWLEDGE_HOME, pageHref, spaceHref } from "../lib/links";
import { PAGE_NOT_FOUND_BODY, PAGE_NOT_FOUND_TITLE, pageRefusal, type PageRead } from "../lib/phase";
import type { PageDetail, PageVersion } from "../lib/types";
import { useKnowledge } from "./KnowledgeContext";
import VersionList from "./VersionList";

async function readPage(id: string): Promise<PageRead<PageDetail>> {
  try {
    return { kind: "page", page: await getPage(id) };
  } catch (err) {
    const refusal = pageRefusal<PageDetail>(err);
    if (refusal) return refusal;
    throw err;
  }
}

const EDIT_TABS = [
  { id: "write", label: "Write", icon: "PenLine" },
  { id: "preview", label: "Preview", icon: "Eye" },
];

/** The session store, or `null` on the server and in a blocked browser. */
function sessionStore(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export default function PageView({ pageId }: { pageId: string }) {
  const { spaces, setPageFocus, openNewPage, setLeaveGuard, leaveGuarded, requestLeave } = useKnowledge();
  const { access, loading: accessLoading } = useAccess();
  const scope = accessLoading ? null : chatScope(access.email, access.organization?.id);
  const router = useRouter();
  const searchParams = useSearchParams();
  const toast = useToast();
  const read = useCachedResource<PageRead<PageDetail>>(pageKey(pageId), () => readPage(pageId));
  const page = read.data?.kind === "page" ? read.data.page : null;
  const space = page ? spaces.find((s) => s.id === page.space_id) ?? null : null;

  // The rail and the bar's subtitle follow the open page.
  const spaceId = page?.space_id ?? null;
  useEffect(() => {
    if (!spaceId) return;
    setPageFocus({ pageId, spaceId });
    return () => setPageFocus(null);
  }, [pageId, spaceId, setPageFocus]);

  // The one assistant knows which page is open (D99, §11.1).
  useAssistantFacts(page ? knowledgeAssistantFacts({ page, spaceName: space?.name ?? null }) : null);

  const [editing, setEditing] = useState(false);
  const [tab, setTab] = useState("write");
  const [draft, setDraft] = useState<Draft>({ title: "", body_md: "" });
  const [snapshot, setSnapshot] = useState<EditSnapshot | null>(null);
  const [stale, setStale] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [reverting, setReverting] = useState<PageVersion | null>(null);
  const [busy, setBusy] = useState(false);

  const writable = Boolean(page?.can_write) && page?.state !== "archived";

  function startEdit(from: PageDetail, restored?: { snapshot: EditSnapshot; draft: Draft }) {
    setSnapshot(restored?.snapshot ?? snapshotOf(from));
    setDraft(restored?.draft ?? { title: from.title, body_md: from.body_md });
    setTab("write");
    setSaveError(null);
    setStale(false);
    setEditing(true);
  }

  /** Close the editor and drop the draft, the stored copy too. */
  function endEdit() {
    const store = sessionStore();
    if (store && scope) clearDraft(store, scope, pageId);
    setEditing(false);
    setSnapshot(null);
    setStale(false);
    setSaveError(null);
  }

  const dirty = editing && snapshot !== null && isDirty(snapshot, draft);

  // A draft that waits in session storage comes back once, after the page
  // and the member are known. That covers the remount at 767px and Back.
  const [restoredFor, setRestoredFor] = useState<string | null>(null);
  if (page && scope && !editing && restoredFor !== page.id) {
    setRestoredFor(page.id);
    const store = sessionStore();
    const stored = store ? readDraft(store, scope, page.id) : null;
    if (stored && page.can_write && page.state !== "archived") startEdit(page, stored);
  }

  // The stored copy follows the draft: kept while dirty, gone when clean.
  useEffect(() => {
    const store = sessionStore();
    if (!store || !scope || !editing || !snapshot) return;
    if (isDirty(snapshot, draft)) writeDraft(store, scope, snapshot, draft);
    else clearDraft(store, scope, snapshot.pageId);
  }, [scope, editing, snapshot, draft]);

  // The leave guard of the layout, and the browser's own warning.
  const endEditRef = useRef(endEdit);
  useEffect(() => {
    endEditRef.current = endEdit;
  });
  useEffect(() => {
    if (!dirty) return;
    setLeaveGuard({ onDiscard: () => endEditRef.current() });
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      setLeaveGuard(null);
      window.removeEventListener("beforeunload", warn);
    };
  }, [dirty, setLeaveGuard]);

  // `?edit=1` opens the editor once, after a new page is made. Set during
  // render, the adjust-state-on-a-prop-change pattern. The address then
  // drops it, so a reload shows the page and not the editor.
  const askEdit = searchParams.get("edit") === "1";
  const [editAsked, setEditAsked] = useState<string | null>(null);
  if (askEdit && page && editAsked !== page.id) {
    setEditAsked(page.id);
    if (page.can_write && page.state !== "archived") startEdit(page);
  }
  useEffect(() => {
    if (askEdit && page) router.replace(pageHref(pageId));
  }, [askEdit, page, pageId, router]);

  if (read.loading) {
    return (
      <div className="mx-auto w-full max-w-3xl p-6">
        <SkeletonRows count={5} />
      </div>
    );
  }
  if (read.data?.kind === "not-found") {
    return (
      <EmptyState
        icon="SearchX"
        message={PAGE_NOT_FOUND_TITLE}
        hint={PAGE_NOT_FOUND_BODY}
        action={{ label: "All spaces", icon: "LayoutGrid", onClick: () => router.push(KNOWLEDGE_HOME) }}
      />
    );
  }
  if (!page) {
    return (
      <EmptyState
        icon="CloudOff"
        message="This page did not load."
        hint="The server did not answer. Try again in a moment."
        action={{ label: "Try again", icon: "RefreshCw", onClick: read.refresh }}
      />
    );
  }

  /** After each act, re-read what is on screen, whatever the answer was. */
  const reread = () => invalidate(KNOWLEDGE_CACHE);

  async function save(anyway = false) {
    if (!page || !snapshot) return;
    const patch = draftPatch(snapshot, draft);
    if (!patch) {
      endEdit();
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      if (!anyway) {
        // Read the page again: the cache does not hear another member save.
        const fresh = await getPage(page.id).catch(() => page);
        if (isStale(snapshot, fresh)) {
          setStale(true);
          return;
        }
      }
      await updatePage(page.id, patch);
      endEdit();
      toast.show({ key: "kb-save", variant: "success", title: "Changes saved" });
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      setSaveError(`Could not save the page. ${reason}`);
    } finally {
      setSaving(false);
      reread();
    }
  }

  async function publish() {
    if (!page) return;
    setBusy(true);
    try {
      await publishPage(page.id);
      toast.show({ key: "kb-publish", variant: "success", title: "Page published" });
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      toast.show({ key: "kb-publish", variant: "error", title: "Could not publish the page", description: reason });
    } finally {
      setBusy(false);
      setConfirmPublish(false);
      reread();
    }
  }

  async function revert(version: PageVersion) {
    if (!page) return;
    setBusy(true);
    try {
      await revertPage(page.id, version.version);
      toast.show({ key: "kb-revert", variant: "success", title: `Reverted to version ${version.version}` });
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      toast.show({ key: "kb-revert", variant: "error", title: "Could not revert the page", description: reason });
    } finally {
      setBusy(false);
      setReverting(null);
      reread();
    }
  }

  const last = page.versions[0] ?? null;
  const patch = editing && snapshot ? draftPatch(snapshot, draft) : null;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-5 sm:px-6 xl:flex-row xl:items-start">
        <article className="min-w-0 flex-1" aria-label={page.title}>
          {space ? (
            <Link
              href={spaceHref(space.id)}
              onClick={(e) => {
                if (!leaveGuarded()) return;
                e.preventDefault();
                requestLeave(() => router.push(spaceHref(space.id)));
              }}
              className="cc-link text-xs text-muted-foreground hover:text-foreground"
            >
              {space.name}
            </Link>
          ) : null}

          {editing ? (
            // ⚠️ A `div`, not a `form`. The buttons of `Tabs` carry no
            // `type`, so inside a form a click on Preview submitted it and
            // saved the page (measured in `e2e/knowledge-pages.spec.ts`).
            <div className="mt-1 space-y-3">
              <Input
                aria-label="Title"
                value={draft.title}
                maxLength={300}
                onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
                className="w-full"
              />
              <Tabs tabs={EDIT_TABS} activeTab={tab} onTabChange={setTab} variant="segmented" chrome="inline" />
              {tab === "write" ? (
                <Textarea
                  aria-label="Page body in Markdown"
                  value={draft.body_md}
                  rows={18}
                  onChange={(e) => setDraft((d) => ({ ...d, body_md: e.target.value }))}
                  placeholder="Write in Markdown. # for a heading, - for a list, **bold** for bold."
                  className="w-full font-mono"
                />
              ) : (
                <div className="min-h-40 rounded-md border border-border bg-card p-4">
                  {draft.body_md.trim() ? (
                    <div className="cc-prose prose prose-sm max-w-none">
                      <MarkdownBody content={demoteHeadings(draft.body_md)} />
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">Nothing to preview yet.</p>
                  )}
                </div>
              )}
              {stale ? (
                <div
                  role="alert"
                  className="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-foreground"
                >
                  <span className="min-w-0 flex-1">
                    Someone saved a newer version of this page while you edited it. Save anyway
                    to write your changes over it, or reload to read it first.
                  </span>
                  <Button size="sm" variant="secondary" disabled={saving} onClick={() => void save(true)}>
                    Save anyway
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={saving}
                    onClick={() => {
                      endEdit();
                      reread();
                    }}
                  >
                    Reload
                  </Button>
                </div>
              ) : null}
              {saveError ? (
                <p role="alert" className="text-xs text-destructive">
                  {saveError}
                </p>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" loading={saving} disabled={saving || !patch || !draft.title.trim()} onClick={() => void save()}>
                  Save changes
                </Button>
                <Button variant="secondary" size="sm" disabled={saving} onClick={() => requestLeave(endEdit)}>
                  Cancel
                </Button>
                {!draft.title.trim() ? (
                  <span className="text-xs text-destructive">A page needs a title.</span>
                ) : null}
              </div>
            </div>
          ) : (
            <>
              <h2 className="mt-1 break-words text-xl font-semibold text-foreground">{page.title}</h2>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge tone={stateTone(page.state)} icon={STATE_LABELS[page.state]?.icon}>
                  {stateLabel(page.state)}
                </Badge>
                {page.can_write ? null : <Badge icon="Eye">Read only</Badge>}
                {last ? (
                  <span>
                    {reasonLabel(last.reason)} by {last.author_email}
                    {last.created_at ? `, ${relativeTime(last.created_at) || "now"}` : ""}
                  </span>
                ) : null}
              </div>
              {writable ? (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Button variant="secondary" size="sm" icon="PenLine" onClick={() => startEdit(page)}>
                    Edit
                  </Button>
                  {page.state === "draft" ? (
                    <Button size="sm" icon="Send" onClick={() => setConfirmPublish(true)}>
                      Publish
                    </Button>
                  ) : null}
                  <Button
                    variant="ghost"
                    size="sm"
                    icon="FilePlus"
                    onClick={() =>
                      openNewPage({ parent: { id: page.id, title: page.title, spaceId: page.space_id } })
                    }
                  >
                    New sub-page
                  </Button>
                </div>
              ) : null}
              <div className="mt-5">
                {page.body_md.trim() ? (
                  <div className="cc-prose prose prose-sm max-w-none">
                    <MarkdownBody content={demoteHeadings(page.body_md)} />
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    This page is empty.{writable ? " Choose Edit to write it." : ""}
                  </p>
                )}
              </div>
            </>
          )}
        </article>

        <aside
          className="w-full shrink-0 space-y-4 border-t border-border pt-4 xl:w-64 xl:border-l xl:border-t-0 xl:pl-4 xl:pt-0"
          aria-label="About this page"
        >
          <dl className="space-y-2 text-xs">
            <div>
              <dt className="text-muted-foreground">Author</dt>
              <dd className="truncate text-foreground">{page.author_email}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Review date</dt>
              <dd className="text-foreground">
                {page.review_at ? dayLabel(page.review_at) : "Set when the page is published"}
              </dd>
            </div>
          </dl>
          <section>
            <h3 className="mb-1 text-xs font-medium text-foreground">Versions</h3>
            <VersionList versions={page.versions} canRevert={writable && !editing} onRevert={setReverting} />
          </section>
        </aside>
      </div>

      <ConfirmDialog
        open={confirmPublish}
        title="Publish this page?"
        subject={page.title}
        body={publishBody(space)}
        confirmLabel="Publish page"
        confirmVariant="primary"
        icon="Send"
        busy={busy}
        onConfirm={() => void publish()}
        onCancel={() => setConfirmPublish(false)}
      />
      <ConfirmDialog
        open={reverting !== null}
        title={reverting ? `Revert to version ${reverting.version}?` : "Revert"}
        subject={reverting?.title ?? null}
        body={
          reverting
            ? `This writes the title and the body of version ${reverting.version} as a new version. The versions after it stay in the history.`
            : ""
        }
        confirmLabel={reverting ? `Revert to version ${reverting.version}` : "Revert"}
        confirmVariant="primary"
        icon="History"
        busy={busy}
        onConfirm={() => reverting && void revert(reverting)}
        onCancel={() => setReverting(null)}
      />
    </div>
  );
}
