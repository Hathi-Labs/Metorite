"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AssistantToggle from "@/components/AssistantToggle";
import { AppSearchButton, AppTopBar } from "@/components/AppTopBar";
import Button from "@/components/ui/Button";
import { useViewMode } from "@/components/ViewModeProvider";
import { useMobileDrawer } from "@/components/AppShell";
import { railClass, useRailFold } from "@/lib/railFold";
import { TASK_PANEL_WIDTH } from "@/lib/taskPanel";
import { useTaskStore } from "./lib/taskStore";
import { ListsSidebar } from "./components/ListsSidebar";
import { CaptureBar } from "./components/CaptureBar";
import { ItemList } from "./components/ItemList";
import { ItemDetail } from "./components/ItemDetail";
import { AssistantRail } from "./components/AssistantRail";
import { InboxView } from "./components/InboxView";
import { EngageView } from "./components/EngageView";
import { LedProjectView } from "./components/LedProjectView";
import { QuickCapture } from "./components/QuickCapture";
// ⚠️ `WorkspacesModal` was deleted 2026-08-25 (D52, WS-39 S1 repair round 1).
// It was the ClickUp connect flow — paste token → list workspaces → connect —
// and every one of its calls ended in `build_provider` → 400 "Unknown
// provider" once the registry emptied. Its only entry point was the sidebar's
// "Connect workspace…" button, deleted with it.
import { TaskSettingsModal } from "./components/TaskSettingsModal";
import { TaskFocusModal } from "./components/TaskFocusModal";
import { ReclarifyModal } from "./components/ReclarifyModal";
import { UndoToast } from "./components/UndoToast";
import { SyncFailureToast } from "./components/SyncFailureToast";
import { PromoteToast } from "./components/PromoteToast";
import { PromoteHost } from "./components/PromoteHost";
import { DeleteConfirmModal } from "./components/DeleteConfirmModal";
import { SchedulePopup } from "./components/SchedulePopup";
import { EliminatePopup } from "./components/EliminatePopup";
import { DelegatePopup } from "./components/DelegatePopup";
import { TasksShortcuts } from "./components/TasksShortcuts";
import { tasksOverlayOpen } from "./lib/shortcuts";
// ⌘K is search in both task apps, and it is ONE palette. My Tasks mounts the
// Projects one with no commands (`paletteCommands`), so it searches tasks only.
import { SearchPalette } from "../projects/components/SearchPalette";
import { NotificationBell } from "../projects/components/NotificationBell";
import { isOpenShortcut } from "../projects/lib/search";
import { hitTarget, searchAllowed } from "./lib/searchHit";

// My Tasks — 4-panel shell, mirroring the email app's layout
// philosophy: Lists/Contexts · Item list (+ capture) · Item detail · Assistant.
// UI-first: runs entirely on mock data (lib/mockData.ts); the gateway `/tasks`
// API is wired later. See project-docs/specs/task_manager_app.md.
export default function TasksPage() {
  const { isMobile } = useViewMode();
  const { open: openDrawer, close: closeDrawer } = useMobileDrawer();
  const selectedView = useTaskStore((s) => s.selectedView);
  const selectView = useTaskStore((s) => s.selectView);
  const openQuickCapture = useTaskStore((s) => s.openQuickCapture);
  const quickCaptureOpen = useTaskStore((s) => s.quickCaptureOpen);
  const clarifyModalOpen = useTaskStore((s) => s.clarifyModalOpen);
  const hydrate = useTaskStore((s) => s.hydrate);
  const selectedItemId = useTaskStore((s) => s.selectedItemId);
  const focusedItemId = useTaskStore((s) => s.focusedItemId);
  const selectItem = useTaskStore((s) => s.selectItem);
  const closeFocus = useTaskStore((s) => s.closeFocus);
  const openFocus = useTaskStore((s) => s.openFocus);
  const router = useRouter();
  // The lists rail folds itself below `lg`, so the pane beside it keeps a
  // usable width on a tablet. The toggle still opens it, and the member's
  // choice holds until the width changes band (`lib/railFold.ts`).
  const lists = useRailFold();
  // The ⌘K search palette. Capture is `C` and the Capture button.
  const [searching, setSearching] = useState(false);
  // The AI assistant opens as a scene from the left sidebar (email-app pattern),
  // not an always-on right rail.
  const [assistantOpen, setAssistantOpen] = useState(false);
  // Maximise: the docked pane raised into TaskFocusModal for reading width.
  // Holds the id it was raised FOR, and the overlay is derived from it below —
  // a bare boolean would need an effect to unset itself when the selection goes
  // away (deleting the task from the overlay is the ordinary way), and a stale
  // one would open the next task the user clicked straight into the overlay.
  const [maximisedFor, setMaximisedFor] = useState<string | null>(null);
  const isInbox = selectedView === "inbox";
  const isEngage = selectedView === "engage";
  // S6e — a project I lead, opened from the sidebar. Its own surface: my
  // tasks first, everybody's open count, and the board one link away.
  const isLed = selectedView === "projects";

  // The list/board surface is the only one with a docked detail column: Inbox
  // clarifies in place (its own ClarifyModal), Engage is full-width by design,
  // and the Assistant replaces the panes entirely. (Calendar was a fourth
  // exception here until D54 moved it to its own app — WS-39 S2.)
  const paneDocked = !isMobile && !assistantOpen && !isInbox && !isEngage;

  // On the docked surface the pane IS the detail view, so a row that calls the
  // app-wide `openFocus` verb (TaskCard, TaskBoard, WaitingForView, the grouped
  // list) must select into the pane rather than raise the overlay. `openFocus`
  // sets `selectedItemId` as well as `focusedItemId`, so dropping the focus
  // half is the whole change — and it has to be dropped rather than ignored, or
  // a stale id would pop the overlay open the moment the user switched to a
  // full-width surface, which reads the same store field. ⚠️ Still true across
  // apps since D54: `/calendar` reads this same store and mounts its own
  // `TaskFocusModal`, so a focus id left set here follows the user there.
  useEffect(() => {
    if (paneDocked && focusedItemId) closeFocus();
  }, [paneDocked, focusedItemId, closeFocus]);

  // Which task the overlay is showing, derived — never stored. Maximise is a
  // mode of THIS selection: leave the surface, delete the task, or navigate to
  // another one (a subtask opened from inside the overlay moves
  // `selectedItemId`) and the overlay drops back to the docked pane on whatever
  // is selected now, rather than hanging on the task you navigated away from.
  const maximisedId =
    paneDocked && maximisedFor === selectedItemId ? selectedItemId : null;

  const openMaximised = useCallback(
    () => setMaximisedFor(selectedItemId),
    [selectedItemId],
  );
  const closeMaximised = useCallback(() => setMaximisedFor(null), []);
  const closeDetail = useCallback(() => selectItem(null), [selectItem]);

  // Load live data from the gateway once; stays on the bundled mock data when
  // the backend isn't reachable (UI-first demo mode).
  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  // Tell the mobile bottom bar which My Tasks section is active (for its highlight).
  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent("cc-tasks-section", { detail: selectedView }),
    );
  }, [selectedView]);

  // Mobile bottom-nav tabs (from AppShell) → drive the tasks app.
  useEffect(() => {
    const handler = (e: Event) => {
      const tab = (e as CustomEvent<string>).detail;
      if (tab === "tasks-inbox") {
        selectView("inbox");
        closeDrawer();
      } else if (tab === "tasks-lists") {
        openDrawer(<ListsSidebar titled onNavigate={closeDrawer} />);
      } else if (tab === "tasks-capture") {
        openQuickCapture("single");
      } else if (tab === "tasks-assistant") {
        openDrawer(<AssistantRail />);
      }
    };
    window.addEventListener("cc-mobile-nav", handler);
    return () => window.removeEventListener("cc-mobile-nav", handler);
  }, [openDrawer, closeDrawer, openQuickCapture, selectView]);

  // Search opens from ⌘K and from the top bar's Search button, through ONE
  // guard. Not over another overlay (`searchAllowed`): the palette would open
  // hidden behind it and take the keystrokes.
  const openSearch = useCallback(() => {
    if (searchAllowed(useTaskStore.getState(), maximisedId !== null)) {
      setSearching(true);
    }
  }, [maximisedId]);

  // Two hotkeys from any Tasks view: ⌘K searches and `C` captures. ⌘K opens
  // the same palette it opens in Projects, so one key means one thing in both
  // task apps. It captured here until 2026-09-24. (App-wide capture from other
  // Metorite apps needs a persisted store + AppShell-level listener — see spec
  // §2.1 C2 [plumbing].)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (quickCaptureOpen || clarifyModalOpen) return; // a modal owns the keyboard
      const el = e.target as HTMLElement | null;
      const typing =
        !!el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.isContentEditable);
      if (isOpenShortcut(e)) {
        e.preventDefault();
        openSearch();
        return;
      }
      if (searching) return; // the palette owns the keyboard
      if (
        !typing &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey &&
        (e.key === "c" || e.key === "C")
      ) {
        e.preventDefault();
        openQuickCapture("single");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openQuickCapture, quickCaptureOpen, clarifyModalOpen, searching, openSearch]);

  // A hit My Tasks holds opens here. Any other task opens in Projects, because
  // the palette searches every project and My Tasks cannot draw a task it does
  // not hold (`lib/searchHit.ts`).
  const openHit = useCallback(
    (id: string) => {
      const target = hitTarget(id, useTaskStore.getState().items);
      if (target.kind === "here") openFocus(target.id);
      else router.push(target.href);
    },
    [openFocus, router],
  );
  const search = (
    <SearchPalette
      open={searching}
      onClose={() => setSearching(false)}
      onOpenTask={openHit}
    />
  );
  // `?` and the `g <letter>` jumps, the ones Projects binds. Held off while
  // any overlay owns the keyboard (`tasksOverlayOpen`, the twin of Projects'
  // `overlayOpen`), so neither fires under an open dialog.
  const overlayOpen = useTaskStore((s) =>
    tasksOverlayOpen(s, { searching, maximised: Boolean(maximisedId) }),
  );
  const shortcuts = <TasksShortcuts blocked={overlayOpen} />;

  if (isMobile) {
    // Single-pane mobile flow. Section switching + capture live in the AppShell
    // bottom bar; here we render just the current surface full-width. Tapping a
    // task opens the detail full-screen (TaskFocusModal, store-driven) — a
    // phone has no room for a docked column, which is the same move Projects
    // makes when it wraps its own `<aside>` in a `fixed inset-0` on mobile
    // (`projects/page.tsx`). Desktop docks the same detail beside the list.
    return (
      <div className="flex h-full w-full flex-col overflow-hidden bg-background">
        {/* The phone bar, as Projects draws it (`AppTopBar compact`). It
            holds the page's one h1 here too. Capture and the lists live in
            the shell's bottom bar, so this carries only search and the bell. */}
        <AppTopBar
          compact
          title="My Tasks"
          tools={
            <>
              <AppSearchButton onOpen={openSearch} />
              <NotificationBell onOpenTask={openHit} />
            </>
          }
        />
        {isInbox ? (
          <InboxView />
        ) : isEngage ? (
          <EngageView />
        ) : isLed ? (
          <LedProjectView />
        ) : (
          <ItemList />
        )}
        <QuickCapture />
        <TaskSettingsModal />
        <TaskFocusModal />
        <ReclarifyModal />
        <UndoToast />
        <SyncFailureToast />
        <PromoteToast />
        <PromoteHost />
        <DeleteConfirmModal />
        <SchedulePopup />
        <EliminatePopup />
        <DelegatePopup />
        {search}
        {shortcuts}
      </div>
    );
  }

  return (
    <div className="flex h-full w-full select-none flex-col overflow-hidden bg-background">
      {/* The shared app bar (`components/AppTopBar.tsx`), the one Projects
          renders: rail toggle, the app's h1, Capture, then search, the
          bell and the assistant at the right end. */}
      <AppTopBar
        rail={{ open: lists.open, onToggle: lists.toggle, noun: "your lists" }}
        title="My Tasks"
        actions={
          /* The same primitive and size as AssistantToggle, so the chips in
             this bar share one height and one radius. */
          <Button
            variant="secondary"
            size="sm"
            icon="Plus"
            onClick={() => openQuickCapture("single")}
          >
            Capture
            <kbd className="rounded border border-border px-1 text-[9px]">C</kbd>
          </Button>
        }
        tools={
          <>
            <AppSearchButton onOpen={openSearch} />
            <NotificationBell onOpenTask={openHit} />
            <AssistantToggle
              open={assistantOpen}
              onToggle={() => setAssistantOpen((v) => !v)}
            />
          </>
        }
      />

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* `railClass`: before mount, CSS hides the rail below `lg`, so a
            tablet load does not paint it open for a frame (`lib/railFold.ts`). */}
        {lists.open && (
          <aside className={`w-60 shrink-0 border-r border-border bg-card ${railClass(lists.settled)}`}>
            <ListsSidebar
              onOpenAssistant={() => setAssistantOpen(true)}
              assistantActive={assistantOpen}
            />
          </aside>
        )}

        {assistantOpen ? (
          /* Assistant as a full scene (email-app pattern) — replaces the main
             panes, keeps the left sidebar. Close via its header × or the
             toolbar toggle. */
          <div className="min-w-0 flex-1 overflow-hidden">
            <AssistantRail onClose={() => setAssistantOpen(false)} />
          </div>
        ) : isInbox ? (
          /* Inbox: a single capture-first surface — no list/detail split */
          <div className="min-w-0 flex-1 overflow-hidden border-r border-border">
            <InboxView />
          </div>
        ) : isEngage ? (
          /* Engage: the energy-first "right now" surface — its own full-width
             view (like the inbox), no list/detail split. */
          <div className="min-w-0 flex-1 overflow-hidden border-r border-border">
            <EngageView />
          </div>
        ) : isLed ? (
          /* S6e — a project I lead. Full width like the Inbox: the surface is
             a project, and its tasks open the same docked detail below. */
          <>
            <div className="min-w-0 flex-1 overflow-hidden border-r border-border">
              <LedProjectView />
            </div>
            {selectedItemId && (
              <aside className={`flex h-full w-full ${TASK_PANEL_WIDTH} shrink-0 flex-col overflow-hidden bg-card`}>
                <ItemDetail onMaximize={openMaximised} onClose={closeDetail} />
              </aside>
            )}
          </>
        ) : (
          /* Task views (Next/Waiting/Someday/…): list/board plus the docked
             detail column — the house layout (DESIGN_SYSTEM §6: content, then
             an optional side panel) and the same composition Projects uses,
             where `taskPanel` is the last child of the desktop row. Like
             Projects, the column is present only while something is selected,
             so the board keeps its full width when nothing is open. */
          <>
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden border-r border-border">
              <CaptureBar />
              <div className="min-h-0 flex-1">
                <ItemList />
              </div>
            </div>
            {selectedItemId && (
              /* `TASK_PANEL_WIDTH` is the docked width Projects' panel takes
                 too (`lib/taskPanel.ts`), so a task opens at one width in
                 both apps. No `border-l`: the list column left of it already
                 draws the divider, and two hairlines is a 2px rule. */
              <aside className={`flex h-full w-full ${TASK_PANEL_WIDTH} shrink-0 flex-col overflow-hidden bg-card`}>
                <ItemDetail onMaximize={openMaximised} onClose={closeDetail} />
              </aside>
            )}
          </>
        )}
      </div>

      <QuickCapture />
      <TaskSettingsModal />
      {/* Where a detail column is docked the overlay is CONTROLLED — it opens
          only from that pane's maximise. Everywhere else on desktop (Inbox,
          Engage, Assistant, and the /calendar app) it stays store-driven,
          because those
          surfaces call `openFocus` and have nowhere else to show a task. */}
      {paneDocked ? (
        <TaskFocusModal itemId={maximisedId} onClose={closeMaximised} />
      ) : (
        <TaskFocusModal />
      )}
      <ReclarifyModal />
      <UndoToast />
      <SyncFailureToast />
      <PromoteToast />
      <PromoteHost />
      <DeleteConfirmModal />
      <SchedulePopup />
      <EliminatePopup />
      <DelegatePopup />
      {search}
      {shortcuts}
    </div>
  );
}
