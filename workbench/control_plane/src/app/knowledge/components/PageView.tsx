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
 * A 404 on this page means "not found". It never means "off": the layout
 * decides "off" from the spaces read alone (`lib/phase.ts`).
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

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
import { dayLabel, patchBody, reasonLabel, stateLabel, stateTone, STATE_LABELS } from "../lib/labels";
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

export default function PageView({ pageId }: { pageId: string }) {
  const { spaces, setPageFocus, openNewPage } = useKnowledge();
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
  const [draft, setDraft] = useState({ title: "", body_md: "" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [reverting, setReverting] = useState<PageVersion | null>(null);
  const [busy, setBusy] = useState(false);

  const writable = Boolean(page?.can_write) && page?.state !== "archived";

  function startEdit(from: PageDetail) {
    setDraft({ title: from.title, body_md: from.body_md });
    setTab("write");
    setSaveError(null);
    setEditing(true);
  }

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

  async function save() {
    if (!page) return;
    const patch = patchBody(page, draft);
    if (!patch) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await updatePage(page.id, patch);
      setEditing(false);
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
  const patch = editing ? patchBody(page, draft) : null;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-5 sm:px-6 xl:flex-row xl:items-start">
        <article className="min-w-0 flex-1" aria-label={page.title}>
          {space ? (
            <Link href={spaceHref(space.id)} className="cc-link text-xs text-muted-foreground hover:text-foreground">
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
              {saveError ? (
                <p role="alert" className="text-xs text-destructive">
                  {saveError}
                </p>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" loading={saving} disabled={saving || !patch || !draft.title.trim()} onClick={() => void save()}>
                  Save changes
                </Button>
                <Button variant="secondary" size="sm" disabled={saving} onClick={() => setEditing(false)}>
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
            <VersionList versions={page.versions} canRevert={writable} onRevert={setReverting} />
          </section>
        </aside>
      </div>

      <ConfirmDialog
        open={confirmPublish}
        title="Publish this page?"
        subject={page.title}
        body={`Every reader of ${space?.name ?? "this space"} can then read it, and search will find it. The review date is set to 180 days from today.`}
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
