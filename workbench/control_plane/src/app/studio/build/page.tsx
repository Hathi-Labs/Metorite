"use client";

/**
 * /studio/build — the Studio IDE (`ai_studio.md` §6.3, slice G3a).
 *
 * Reading this as: a full-page app view under `AppTopBar` with a back link, like
 * `/studio/package-preview`. Three columns: the Explorer on the left, the editor in the centre
 * (Preview, Data, Views and Code tabs, with the Problems panel below), and the shell's own
 * assistant dock on the right.
 *
 * Interaction: the member opens an app in the IDE, picks a part in the Explorer, and edits it in a
 * tab (G3c): a field in Data, a view in Views, the JSON in Code. The bar shows "Unsaved changes"
 * with Save and Revert, and Save sends the draft through the one save (`draftSave.ts`, the route
 * that `studio_save` uses). They ask Mira in the dock. The dock holds the facts of this page (the
 * package id, the STORED draft and its hash), so Mira edits THIS app, and a save of the member
 * moves the hash that her next save uses. When her run ends, the page reads the draft again and
 * the Explorer, the Preview and the Problems follow. A poll never replaces an unsaved edit.
 *
 * G5: a change bar above the tabs says what the newest change in the draft did, in plain words, with
 * a one-step Undo through the same save. Publish is in the top bar: its dialog lists what changes in
 * the LIVE app, then sends the stored draft with its hash. The bar is in the IDE and not in the dock,
 * because the dock is the shell's one seam (D89).
 *
 * The shell is ONE seam (D89). The page mounts no chat, no palette, no bell and no key
 * handler. It publishes facts with `useAssistantFacts` and asks the shell to open its dock.
 * It is no pane, so `nav.ts` does not list it, and the access gate of `/studio` covers it.
 * An org outside `STUDIO_ORGS` gets 404 from the gateway, and the page shows "not on".
 */
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AppTopBar } from "@/components/AppTopBar";
import { EmptyState } from "@/components/EmptyState";
import RecordsStatus from "@/components/studio/RecordsStatus";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Tabs from "@/components/Tabs";
import { useOptionalSidebarFold } from "@/components/SidebarFold";
import { autoFoldEnabled } from "@/lib/sidebarFold";
import { useViewMode } from "@/components/ViewModeProvider";
import { DOCK_MIN_VIEWPORT, assistantState, dockFoldsNav, currentAssistantFacts, openAssistant, useAssistantFacts } from "@/lib/shell/assistant";
import { sendHeldBrief } from "@/lib/studio/buildHandoff";
import { buildOutline, type OutlineTab } from "@/lib/studio/buildOutline";
import type { Reveal } from "@/lib/studio/codeReveal";
import type { Problem } from "@/lib/studio/draftProblems";
import { checkDraft } from "@/lib/studio/draftProblems";
import { PendingTextContext } from "@/lib/studio/pendingText";
import { useDraftEditor } from "@/lib/studio/useDraftEditor";
import type { LiveObject } from "@/lib/studio/draftEdit";
import { useDraftWatch } from "@/lib/studio/draftWatch";
import { useLandedChange } from "@/lib/studio/useLandedChange";
import { publishBlock } from "@/lib/studio/publish";
import { ideFacts } from "@/lib/studio/ideFacts";
import { clearFoldMark, foldMarked, foldOnOpen, markFolded, restoreOnLeave } from "@/lib/studio/ideFold";
import { buildHref, draftHash, type PackageDraft } from "@/lib/studio/packageDraft";
import { previewHref } from "@/lib/studio/packagePreview";

import CodeTab from "./components/CodeTab";
import DataTab from "./components/DataTab";
import EditBar from "./components/EditBar";
import Explorer from "./components/Explorer";
import PreviewTab from "./components/PreviewTab";
import PublishDialog from "./components/PublishDialog";
import ChangeBar from "./components/ChangeBar";
import ProblemsPanel from "./components/ProblemsPanel";
import ViewsTab from "./components/ViewsTab";

const TABS = [
  { id: "preview", label: "Preview", icon: "Eye" },
  { id: "data", label: "Data", icon: "Table" },
  { id: "views", label: "Views", icon: "LayoutGrid" },
  { id: "code", label: "Code", icon: "Code" },
];
const PHONE_TABS = [
  { id: "explorer", label: "Explorer", icon: "ListTree" },
  { id: "editor", label: "Editor", icon: "PanelsTopLeft" },
  { id: "problems", label: "Problems", icon: "CircleAlert" },
];

/** The page frame. The title is the name of the app, and a page with no app yet is "Studio IDE". */
function Frame({ children, title, subtitle, actions, rail }: {
  children: React.ReactNode; title?: string; subtitle?: string; actions?: React.ReactNode;
  rail?: { open: boolean; onToggle: () => void; noun: string };
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <AppTopBar title={title?.trim() || "Studio IDE"} subtitle={subtitle} icon="Boxes"
        back={{ href: "/studio", label: "Back to Studio" }} rail={rail} actions={actions} />
      {children}
    </div>
  );
}

function Off() {
  const router = useRouter();
  return (
    <Frame>
      <EmptyState icon="Inbox" message="This app is not available to you."
        hint="AI Studio may be off for your workspace, or the app is not yours. Go back to Studio and open one of your apps."
        action={{ label: "Back to Studio", icon: "ArrowLeft", onClick: () => router.push("/studio") }} />
    </Frame>
  );
}

function Ide({ pkg }: { pkg: string }) {
  const router = useRouter();
  const params = useSearchParams();
  const { isMobile } = useViewMode();
  const rawWatch = useDraftWatch(pkg);
  const change = useLandedChange(pkg, rawWatch);
  // The member's own save marks its hash first, so the change bar says "You saved" and not "Mira or another tab".
  const { markOwn } = change;
  const watch = useMemo(() => ({ ...rawWatch, adopt: (next: PackageDraft) => { markOwn("you", next.manifestHash); rawWatch.adopt(next); } }), [rawWatch, markOwn]);
  const { draft } = watch;
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishProblems, setPublishProblems] = useState<Problem[]>([]);
  const [pendingText, setPendingText] = useState(0);
  const editor = useDraftEditor(pkg, watch, pendingText);
  const reportPending = useCallback((d: 1 | -1) => setPendingText((n) => Math.max(0, n + d)), []);
  const [tab, setTab] = useState<string>("preview");
  const [phoneTab, setPhoneTab] = useState<string>("editor");
  const [explorerOpen, setExplorerOpen] = useState(true);
  const [problemsOpen, setProblemsOpen] = useState(true);
  const [phoneProblemsOpen, setPhoneProblemsOpen] = useState(true);
  const [reveal, setReveal] = useState<(Reveal & { seq: number }) | null>(null);
  const object = params.get("object");

  // The editor gets the width: the app nav folds while the IDE is open, and opens again on leave.
  // The ref holds the latest fold, so the cleanup reads the state of that moment.
  const fold = useOptionalSidebarFold();
  const foldRef = useRef(fold);
  useEffect(() => { foldRef.current = fold; });
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    // A strict-mode remount runs the cleanup and the effect at once. The give-back waits one tick, and
    // the effect cancels it, so only a real leave gives the nav back.
    if (leaveTimer.current) { clearTimeout(leaveTimer.current); leaveTimer.current = null; }
    const f = foldRef.current;
    if (!f) return;
    // A fold that the IDE made before a reload stays marked. A new fold needs the auto fold on.
    if (foldOnOpen({ collapsed: f.collapsed, enabled: autoFoldEnabled() })) {
      f.foldForWork();
      markFolded();
    }
    return () => {
      leaveTimer.current = setTimeout(() => {
        leaveTimer.current = null;
        const now = foldRef.current;
        const st = assistantState();
        const dockHolds = st.open && st.mode === "dock" && dockFoldsNav(window.innerWidth, false);
        if (now && restoreOnLeave({ marked: foldMarked(), collapsed: now.collapsed, dockHolds })) now.toggle();
        clearFoldMark();
      }, 0);
    };
  }, []);

  // The dock opens on the right once, when the app is on screen. A member who closes it keeps it closed.
  useEffect(() => {
    // Below the dock floor the dock would float over the page, so the shell's strip and sheet serve a phone.
    if (window.innerWidth >= DOCK_MIN_VIEWPORT && !assistantState().open) openAssistant();
  }, []);

  // The tabs, the Explorer and the Problems draw what the member edits. The facts below keep the STORED draft.
  const manifest = editor.shown;
  const hash = useMemo(() => draftHash(manifest), [manifest]);
  const outline = useMemo(() => buildOutline(manifest), [manifest]);
  const { server } = editor.state;
  const checked = useMemo(() => [...server, ...checkDraft(manifest)], [server, manifest]);
  // A refusal of a publish is shown in the panel too, but it never blocks the next try.
  const problems = useMemo(() => [...checked, ...publishProblems], [checked, publishProblems]);
  // A refusal of the server check lands in the Problems panel at its path, so the panel opens for it.
  const [seenServer, setSeenServer] = useState(server);
  if (server !== seenServer) {
    setSeenServer(server);
    if (server.length > 0) { setProblemsOpen(true); setPhoneProblemsOpen(true); }
  }
  const liveRows = useMemo(() => (draft?.objects ?? []) as unknown as LiveObject[], [draft]);
  // No facts when the app is gone (404): Mira must never save to a deleted app.
  useAssistantFacts(
    draft && !watch.off ? ideFacts({ id: draft.id, name: draft.name }, draft.manifest, draft.manifestHash) : null,
  );
  // "Build it" held a brief for this package. The facts above are published by the effect of the same
  // commit, which runs before this one, so the ask now names THIS package and makes no second one.
  const draftId = draft && !watch.off ? draft.id : null;
  useEffect(() => {
    if (!draftId) return;
    sendHeldBrief({
      pkg,
      facts: currentAssistantFacts(),
      send: (text) => openAssistant({ text, fresh: true }),
    });
  }, [draftId, pkg]);

  if (watch.off) return <Off />;
  if (!draft) {
    return (
      <Frame>
        <div className="p-4">
          {watch.stale && !watch.loading ? (
            <p role="alert" className="text-sm text-destructive">{watch.stale}</p>
          ) : (
            <RecordsStatus loading error={null} onRetry={watch.refresh} />
          )}
        </div>
      </Frame>
    );
  }

  const setObject = (name: string) => router.replace(buildHref(pkg, name));
  const pick = (p: { object: string | null; tab: OutlineTab | null; reveal?: Reveal }) => {
    if (p.object) setObject(p.object);
    if (p.tab) setTab(p.tab);
    if (p.reveal) setReveal({ ...p.reveal, seq: (reveal?.seq ?? 0) + 1 });
    if (isMobile) setPhoneTab("editor");
  };
  const live = draft.liveVersion !== null;
  const block = publishBlock({
    dirty: editor.dirty, saving: editor.state.saving, undoing: change.undoing, fault: editor.state.fault !== null,
    errors: checked.filter((x) => x.severity === "error").length, hash: draft.manifestHash,
  });
  const actions = (
    <>
      <Badge tone={live ? "success" : "warning"} size="xs">{live ? `Live v${draft.liveVersion}` : "Draft"}</Badge>
      <Button variant="primary" size={isMobile ? "icon" : "sm"} icon="Rocket" disabled={block !== null}
        aria-label={block ? `Publish. ${block}` : "Publish"} title={block ?? "Publish the saved draft"}
        onClick={() => { setPublishProblems([]); setPublishOpen(true); }}>
        {isMobile ? null : "Publish"}
      </Button>
      <Button variant="secondary" size="sm" icon="ExternalLink" href={previewHref(pkg, object ?? undefined)}>
        Open app
      </Button>
    </>
  );

  const explorer = <Explorer outline={outline} selectedObject={object} onPick={pick} />;
  const stale = watch.stale ? (
    <p role="status" className="border-b border-border px-3 py-1 text-xs text-muted-foreground">{watch.stale}</p>
  ) : null;
  const locked = editor.locked || change.undoing;
  const lockedLine = editor.state.fault !== null && tab !== "code" ? (
    <p role="status" className="border-b border-border px-3 py-1 text-xs text-warning">
      The Code tab holds JSON that does not parse. Fix it there, or press Revert, before you edit here.
    </p>
  ) : null;
  const body = (
    <PendingTextContext.Provider value={reportPending}>
    <div className="min-h-0 flex-1 overflow-y-auto">
      {tab === "preview" ? <PreviewTab manifest={manifest} hash={hash} pkg={pkg} object={object} onObject={setObject} /> : null}
      {tab === "data" ? <DataTab manifest={manifest} object={object} onObject={setObject} onChange={editor.edit} live={liveRows} locked={locked} /> : null}
      {tab === "views" ? <ViewsTab manifest={manifest} onChange={editor.edit} locked={locked} /> : null}
      {tab === "code" ? <CodeTab manifest={manifest} text={editor.codeText} editorKey={editor.codeKey} readOnly={editor.state.saving || change.undoing} fault={editor.state.fault} reveal={reveal}
        onText={editor.typeCode} onSave={editor.save} /> : null}
    </div>
    </PendingTextContext.Provider>
  );
  const editorPane = (
    <>
      <ChangeBar landed={change.landed} dirty={editor.dirty} saving={editor.state.saving} fault={editor.state.fault !== null}
        undoing={change.undoing} note={change.note} liveBlock={change.liveBlock} onUndo={change.undo} onReload={editor.reload} />
      <Tabs tabs={TABS} activeTab={tab} onTabChange={setTab} variant="underline" chrome="inline" className="px-2 pt-1" />
      <EditBar state={editor.state} dirty={editor.dirty} newer={editor.newer} onSave={editor.save} onRevert={editor.revert} onReload={editor.reload} hideJson={tab === "code"}
        changes={editor.changes} editText={editor.editText} />
      {stale}
      {lockedLine}
      {body}
    </>
  );

  const dialog = (
    <PublishDialog open={publishOpen} pkg={pkg} name={draft.name} manifest={draft.manifest} hash={draft.manifestHash}
      liveVersion={draft.liveVersion} onClose={() => setPublishOpen(false)} onPublished={watch.refresh}
      onProblems={(ps) => { setPublishProblems(ps); setProblemsOpen(true); setPhoneProblemsOpen(true); }} onReload={editor.reload} />
  );

  if (isMobile) {
    return (
      <Frame title={draft.name} actions={actions}>
        <Tabs tabs={PHONE_TABS} activeTab={phoneTab} onTabChange={setPhoneTab} variant="segmented" chrome="inline" className="p-2" />
        <div className="flex min-h-0 flex-1 flex-col">
          {phoneTab === "explorer" ? <div className="min-h-0 flex-1 overflow-y-auto">{explorer}</div> : null}
          {phoneTab === "editor" ? editorPane : null}
          {phoneTab === "problems" ? <ProblemsPanel problems={problems} open={phoneProblemsOpen} onToggle={() => setPhoneProblemsOpen((o) => !o)} fill /> : null}
        </div>
        {dialog}
      </Frame>
    );
  }

  return (
    <Frame title={draft.name} actions={actions}
      rail={{ open: explorerOpen, onToggle: () => setExplorerOpen((o) => !o), noun: "the explorer" }}>
      <div className="flex min-h-0 min-w-0 flex-1">
        {explorerOpen ? (
          <aside aria-label="Explorer" className="w-60 shrink-0 overflow-y-auto border-r border-border bg-card/40">
            {explorer}
          </aside>
        ) : null}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {editorPane}
          <ProblemsPanel problems={problems} open={problemsOpen} onToggle={() => setProblemsOpen((o) => !o)} />
        </div>
      </div>
      {dialog}
    </Frame>
  );
}

function BuildBody() {
  const pkg = useSearchParams().get("pkg");
  if (!pkg) {
    return (
      <Frame>
        <EmptyState icon="PackageOpen" message="No app is open in the IDE."
          hint="Go back to Studio, then press Build it or open an app." />
      </Frame>
    );
  }
  return <Ide pkg={pkg} />;
}

export default function StudioBuildPage() {
  return (
    <Suspense fallback={null}>
      <BuildBody />
    </Suspense>
  );
}
