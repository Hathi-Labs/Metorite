"use client";

/**
 * The Search view (WS-61 KB-3b, `knowledge_base.md` §11.1).
 *
 * One box, and the published pages that match it, grouped by space. Each row
 * is one page, with its heading path and up to two snippets, and it opens the
 * page at `/knowledge/pages/<id>`. These are the hits `GET /knowledge/search`
 * gives the assistant too (§6.3).
 *
 * The words live in the address (`?q=`), so a search is a link and a reload
 * shows the same hits. The view asks 300 ms after the last keystroke. It
 * cancels the request before, and drops an answer for other words
 * (`lib/search.ts`). It shows nothing for `degraded`.
 *
 * It is a view of the app and not a palette. It mounts no key listener, and
 * the layout's bar and rail open it (`AGENTS.md` rule 10).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import { Input } from "@/components/ui/Input";
import { SkeletonRows } from "@/components/ui/Skeleton";

import { searchKnowledge } from "../lib/api";
import { spaceIcon } from "../lib/labels";
import { pageHref, searchHref } from "../lib/links";
import {
  SEARCH_DEBOUNCE_MS,
  SEARCH_INPUT_ID,
  SEARCH_MAX,
  SEARCH_MIN,
  groupHits,
  highlight,
  isAbort,
  isCurrent,
  normalizeQuery,
  queryState,
  resultCount,
  type SpaceGroup,
} from "../lib/search";
import type { SearchHit } from "../lib/types";
import { useKnowledge } from "./KnowledgeContext";

/** The last answer the view holds, for the words it answers. */
interface Answer {
  query: string;
  hits: SearchHit[];
}

/** A request that failed, for the words it asked. The view says why in its own words. */
interface Failure {
  query: string;
}

export default function KnowledgeSearch() {
  const router = useRouter();
  const urlQuery = useSearchParams().get("q") ?? "";

  const [raw, setRaw] = useState(urlQuery);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [retry, setRetry] = useState(0);

  // The address follows the box (the effect below). When the address
  // changes from outside, such as Back, the box follows the address. Set
  // during render, the adjust-state-on-a-prop-change pattern.
  const [written, setWritten] = useState(urlQuery);
  const [seenUrl, setSeenUrl] = useState(urlQuery);
  if (urlQuery !== seenUrl) {
    setSeenUrl(urlQuery);
    if (urlQuery !== written) {
      setWritten(urlQuery);
      setRaw(urlQuery);
    }
  }

  const query = normalizeQuery(raw);
  const state = queryState(raw);

  // The live words, for an answer that lands after newer keystrokes.
  const live = useRef(query);
  useEffect(() => {
    live.current = query;
  }, [query]);

  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
  }, []);

  useEffect(() => {
    if (state !== "ok") return;
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const res = await searchKnowledge(query, { signal: ctrl.signal });
        // An answer for other words is stale. A newer request is on its way.
        if (!isCurrent(res?.query ?? "", live.current)) return;
        setAnswer({ query: res.query, hits: res.hits ?? [] });
        setFailure(null);
      } catch (err) {
        if (isAbort(err) || !isCurrent(query, live.current)) return;
        setFailure({ query });
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [query, state, retry]);

  // The address, after the same wait, so the history does not fill with
  // each letter. `replace`, so Back leaves the view in one step.
  useEffect(() => {
    const next = searchHref(raw);
    if (next === searchHref(written)) return;
    const timer = setTimeout(() => {
      setWritten(raw.trim() ? raw : "");
      router.replace(next, { scroll: false });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [raw, written, router]);

  const clear = useCallback(() => {
    setRaw("");
    input.current?.focus();
  }, []);

  // Derived, so no effect has to reset them. A request is pending while
  // neither an answer nor a failure is for the live words.
  const answered = answer !== null && isCurrent(answer.query, query);
  const failed = failure !== null && isCurrent(failure.query, query);
  const pending = state === "ok" && !answered && !failed;
  const error = state === "ok" && failed;
  const shown = state === "ok" ? answer : null;
  const groups = useMemo(() => (shown ? groupHits(shown.hits) : []), [shown]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {/* The frame of the Spaces view, so the title stays in one place when
          the member moves between the two. The box and the hits keep a
          reading width inside it. */}
      <div className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6">
        <div className="max-w-3xl">
          <h2 className="text-base font-semibold text-foreground">Search</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Find a published page by a word in its title or its text.
          </p>
          <div className="mt-4">
            <Input
              ref={input}
              id={SEARCH_INPUT_ID}
              icon="Search"
              type="text"
              role="searchbox"
              enterKeyHint="search"
              inputSize="lg"
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape" && raw) {
                  e.preventDefault();
                  clear();
                }
              }}
              placeholder="Find in Knowledge"
              aria-label="Find in Knowledge"
              aria-describedby="knowledge-search-status"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <p
            id="knowledge-search-status"
            role="status"
            className="mt-2 min-h-4 text-xs text-muted-foreground"
          >
            {statusLine(state, pending, error, shown, groups)}
          </p>
          <div className="mt-3">
            <Body
              state={state}
              pending={pending}
              error={error}
              shown={shown}
              groups={groups}
              query={query}
              onClear={clear}
              onRetry={() => {
                setFailure(null);
                setRetry((n) => n + 1);
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

/** The one line under the box. A screen reader hears it when it changes. */
function statusLine(
  state: ReturnType<typeof queryState>,
  pending: boolean,
  error: boolean,
  shown: Answer | null,
  groups: SpaceGroup[],
): string {
  if (state === "short")
    return `Type ${SEARCH_MIN} or more characters to search.`;
  if (state === "long") return `Use ${SEARCH_MAX} characters or fewer.`;
  if (state === "empty" || error) return "";
  if (pending) return "Searching…";
  if (shown && groups.length) return resultCount(groups);
  return "";
}

function Body({
  state,
  pending,
  error,
  shown,
  groups,
  query,
  onClear,
  onRetry,
}: {
  state: ReturnType<typeof queryState>;
  pending: boolean;
  error: boolean;
  shown: Answer | null;
  groups: SpaceGroup[];
  query: string;
  onClear: () => void;
  onRetry: () => void;
}) {
  if (state === "empty") {
    return (
      <EmptyState
        icon="Search"
        message="Search the pages of every space you can open."
        hint="Only published pages show here. A draft shows in its space."
      />
    );
  }
  if (state !== "ok") return null;
  if (error) {
    return (
      <EmptyState
        icon="CloudOff"
        message="Could not search Knowledge."
        hint="The search did not answer. Check your connection, then try again."
        action={{ label: "Try again", icon: "RefreshCw", onClick: onRetry }}
      />
    );
  }
  // Only a true miss earns a skeleton. While newer words are on their way,
  // the last hits stay on screen (AGENTS.md rule 9).
  if (!shown) {
    return pending ? <SkeletonRows count={4} /> : null;
  }
  if (!groups.length) {
    return (
      <EmptyState
        icon="SearchX"
        message={`No published page matches “${shown.query}”.`}
        hint="Try another word, or fewer words."
        action={{ label: "Clear the search", icon: "X", onClick: onClear }}
      />
    );
  }
  return (
    <div
      className={`flex flex-col gap-5 ${pending ? "opacity-70" : ""}`}
      aria-busy={pending || undefined}
    >
      {groups.map((group) => (
        <SpaceResults key={group.spaceId} group={group} query={query} />
      ))}
    </div>
  );
}

function SpaceResults({ group, query }: { group: SpaceGroup; query: string }) {
  const { spaces } = useKnowledge();
  const space = spaces.find((s) => s.id === group.spaceId);
  const headingId = `knowledge-search-space-${group.spaceId}`;
  return (
    <section aria-labelledby={headingId}>
      <h3
        id={headingId}
        className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground"
      >
        <Icon
          name={space ? spaceIcon(space) : "BookOpen"}
          size={14}
          className="shrink-0"
        />
        <span className="truncate">{group.spaceName}</span>
      </h3>
      {/* One card per page, as the space cards draw theirs. No clip on the
          list, so the focus ring of a row stays in sight. */}
      <ul className="mt-2 flex flex-col gap-2">
        {group.pages.map((page) => (
          <li key={page.pageId}>
            <Link
              href={pageHref(page.pageId)}
              className="cc-link flex gap-3 rounded-lg border border-border bg-card px-4 py-3 tech-transition hover:border-primary/40 hover:bg-muted/40"
            >
              <Icon
                name="FileText"
                size={16}
                className="mt-0.5 shrink-0 text-muted-foreground"
              />
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-sm font-medium text-foreground">
                  <Marked text={page.title} query={query} />
                </span>
                {page.snippets.map((snippet, i) => (
                  <span key={i} className="mt-1.5 flex min-w-0 flex-col">
                    {snippet.headingPath ? (
                      <span className="truncate text-[11px] text-muted-foreground">
                        {snippet.headingPath}
                      </span>
                    ) : null}
                    <span className="line-clamp-2 text-xs text-muted-foreground">
                      <Marked text={snippet.text} query={query} />
                    </span>
                  </span>
                ))}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** A text with the words of the query marked. React text, never HTML. */
function Marked({ text, query }: { text: string; query: string }) {
  return (
    <>
      {highlight(text, query).map((part, i) =>
        part.match ? (
          <mark
            key={i}
            className="rounded-sm bg-primary/25 px-0.5 text-foreground"
          >
            {part.text}
          </mark>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}
