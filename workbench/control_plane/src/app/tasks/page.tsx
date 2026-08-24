"use client";

import { useCallback, useEffect, useState } from "react";
import Icon from "@/components/Icon";
import { useViewMode } from "@/components/ViewModeProvider";
import { useMobileDrawer } from "@/components/AppShell";
import { useTaskStore } from "./lib/taskStore";
import { ListsSidebar } from "./components/ListsSidebar";
import { CaptureBar } from "./components/CaptureBar";
import { ItemList } from "./components/ItemList";
import { ItemDetail } from "./components/ItemDetail";
import { AssistantRail } from "./components/AssistantRail";
import { InboxView } from "./components/InboxView";
import { EngageView } from "./components/EngageView";
import { QuickCapture } from "./components/QuickCapture";
import { WorkspacesModal } from "./components/WorkspacesModal";
import { TaskSettingsModal } from "./components/TaskSettingsModal";
import { TaskFocusModal } from "./components/TaskFocusModal";
import { ReclarifyModal } from "./components/ReclarifyModal";
import { UndoToast } from "./components/UndoToast";
import { DeleteConfirmModal } from "./components/DeleteConfirmModal";
import { SchedulePopup } from "./components/SchedulePopup";
import { EliminatePopup } from "./components/EliminatePopup";
import { DelegatePopup } from "./components/DelegatePopup";

// Task Manager (GTD) — 4-panel shell, mirroring the email app's layout
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
  const [leftOpen, setLeftOpen] = useState(true);
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

  // Tell the mobile bottom bar which GTD section is active (for its highlight).
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
        openDrawer(<ListsSidebar onNavigate={closeDrawer} />);
      } else if (tab === "tasks-capture") {
        openQuickCapture("single");
      } else if (tab === "tasks-assistant") {
        openDrawer(<AssistantRail />);
      }
    };
    window.addEventListener("cc-mobile-nav", handler);
    return () => window.removeEventListener("cc-mobile-nav", handler);
  }, [openDrawer, closeDrawer, openQuickCapture, selectView]);

  // Ubiquitous capture — a hotkey opens the capture palette from any Tasks view.
  // (App-wide capture from other Metorite apps needs a persisted store +
  // AppShell-level listener — see spec §2.1 C2 [plumbing].)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (quickCaptureOpen || clarifyModalOpen) return; // a modal owns the keyboard
      const el = e.target as HTMLElement | null;
      const typing =
        !!el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.isContentEditable);
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        openQuickCapture("single");
        return;
      }
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
  }, [openQuickCapture, quickCaptureOpen, clarifyModalOpen]);

  if (isMobile) {
    // Single-pane mobile flow. Section switching + capture live in the AppShell
    // bottom bar; here we render just the current surface full-width. Tapping a
    // task opens the detail full-screen (TaskFocusModal, store-driven) — a
    // phone has no room for a docked column, which is the same move Projects
    // makes when it wraps its own `<aside>` in a `fixed inset-0` on mobile
    // (`projects/page.tsx`). Desktop docks the same detail beside the list.
    return (
      <div className="flex h-full w-full flex-col overflow-hidden bg-background">
        {isInbox ? (
          <InboxView />
        ) : isEngage ? (
          <EngageView />
        ) : (
          <ItemList />
        )}
        <QuickCapture />
        <WorkspacesModal />
        <TaskSettingsModal />
        <TaskFocusModal />
        <ReclarifyModal />
        <UndoToast />
        <DeleteConfirmModal />
        <SchedulePopup />
        <EliminatePopup />
      <DelegatePopup />
      </div>
    );
  }

  return (
    <div className="flex h-full w-full select-none flex-col overflow-hidden bg-background">
      {/* Slim toolbar: panel toggles + title */}
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-card px-2">
        <PanelToggle
          active={leftOpen}
          onClick={() => setLeftOpen((v) => !v)}
          label="Toggle lists"
          icon="PanelLeft"
        />
        <span className="text-xs font-medium text-muted-foreground">
          Task Manager
        </span>
        <button
          type="button"
          onClick={() => openQuickCapture("single")}
          className="tech-transition ml-2 inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
        >
          <Icon name="Plus" className="h-3.5 w-3.5" />
          Capture
          <kbd className="rounded border border-border px-1 text-[9px]">C</kbd>
        </button>
        <button
          type="button"
          onClick={() => setAssistantOpen((v) => !v)}
          aria-pressed={assistantOpen}
          title="Assistant"
          className={[
            "tech-transition ml-auto inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs",
            assistantOpen
              ? "border-primary/40 bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
          ].join(" ")}
        >
          <Icon name="Sparkles" className="h-3.5 w-3.5" />
          Assistant
        </button>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {leftOpen && (
          <aside className="w-60 shrink-0 border-r border-border bg-card">
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
              /* `w-[380px]` is DESIGN_SYSTEM §6's side-panel width, not
                 Projects' `max-w-md`: Projects writes `w-full max-w-md` because
                 the same <aside> becomes the phone screen with the cap lifted,
                 and this one never does — the phone gets TaskFocusModal. No
                 `border-l`: the list column left of it already draws the
                 divider, and two hairlines is a 2px rule. */
              <aside className="flex h-full w-[380px] shrink-0 flex-col overflow-hidden bg-card">
                <ItemDetail onMaximize={openMaximised} onClose={closeDetail} />
              </aside>
            )}
          </>
        )}
      </div>

      <QuickCapture />
      <WorkspacesModal />
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
      <DeleteConfirmModal />
      <SchedulePopup />
      <EliminatePopup />
      <DelegatePopup />
    </div>
  );
}

function PanelToggle({
  active,
  onClick,
  label,
  icon,
  className = "",
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  /** Lucide icon NAME. */
  icon: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      className={[
        "tech-transition flex h-7 w-7 items-center justify-center rounded-md",
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:bg-secondary hover:text-foreground",
        className,
      ].join(" ")}
    >
      <Icon name={icon} className="h-4 w-4" />
    </button>
  );
}
