"use client";

/**
 * One version of a page, read-only, in the history modal (WS-61 KB-2g).
 *
 * The body draws through `KnowledgeEditor` in read mode, the renderer of the
 * page itself, so an old version reads as the page did. Each image goes
 * through `MarkdownImage`, and the view makes no HTML sink.
 *
 * With `against`, the view compares: it draws the newer version and marks in
 * place what changed since the older one (`lib/historyDiff.ts`). Added words
 * take a tint of `--success`, and removed words show struck through in
 * `--destructive`, at the place they left. "Highlight changes" off draws the
 * version with no marks. The view tells its parent the counts.
 */

import { useEffect, useMemo, useRef } from "react";
import { getSchema } from "@tiptap/core";
import type { Node as PMNode, Schema } from "@tiptap/pm/model";

import { EmptyState } from "@/components/EmptyState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";

import { KnowledgeError, getVersion } from "../../lib/api";
import { parseBody } from "../../lib/editor/markdown";
import { knowledgeExtensions } from "../../lib/editor/schema";
import { authorName, versionDate, versionKey, type PageVersionBody } from "../../lib/history";
import { compareDecorations, compareDocs, type Compare } from "../../lib/historyDiff";
import { reasonLabel } from "../../lib/labels";
import KnowledgeEditor from "../editor/KnowledgeEditor";

/** The schema of the editor, made once, to parse the older side of a compare. */
let schemaOnce: Schema | null = null;
function knowledgeSchema(): Schema {
  schemaOnce ??= getSchema(knowledgeExtensions());
  return schemaOnce;
}

type VersionRead = { kind: "version"; version: PageVersionBody } | { kind: "missing" };

async function readVersion(pageId: string, n: number): Promise<VersionRead> {
  try {
    return { kind: "version", version: await getVersion(pageId, n) };
  } catch (err) {
    if (err instanceof KnowledgeError && err.status === 404) return { kind: "missing" };
    throw err;
  }
}

function useVersion(pageId: string, n: number | null) {
  return useCachedResource<VersionRead>(n === null ? null : versionKey(pageId, n), () =>
    readVersion(pageId, n as number),
  );
}

export default function HistoryView({
  pageId,
  version,
  against,
  highlight,
  current,
  onCompare,
  onNavigate,
}: {
  pageId: string;
  /** The version on show: the newer side of a compare. */
  version: number;
  /** The older side of a compare, or null for no compare. */
  against: number | null;
  highlight: boolean;
  /** The change the arrows moved to, from 1. 0 is none. */
  current: number;
  /** The compare once both sides have loaded, and null while there is none. */
  onCompare: (compare: Compare | null) => void;
  /** A click on a link to a page of this app. */
  onNavigate: (href: string) => void;
}) {
  const shown = useVersion(pageId, version);
  const older = useVersion(pageId, against);
  const body = shown.data?.kind === "version" ? shown.data.version : null;
  const before = older.data?.kind === "version" ? older.data.version : null;

  // The compare, from the same parse the editor makes of each body.
  const compare = useMemo(() => {
    if (!body || !before || against === null) return null;
    const schema = knowledgeSchema();
    return {
      newer: schema.nodeFromJSON(parseBody(body.body_md)),
      older: schema.nodeFromJSON(parseBody(before.body_md)),
    };
  }, [body, before, against]);
  const result = useMemo(() => (compare ? compareDocs(compare.older, compare.newer) : null), [compare]);

  const onCompareRef = useRef(onCompare);
  useEffect(() => {
    onCompareRef.current = onCompare;
  });
  useEffect(() => {
    onCompareRef.current(result);
  }, [result]);

  // The marks on the editor's own document. A document that differs from
  // the parse here (it should not) gets its own compare, so a mark never
  // lands in the wrong place.
  const decorate = useMemo(() => {
    if (!highlight || !compare || !result) return null;
    let last: { doc: PMNode; value: Compare } | null = null;
    return (doc: PMNode) => {
      let value = result;
      if (!doc.eq(compare.newer)) {
        if (last?.doc !== doc) last = { doc, value: compareDocs(compare.older, doc) };
        value = last.value;
      }
      return compareDecorations(doc, value, current);
    };
  }, [highlight, compare, result, current]);

  if (shown.loading || (against !== null && older.loading)) {
    return <SkeletonRows count={6} />;
  }
  if (shown.data?.kind === "missing" || older.data?.kind === "missing") {
    return (
      <EmptyState
        icon="SearchX"
        message="This version is not there for you."
        hint="A version from before the page moved to this space stays private. Pick another version."
      />
    );
  }
  if (!body) {
    return (
      <EmptyState
        icon="CloudOff"
        message="This version did not load."
        hint="The server did not answer. Try again in a moment."
        action={{ label: "Try again", icon: "RefreshCw", onClick: shown.refresh }}
      />
    );
  }

  const titleChanged = highlight && before !== null && before.title !== body.title;
  return (
    <article aria-label={`Version ${body.version}, ${body.title}`}>
      <h3 className="break-words text-xl font-semibold text-foreground">{body.title}</h3>
      {titleChanged ? (
        <p className="mt-1 text-xs text-muted-foreground">
          Title changed from <del className="kb-diff-removed">{before.title}</del>
        </p>
      ) : null}
      <p className="mt-1 text-xs text-muted-foreground">
        Version {body.version} · {reasonLabel(body.reason)} by {authorName(body.author_email)}
        {body.created_at ? ` · ${versionDate(body.created_at)}` : ""}
      </p>
      <div className="mt-5">
        {body.body_md.trim() || (decorate && result?.removedWords) ? (
          <KnowledgeEditor
            // A new pair is a new editor, so no mark of the last compare stays.
            key={`${body.version}:${against ?? "none"}`}
            mode="read"
            body={body.body_md}
            label={`Version ${body.version} of ${body.title}, the page body`}
            decorate={decorate}
            onNavigate={onNavigate}
          />
        ) : (
          <p className="text-sm text-muted-foreground">This version is empty.</p>
        )}
      </div>
    </article>
  );
}
