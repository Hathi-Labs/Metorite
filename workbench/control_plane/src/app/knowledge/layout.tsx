"use client";

/**
 * /knowledge — the Knowledge app's frame (WS-61 KB-2a, `knowledge_base.md`
 * §11 and §11.1).
 *
 * The layout owns three things for every Knowledge route:
 *
 *   1. The app bar, with the name as the app's one `<h1>` (AGENTS.md rule
 *      11). The rail toggle, the space on screen as the subtitle, New page,
 *      and the Search tool (KB-3b). The view switch is not built yet.
 *   2. The rail of `RailRow`s: Search, Spaces, each space, and the open
 *      space's page tree (`components/KnowledgeRail.tsx`). On a phone it
 *      opens in the shell's drawer from the title, as the CRM's views do.
 *      A row's `+` and its New sub-page open the one New page form here,
 *      and its Archive asks here first (KB-2e).
 *   3. The first-open phase, decided by the FIRST `GET /knowledge/spaces` and
 *      by nothing else (`lib/phase.ts`). A 404 is the off state, and a 403
 *      is "no feature". A 404 on one page is never "off".
 *
 * It mounts no ⌘K listener, no palette, no bell and no assistant rail. The
 * shell owns those (`AGENTS.md` rule 10, `seams.test.ts`). The job
 * "Write a page" (KB-12) arrives as `?do=write-page`, and `ShellJob` opens
 * the New page form through the leave guard. The form saves nothing by itself.
 */

import { Suspense, useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { AppTopBar } from "@/components/AppTopBar";
import { useMobileDrawer } from "@/components/AppShell";
import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { useViewMode } from "@/components/ViewModeProvider";
import { invalidate } from "@/lib/dataCache";
import { railClass, useRailFold } from "@/lib/railFold";
import { ShellJob } from "@/lib/shell/doJob";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  KnowledgeContext,
  type LeaveGuard,
  type NewPageAsk,
  type PageFocus,
} from "./components/KnowledgeContext";
import KnowledgeRail, { type SubPageParent } from "./components/KnowledgeRail";
import NewPageDialog from "./components/NewPageDialog";
import { KNOWLEDGE_CACHE, KnowledgeError, SPACES_KEY, archivePage, listSpaces } from "./lib/api";
import type { PageSummary, Space } from "./lib/types";
import { KNOWLEDGE_HOME, KNOWLEDGE_SEARCH, pageHref, pageIdOf, spaceHref } from "./lib/links";
import {
  KNOWLEDGE_OFF_TITLE,
  LOAD_FAILED_BODY,
  LOAD_FAILED_TITLE,
  NO_FEATURE_BODY,
  NO_FEATURE_TITLE,
  SIGNED_OUT_BODY,
  SIGNED_OUT_TITLE,
  signInAgainHref,
  spacesPhase,
  spacesRefusal,
  type SpacesRead,
} from "./lib/phase";
import { SEARCH_INPUT_ID } from "./lib/search";

/** The spaces read. A 404 or a 403 is a state the page explains, not an error. */
async function readSpaces(): Promise<SpacesRead> {
  try {
    return { kind: "spaces", spaces: await listSpaces() };
  } catch (err) {
    const refusal = spacesRefusal(err);
    if (refusal) return refusal;
    throw err;
  }
}

/** The bar and the body of a state with no rail: loading, off, no feature. */
function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <AppTopBar title="Knowledge" />
      <div className="flex min-h-0 flex-1 flex-col overflow-auto">{children}</div>
    </div>
  );
}

function Loading() {
  return (
    <Frame>
      <div className="mx-auto w-full max-w-3xl p-6">
        <SkeletonRows count={4} />
      </div>
    </Frame>
  );
}

export default function KnowledgeLayout({ children }: { children: ReactNode }) {
  // `useSearchParams` needs a boundary of its own, or the route renders
  // nothing on the server. The fallback draws the bar, so the one `<h1>` is
  // there from the first paint.
  return (
    <Suspense fallback={<Loading />}>
      <KnowledgeShell>{children}</KnowledgeShell>
    </Suspense>
  );
}

function KnowledgeShell({ children }: { children: ReactNode }) {
  const read = useCachedResource<SpacesRead>(SPACES_KEY, readSpaces);
  const phase = spacesPhase(read);

  if (phase === "loading") return <Loading />;
  if (phase === "off") {
    return (
      <Frame>
        <EmptyState icon="PowerOff" message={KNOWLEDGE_OFF_TITLE} />
      </Frame>
    );
  }
  if (phase === "no-feature") {
    return (
      <Frame>
        <EmptyState icon="Lock" message={NO_FEATURE_TITLE} hint={NO_FEATURE_BODY} />
      </Frame>
    );
  }
  if (phase === "signed-out") {
    // A 401 from the BFF: nobody is signed in any more. A full navigation,
    // because the sign-in page is outside this app (fix round 1, P3-d).
    return (
      <Frame>
        <EmptyState
          icon="LogIn"
          message={SIGNED_OUT_TITLE}
          hint={SIGNED_OUT_BODY}
          action={{
            label: "Sign in again",
            icon: "LogIn",
            onClick: () =>
              window.location.assign(signInAgainHref(window.location.pathname + window.location.search)),
          }}
        />
      </Frame>
    );
  }
  if (phase === "error" || read.data?.kind !== "spaces") {
    return (
      <Frame>
        <EmptyState
          icon="CloudOff"
          message={LOAD_FAILED_TITLE}
          hint={LOAD_FAILED_BODY}
          action={{ label: "Try again", icon: "RefreshCw", onClick: read.refresh }}
        />
      </Frame>
    );
  }
  return <KnowledgeReady spaces={read.data.spaces}>{children}</KnowledgeReady>;
}

function KnowledgeReady({ spaces, children }: { spaces: Space[]; children: ReactNode }) {
  const pathname = usePathname() ?? KNOWLEDGE_HOME;
  const searchParams = useSearchParams();
  const router = useRouter();
  const { isMobile } = useViewMode();
  const { open: openDrawer, close: closeDrawer } = useMobileDrawer();
  const rail = useRailFold();

  const [focus, setFocus] = useState<PageFocus | null>(null);
  const [ask, setAsk] = useState<NewPageAsk | null>(null);
  const [archiving, setArchiving] = useState<PageSummary | null>(null);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const toast = useToast();

  const pageId = pageIdOf(pathname);
  const atHomePath = pathname === KNOWLEDGE_HOME;
  const atSearch = pathname === KNOWLEDGE_SEARCH;
  const spaceParam = atHomePath ? searchParams.get("space") : null;
  const selectedSpaceId = pageId ? (focus?.pageId === pageId ? focus.spaceId : null) : spaceParam;
  const selectedSpace = spaces.find((s) => s.id === selectedSpaceId) ?? null;

  const setPageFocus = useCallback((next: PageFocus | null) => setFocus(next), []);
  const openNewPage = useCallback((next?: NewPageAsk) => setAsk(next ?? {}), []);

  // The one leave guard (fix round 1, P1). A dirty page registers it, and
  // every way out of the page asks through `requestLeave` first.
  const guard = useRef<LeaveGuard | null>(null);
  const [leaving, setLeaving] = useState<{ act: () => void } | null>(null);
  const setLeaveGuard = useCallback((next: LeaveGuard | null) => {
    guard.current = next;
  }, []);
  const leaveGuarded = useCallback(() => guard.current !== null, []);
  const requestLeave = useCallback((act: () => void) => {
    if (guard.current) setLeaving({ act });
    else act();
  }, []);

  // Go to an address through the leave guard. On the Search view, the rail's
  // Search row and the bar's Search tool focus the box and keep the words.
  const go = useCallback(
    (href: string) => {
      if (href === KNOWLEDGE_SEARCH && atSearch) {
        document.getElementById(SEARCH_INPUT_ID)?.focus();
        return;
      }
      requestLeave(() => router.push(href));
    },
    [atSearch, requestLeave, router],
  );

  const context = useMemo(
    () => ({ spaces, setPageFocus, openNewPage, setLeaveGuard, leaveGuarded, requestLeave }),
    [spaces, setPageFocus, openNewPage, setLeaveGuard, leaveGuarded, requestLeave],
  );

  // A row's `+` and New sub-page (KB-2e). The form opens through the guard.
  const newSubPage = useCallback(
    (parent: SubPageParent) => requestLeave(() => openNewPage({ parent })),
    [requestLeave, openNewPage],
  );

  // A row's Archive, after the member confirms. The gateway asks for manage.
  const archive = useCallback(async () => {
    const target = archiving;
    if (!target) return;
    setArchiveBusy(true);
    try {
      await archivePage(target.id);
      setArchiving(null);
      invalidate(KNOWLEDGE_CACHE);
      toast.show({ key: `kb-archive:${target.id}`, title: `Archived ${target.title}` });
      // The open page left the tree, so the space opens in its place.
      if (target.id === pageId) go(spaceHref(target.space_id));
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      setArchiving(null);
      toast.show({
        key: `kb-archive:${target.id}`,
        variant: "error",
        title: "Could not archive the page",
        description: reason,
      });
    } finally {
      setArchiveBusy(false);
    }
  }, [archiving, go, pageId, toast]);

  const railProps = {
    spaces,
    selectedSpaceId,
    selectedPageId: pageId,
    atHome: atHomePath && !spaceParam,
    atSearch,
    onArchive: setArchiving,
  };

  const title = isMobile ? (
    <button
      type="button"
      onClick={() =>
        openDrawer(
          <nav aria-label="Knowledge spaces" className="p-2">
            <KnowledgeRail
              {...railProps}
              onNavigate={(href) => {
                closeDrawer();
                go(href);
              }}
              onNewSubPage={(parent) => {
                closeDrawer();
                newSubPage(parent);
              }}
              onArchive={(page) => {
                closeDrawer();
                setArchiving(page);
              }}
            />
          </nav>,
        )
      }
      className="inline-flex max-w-full items-center gap-1 align-middle tech-transition hover:opacity-80"
    >
      <span className="truncate">Knowledge</span>
      <Icon name="ChevronDown" size={14} className="shrink-0 text-muted-foreground" />
    </button>
  ) : (
    "Knowledge"
  );

  return (
    <KnowledgeContext.Provider value={context}>
      <ShellJob id="write-page" onOpen={() => requestLeave(() => openNewPage({ spaceId: selectedSpaceId }))} />
      <div className="flex h-full w-full flex-col overflow-hidden bg-background">
        <AppTopBar
          rail={{ open: rail.open, onToggle: rail.toggle, noun: "the spaces" }}
          title={title}
          subtitle={selectedSpace?.name}
          actions={
            <Button
              variant="primary"
              size="sm"
              icon="Plus"
              aria-label="New page"
              onClick={() => requestLeave(() => openNewPage({ spaceId: selectedSpaceId }))}
            >
              {isMobile ? null : "New page"}
            </Button>
          }
          tools={
            <Button
              variant="ghost"
              size="sm"
              icon="Search"
              aria-label="Search Knowledge"
              title="Search Knowledge"
              onClick={() => go(KNOWLEDGE_SEARCH)}
            >
              {isMobile ? null : "Search"}
            </Button>
          }
        />
        <div className="flex min-h-0 flex-1 overflow-hidden">
          {!isMobile && rail.open ? (
            <nav
              aria-label="Knowledge spaces"
              className={`w-60 shrink-0 overflow-y-auto border-r border-border bg-card p-2 ${railClass(rail.settled)}`}
            >
              <KnowledgeRail {...railProps} onNavigate={go} onNewSubPage={newSubPage} />
            </nav>
          ) : null}
          <main className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</main>
        </div>
      </div>
      <ConfirmDialog
        open={leaving !== null}
        title="Discard your changes?"
        body="Your changes to this page are not saved. If you leave now, they are lost."
        confirmLabel="Discard"
        cancelLabel="Keep editing"
        confirmVariant="destructive"
        defaultFocus="cancel"
        icon="TriangleAlert"
        onCancel={() => setLeaving(null)}
        onConfirm={() => {
          const held = leaving;
          setLeaving(null);
          guard.current?.onDiscard();
          guard.current = null;
          held?.act();
        }}
      />
      <ConfirmDialog
        open={archiving !== null}
        title="Archive this page?"
        subject={archiving?.title ?? null}
        body="The page leaves the tree for each member. Its sub-pages stay in the space. A manager of the space can restore it."
        confirmLabel="Archive page"
        cancelLabel="Keep the page"
        confirmVariant="destructive"
        defaultFocus="cancel"
        icon="Archive"
        busy={archiveBusy}
        onCancel={() => setArchiving(null)}
        onConfirm={() => void archive()}
      />
      <NewPageDialog
        ask={ask}
        spaces={spaces}
        onClose={() => setAsk(null)}
        onCreated={(page) => {
          setAsk(null);
          invalidate(KNOWLEDGE_CACHE);
          router.push(pageHref(page.id, { edit: true }));
        }}
      />
    </KnowledgeContext.Provider>
  );
}
