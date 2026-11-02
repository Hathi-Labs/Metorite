/**
 * The UI gate: four interaction rules a regex can see (`DESIGN_SYSTEM.md` §11).
 *
 * Owner, 2026-10-27: the UI must be simple and immersive, and it must work as
 * a person intends. Every UI change passes through gates, and a gate blocks in
 * build mode. Today's violations are counted and kept. A new one fails here.
 *
 *   G5  `outline-none` with no focus ring on the same line
 *   G6  a drag with no keyboard path in the same file (M6)
 *   G7  `h-screen`, `min-h-screen` or `100vh` (M12)
 *   G8  an `<img>` that reserves no space (M1)
 *
 * The motion rules G1 to G4 are in `theme/motion.test.ts`. The ratchet is
 * `theme/uiRatchet.ts`: a file with no budget is clean, a budget never rises,
 * and a file that got better lowers its number in the same change.
 */
import { describe, expect, it } from "vitest";

import { countMatches, holdRatchet, readSource, uiSources } from "./theme/uiRatchet";

const SOURCES = uiSources();
const CODE = SOURCES.filter((f) => /\.tsx?$/.test(f));

// ── G5: a focus ring replaces the outline ───────────────────────────────────

const OUTLINE_NONE = /(?<![\w-])outline-none(?![\w-])/;
/** A ring that a keyboard member sees. `cc-control` and `cc-link` draw it in globals.css. */
const FOCUS_RING = /focus(?:-visible|-within)?:(?:ring|outline-(?!none))|(?<![\w-])cc-(?:control|link)(?![\w-])/;

/** The lines that take the outline away and put no ring in its place. */
function bareOutlines(file: string): number {
  return readSource(file)
    .split("\n")
    .filter((line) => OUTLINE_NONE.test(line) && !FOCUS_RING.test(line)).length;
}

/** Measured 2026-10-27. Each number may only fall. */
const OUTLINE_DEBT: Record<string, number> = {
  "app/agents/page.tsx": 8,
  "app/build/apps/[slug]/edit/page.tsx": 2,
  "app/studio/components/AppsGallery.tsx": 1,
  "app/calendar/components/CalendarSettings.tsx": 5,
  "app/calendar/components/PlanDayPanel.tsx": 3,
  "app/chat/page.tsx": 1,
  "app/email/components/ComposePanel.tsx": 4,
  "app/email/components/ConversationView.tsx": 1,
  "app/email/components/DraftAssistant.tsx": 1,
  "app/email/components/EmailDetail.tsx": 4,
  "app/email/components/EmailList.tsx": 1,
  "app/email/components/MessageActions.tsx": 1,
  "app/email/components/SearchBar.tsx": 4,
  "app/email/components/SignatureEditor.tsx": 2,
  "app/email/components/TaskCaptureModal.tsx": 8,
  "app/email/components/automation/BulkUnsubscribeView.tsx": 1,
  "app/email/components/automation/DigestSettingsDialog.tsx": 1,
  "app/email/components/automation/ai-settings/RulesTab.tsx": 1,
  "app/email/components/automation/ai-settings/common.tsx": 1,
  "app/integrations/components/ApiKeysView.tsx": 3,
  "app/integrations/components/PluginsView.tsx": 1,
  "app/memory/page.tsx": 1,
  "app/notes/components/MeetingPrep.tsx": 7,
  "app/notes/components/NotesSettingsModal.tsx": 4,
  "app/people/components/PersonEditor.tsx": 2,
  "app/projects/components/CardInput.tsx": 1,
  "app/projects/components/TableView.tsx": 1,
  "app/projects/components/TaskBoard.tsx": 1,
  "app/projects/components/TaskList.tsx": 1,
  "app/settings/groups/page.tsx": 6,
  "app/settings/members/[email]/page.tsx": 1,
  "app/settings/organization/OrganizationAdmin.tsx": 5,
  "app/settings/roles/page.tsx": 2,
  "app/tasks/components/AttachmentComposer.tsx": 1,
  "app/tasks/components/CaptureBar.tsx": 1,
  "app/tasks/components/ClarifyPanel.tsx": 10,
  "app/tasks/components/DelegateDialog.tsx": 1,
  "app/tasks/components/DelegatePopup.tsx": 1,
  "app/tasks/components/EngageView.tsx": 2,
  "app/tasks/components/FlatList.tsx": 1,
  "app/tasks/components/InboxCard.tsx": 3,
  "app/tasks/components/InboxView.tsx": 3,
  "app/tasks/components/ItemDetail.tsx": 5,
  "app/tasks/components/QuickCapture.tsx": 4,
  "app/tasks/components/SchedulePopup.tsx": 1,
  "app/tasks/components/TaskBoard.tsx": 1,
  "app/tasks/components/TaskListGrouped.tsx": 1,
  "app/tasks/components/TaskSettingsModal.tsx": 1,
  "app/tasks/components/WaitingForView.tsx": 1,
  "app/whatsapp/connect/page.tsx": 3,
  "app/whatsapp/page.tsx": 1,
  "app/whatsapp/settings/categories/page.tsx": 1,
  "app/whatsapp/settings/replies/page.tsx": 3,
  "app/studio/workflows/[id]/page.tsx": 1,
  "app/workflows/components/ModuleStudio.tsx": 1,
  "components/AddAgentWizard.tsx": 1,
  "components/AgentChat.tsx": 2,
  "components/DocumentPane.tsx": 1,
  "components/IntegrationSetup.tsx": 1,
  "components/MessageBubble.tsx": 2,
  "components/SidePanelEditor.tsx": 1,
  "components/TaskPanelHeader.tsx": 1,
  "components/room/ShareSheet.tsx": 2,
  "lib/shell/ActivityControl.tsx": 1,
  "lib/shell/AssistantDock.tsx": 1,
  "lib/shell/ChatBar.tsx": 1,
  "lib/shell/CommandBar.tsx": 1,
  "lib/shell/HomeCard.tsx": 1,
};

// ── G6: every drag has a keyboard path (M6) ─────────────────────────────────

/** A stop that only keeps a drag from starting, as a link inside a card does. */
const STOP_ONLY = String.raw`\s*\(?\s*\w*(?:\s*:\s*[\w.<>]+)?\s*\)?\s*=>\s*(?:\{\s*)?(?:\w+\.(?:stopPropagation|preventDefault)\(\)\s*;?\s*)+[},]`;

/**
 * A drag source: a JSX `draggable` that is not false, or an `onDragStart`
 * that does more than stop. A props spread (`onDragStart: (e) => …`) counts.
 */
const DRAG = new RegExp(
  String.raw`(?<![\w.-])draggable(?:=(?!\{\s*false\s*\}|["']false["'])|(?=\s+[\w{/>]|\s*\/?>))|\bonDragStart(?:=\{|\s*:\s*)(?!${STOP_ONLY})`,
);

/** A keyboard path: a "Move to" item, named shortcuts, or Alt with an arrow key. */
const KEY_PATH =
  /\bMove to\b|aria-keyshortcuts|\baltKey\b[\s\S]{0,300}\bArrow(?:Up|Down|Left|Right)\b|\bArrow(?:Up|Down|Left|Right)\b[\s\S]{0,300}\baltKey\b/;

/** Today's drags with no keyboard path. A file leaves when it gets one. */
const DRAG_DEBT: Record<string, number> = {
  "app/calendar/components/UnscheduledRail.tsx": 1,
  "app/crm/components/FieldStudio.tsx": 1,
  "app/crm/components/FieldStudioOptions.tsx": 1,
  "app/crm/components/KanbanBoard.tsx": 1,
  "app/crm/components/PipelineSettings.tsx": 1,
  "app/knowledge/components/KnowledgeRail.tsx": 1,
  "app/people/chart/page.tsx": 1,
  "app/projects/components/CalendarView.tsx": 1,
  "app/projects/components/ProjectTree.tsx": 1,
  "app/projects/components/TaskBoard.tsx": 1,
  "app/tasks/components/TaskBoard.tsx": 1,
  "app/tasks/components/TaskCard.tsx": 1,
  "app/tasks/components/TaskListGrouped.tsx": 1,
  "app/workflows/components/NodePalette.tsx": 1,
  "components/TaskCardShell.tsx": 1,
};

const dragWithoutKeys = (file: string) => {
  const text = readSource(file);
  return DRAG.test(text) && !KEY_PATH.test(text) ? 1 : 0;
};

// ── G7: a full height follows the phone's browser bar (M12) ─────────────────

const SCREEN_HEIGHT = /(?<![\w-])(?:min-|max-)?h-screen(?![\w-])|(?<![\w.-])100vh\b/g;

const SCREEN_HEIGHT_DEBT: Record<string, number> = {
  "app/email/oauth/callback/page.tsx": 2,
  "app/knowledge/components/PageView.tsx": 1,
  "app/memory/page.tsx": 1,
  "app/oauth/approved/ApprovedCard.tsx": 1,
  "app/signin/SignInForm.tsx": 2,
  "app/signin/code/CodeForm.tsx": 2,
  "app/signup/SignUpForm.tsx": 1,
  "app/signup/page.tsx": 1,
  "components/AccountSwitcher.tsx": 1,
  "components/AppShell.tsx": 1,
};

// ── G8: an image reserves its space (M1) ────────────────────────────────────

/** An `<img>` with no width, no height, no aspect ratio and no height class. */
const SIZELESS_IMG =
  /<img\b(?!(?:=>|[^>])*?(?:\b(?:width|height)\s*[=:]|aspect-|(?<![\w-])(?:h|size)-(?:\d|\[|full)))/g;

const SIZELESS_IMG_DEBT: Record<string, number> = {
  "app/artifacts/page.tsx": 1,
  "app/notes/meeting/[id]/page.tsx": 1,
  "app/observability/office-topdown.tsx": 6,
  "app/people/components/Avatar.tsx": 1,
  "app/projects/components/AttachmentViewer.tsx": 1,
  "app/whatsapp/page.tsx": 1,
  "components/ArtifactCard.tsx": 1,
  "components/ArtifactViewerModal.tsx": 1,
  "components/DocumentPane.tsx": 1,
};

const each = (files: string[], n: (file: string) => number) => Object.fromEntries(files.map((f) => [f, n(f)]));

describe("the UI gate: interaction rules (DESIGN_SYSTEM.md §11)", () => {
  it("reads the real tree", () => {
    expect(CODE.length).toBeGreaterThan(100);
    expect(CODE).toContain("components/AppShell.tsx");
  });

  holdRatchet({
    rule: "G5 outline-none with no focus ring on the same line",
    fix: "Put focus-visible:ring-2 focus-visible:ring-ring beside it, or use a control from components/ui/.",
    measure: () => each(CODE.filter((f) => !f.startsWith("components/ui/")), bareOutlines),
    budget: OUTLINE_DEBT,
  });

  holdRatchet({
    rule: "G6 a drag with no keyboard path in the same file (M6)",
    fix: 'Give the same move a "Move to" menu item, or Alt+Arrow keys named in aria-keyshortcuts.',
    measure: () => each(CODE, dragWithoutKeys),
    budget: DRAG_DEBT,
  });

  holdRatchet({
    rule: "G7 h-screen, min-h-screen or 100vh (M12)",
    fix: "Use h-dvh or min-h-dvh, so a phone's browser bar does not cover the bottom.",
    measure: () => each(SOURCES, (f) => countMatches(readSource(f), SCREEN_HEIGHT)),
    budget: SCREEN_HEIGHT_DEBT,
  });

  holdRatchet({
    rule: "G8 an <img> that reserves no space (M1)",
    fix: "Give it width and height, an aspect-* class, or a size-* or h-* class, so nothing moves when it loads.",
    measure: () => each(CODE.filter((f) => f.endsWith(".tsx")), (f) => countMatches(readSource(f), SIZELESS_IMG)),
    budget: SIZELESS_IMG_DEBT,
  });

  it("G6 sees a drag, a stop and a keyboard path the way it should", () => {
    expect(DRAG.test('<div draggable onDragEnd={end}>')).toBe(true);
    expect(DRAG.test("<div draggable={canMove}>")).toBe(true);
    expect(DRAG.test("onDragStart: (event: React.DragEvent) => { setDrag(id); }")).toBe(true);
    expect(DRAG.test("<a draggable={false} onDragStart={(event) => event.stopPropagation()}>")).toBe(false);
    expect(DRAG.test("draggable?: boolean;")).toBe(false);
    expect(KEY_PATH.test('label: "Move to…"')).toBe(true);
    expect(KEY_PATH.test("if (e.altKey && e.key === 'ArrowUp')")).toBe(true);
    expect(KEY_PATH.test("onKeyDown={(e) => e.key === 'Escape' && close()}")).toBe(false);
  });
});
