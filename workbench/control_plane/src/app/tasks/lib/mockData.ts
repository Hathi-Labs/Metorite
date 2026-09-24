// Mock My Tasks data for the UI-first build. Mirrors the demo-data pattern used by
// the email app (lib/mockData.ts): bundled sample data so the /tasks UI is
// fully explorable with no backend. When the gateway `/tasks` API lands, the
// store swaps these for live data; nothing else changes.

import { TaskContext, MyTask, MyTasksProject, Person, ProviderKind, Source } from "./types";

/** Where a clarified task can be stored — Local, or a connected PM tool.
 *  Stands in for the connected `task_accounts` (§4) until integrations land. */
export interface ConnectedProvider {
  id: string;
  label: string;
  provider: ProviderKind;
  source: Source;
  /** the tool's workflow stages/statuses (empty for Local). */
  statuses: string[];
}
export const CONNECTED_PROVIDERS: ConnectedProvider[] = [
  { id: "local", label: "Local", provider: "local", source: "LOCAL", statuses: [] },
  {
    id: "clickup",
    label: "ClickUp",
    provider: "clickup",
    source: "SYNCED",
    statuses: ["Backlog", "To-do", "In Process", "Review", "Done"],
  },
  {
    id: "jira",
    label: "Jira",
    provider: "jira",
    source: "SYNCED",
    statuses: ["Backlog", "Selected", "In Progress", "In Review", "Done"],
  },
];

/** Teammates available for delegation (the Waiting-For directory). */
export const MOCK_PEOPLE: Person[] = [
  { name: "Sai Kumar", email: "sai@fracktal.in", accent: "primary" },
  { name: "Priya", email: "priya@fracktal.in", accent: "accent" },
  { name: "Arjun", email: "arjun@fracktal.in", accent: "primary" },
  { name: "Meera", email: "meera@fracktal.in", accent: "accent" },
];

export const MOCK_CONTEXTS: TaskContext[] = [
  { name: "@computer", icon: "Monitor" },
  { name: "@calls", icon: "Phone" },
  { name: "@errands", icon: "Car" },
  { name: "@office", icon: "Building2" },
  { name: "@home", icon: "Home" },
  { name: "@agenda", icon: "Users" },
];

export const MOCK_PROJECTS: MyTasksProject[] = [
  {
    id: "p1",
    source: "SYNCED",
    provider: "clickup",
    outcome: "Ship the Quasar X1 firmware 2.0 release",
    purpose: "Unblock 40 customers waiting on the auto-calibration fix.",
    status: "ACTIVE",
    hasNextAction: true,
    areaId: "a-product",
  },
  {
    id: "p2",
    source: "SYNCED",
    provider: "clickup",
    outcome: "Close the Hyderabad lab fit-out",
    purpose: "New lab operational before the Q3 hiring wave.",
    status: "ACTIVE",
    hasNextAction: false, // ← cardinal GTD health-check failure: surfaced in review
    areaId: "a-ops",
  },
  {
    id: "p3",
    source: "LOCAL",
    provider: "local",
    outcome: "Plan parents' 40th anniversary trip",
    purpose: "Personal — just me organising it.",
    status: "ACTIVE",
    hasNextAction: true,
    areaId: "a-personal",
  },
  {
    id: "p4",
    source: "LOCAL",
    provider: "local",
    outcome: "Write the GTD task-manager blog post",
    status: "ACTIVE",
    hasNextAction: true,
    areaId: "a-personal",
  },
  {
    id: "p5",
    source: "SYNCED",
    provider: "clickup",
    outcome: "Launch Quasar in the EU market",
    purpose: "CE marking, distribution, and pricing ready before Q4.",
    status: "ACTIVE",
    hasNextAction: true,
    areaId: "a-product",
  },
  {
    id: "p6",
    source: "SYNCED",
    provider: "clickup",
    outcome: "Run the Q3 hiring wave",
    purpose: "Five engineers and an ops lead onboarded by September.",
    status: "ACTIVE",
    hasNextAction: true,
    areaId: "a-ops",
  },
  {
    id: "p7",
    source: "SYNCED",
    provider: "jira",
    outcome: "Stand up the customer support desk",
    purpose: "One place for warranty tickets and RMA tracking.",
    status: "ACTIVE",
    hasNextAction: true,
    areaId: "a-ops",
  },
  {
    id: "p8",
    source: "SYNCED",
    provider: "clickup",
    outcome: "Overhaul the print-farm reliability program",
    purpose: "Cut rig downtime — cooling, calibration, and spares.",
    status: "ACTIVE",
    hasNextAction: true,
    areaId: "a-product",
  },
];

// Mock timestamps are RELATIVE TO PAGE LOAD, not to a fixed calendar date: the
// fixtures below encode thresholds (4d delegated is just under the 5-day stale
// line; +1d is not yet overdue), and those only keep meaning if the data clock
// and the render clock are the same one. Every task surface reads the real
// wall-clock, so this anchor must too — a frozen anchor made w1 read "37d
// overdue" instead of the deliberately-clean contrast against w2.
const now = Date.now();
const iso = (offsetHours: number) => new Date(now + offsetHours * 3600_000).toISOString();

export const MOCK_ITEMS: MyTask[] = [
  // ── INBOX (unclarified captures) ─────────────────────────────────────────
  {
    id: "i0", source: "LOCAL", provider: "local",
    title: "Follow up on the anodizing vendor samples",
    disposition: "INBOX", isMine: true,
    createdAt: iso(-24 * 6), updatedAt: iso(-24 * 6), // ~a week old → aging signal
  },
  {
    id: "i1", source: "LOCAL", provider: "local",
    title: "Slack from Priya — reschedule the vendor review?",
    disposition: "INBOX", isMine: true,
    createdAt: iso(-2), updatedAt: iso(-2),
  },
  {
    id: "i2", source: "LOCAL", provider: "local",
    title: "Idea: add a 'snooze to next week' button to the inbox",
    disposition: "INBOX", isMine: true,
    createdAt: iso(-5), updatedAt: iso(-5),
  },
  {
    id: "i3", source: "LOCAL", provider: "local",
    title: "Receipt from the Hyderabad flight — file for expenses",
    disposition: "INBOX", isMine: true,
    createdAt: iso(-26), updatedAt: iso(-26),
  },
  // A fuller inbox — spans date buckets so filtering/scale is demonstrable.
  { id: "i4", source: "LOCAL", provider: "local", title: "Order replacement nozzles (0.4mm ×10)", disposition: "INBOX", isMine: true, createdAt: iso(-3), updatedAt: iso(-3) },
  { id: "i5", source: "LOCAL", provider: "local", title: "Ravi's LinkedIn message about a bulk order", disposition: "INBOX", isMine: true, createdAt: iso(-7), updatedAt: iso(-7) },
  { id: "i6", source: "LOCAL", provider: "local", title: "Book the annual GST filing appointment", disposition: "INBOX", isMine: true, createdAt: iso(-30), updatedAt: iso(-30) },
  { id: "i7", source: "LOCAL", provider: "local", title: "Note from standup: bed-leveling firmware regression", disposition: "INBOX", isMine: true, createdAt: iso(-30), updatedAt: iso(-30) },
  { id: "i8", source: "LOCAL", provider: "local", title: "Renew the AWS reserved instance before it lapses", disposition: "INBOX", isMine: true, createdAt: iso(-24 * 2), updatedAt: iso(-24 * 2) },
  { id: "i9", source: "LOCAL", provider: "local", title: "Idea: a 'someday' triage shortcut in the inbox", disposition: "INBOX", isMine: true, createdAt: iso(-24 * 2 - 5), updatedAt: iso(-24 * 2 - 5) },
  { id: "i10", source: "LOCAL", provider: "local", title: "Reply to the intern's onboarding questions", disposition: "INBOX", isMine: true, createdAt: iso(-24 * 3), updatedAt: iso(-24 * 3) },
  { id: "i11", source: "LOCAL", provider: "local", title: "Chase the courier about the delayed spindle", disposition: "INBOX", isMine: true, createdAt: iso(-24 * 4), updatedAt: iso(-24 * 4) },
  { id: "i12", source: "LOCAL", provider: "local", title: "Draft the Q3 OKRs for the firmware team", disposition: "INBOX", isMine: true, createdAt: iso(-24 * 5), updatedAt: iso(-24 * 5) },
  { id: "i13", source: "LOCAL", provider: "local", title: "Water-cooling loop is leaking on rig 2 — investigate", disposition: "INBOX", isMine: true, createdAt: iso(-24 * 9), updatedAt: iso(-24 * 9) },
  { id: "i14", source: "LOCAL", provider: "local", title: "Research CE marking for the EU launch", disposition: "INBOX", isMine: true, createdAt: iso(-24 * 12), updatedAt: iso(-24 * 12) },
  { id: "i15", source: "LOCAL", provider: "local", title: "Someday: write up the lab safety handbook", disposition: "INBOX", isMine: true, createdAt: iso(-24 * 18), updatedAt: iso(-24 * 18) },
  // Tickled (deferred) — hidden from the active inbox until they resurface.
  { id: "t1", source: "LOCAL", provider: "local", title: "Re-check the trademark filing status", disposition: "INBOX", isMine: true, createdAt: iso(-24 * 4), updatedAt: iso(-24 * 4), deferUntil: iso(24 * 4) },
  { id: "t2", source: "LOCAL", provider: "local", title: "Follow up with the accountant after month-end", disposition: "INBOX", isMine: true, createdAt: iso(-24 * 2), updatedAt: iso(-24 * 2), deferUntil: iso(24 * 10) },

  // ── NEXT ACTIONS (by @context) ───────────────────────────────────────────
  {
    id: "n1", source: "SYNCED", provider: "clickup",
    title: "Review the calibration PR #214",
    nextAction: "Read the diff and leave review comments on PR #214",
    disposition: "NEXT", context: "@computer", energy: "high",
    timeEstimateMins: 45, projectId: "p1", isMine: true,
    important: true, deepWork: true,
    createdAt: iso(-30), updatedAt: iso(-4),
  },
  {
    id: "n2", source: "SYNCED", provider: "clickup",
    title: "Reply to the contractor's lab-quote email",
    nextAction: "Draft a reply approving option B and asking for the timeline",
    disposition: "NEXT", context: "@computer", energy: "low",
    timeEstimateMins: 10, projectId: "p2", isMine: true,
    important: true,
    createdAt: iso(-48), updatedAt: iso(-6),
  },
  {
    id: "n3", source: "LOCAL", provider: "local",
    title: "Call the travel agent about anniversary trip dates",
    nextAction: "Call Meera and confirm the Coorg dates for the trip",
    disposition: "NEXT", context: "@calls", energy: "low",
    timeEstimateMins: 10, projectId: "p3", isMine: true,
    isTwoMinute: false, important: true, dueAt: iso(20),
    createdAt: iso(-20), updatedAt: iso(-20),
  },
  {
    id: "n4", source: "LOCAL", provider: "local",
    title: "Pick up the soldering tips from the electronics store",
    nextAction: "Buy 3× T18-D24 soldering tips on the way home",
    disposition: "NEXT", context: "@errands", energy: "low",
    timeEstimateMins: 20, isMine: true,
    createdAt: iso(-72), updatedAt: iso(-72),
  },
  {
    id: "n5", source: "LOCAL", provider: "local",
    title: "Outline the GTD blog post",
    nextAction: "Sketch the 5-step section headings in the draft doc",
    disposition: "NEXT", context: "@computer", energy: "medium",
    timeEstimateMins: 30, projectId: "p4", isMine: true,
    leveraged: true, deepWork: true,
    createdAt: iso(-10), updatedAt: iso(-10),
  },
  {
    id: "n6", source: "SYNCED", provider: "clickup",
    title: "Agenda: discuss Q3 hiring plan with Arjun",
    nextAction: "Raise the 2 firmware hires at the next 1:1 with Arjun",
    disposition: "NEXT", context: "@agenda", energy: "low",
    isMine: true,
    createdAt: iso(-15), updatedAt: iso(-15),
  },

  // ── WAITING FOR (delegated / blocked on others) ──────────────────────────
  {
    id: "w1", source: "SYNCED", provider: "clickup",
    title: "Firmware QA sign-off on build 2.0-rc3",
    disposition: "WAITING", projectId: "p1", isMine: false,
    waitingOn: { name: "Sai Kumar", email: "sai@fracktal.in", accent: "primary" },
    delegatedAt: iso(-96),
    // The date it was promised BY — what the Waiting-For view flags on. Every
    // server write site (items/capture_email/sync) seeds it from the due date.
    expectedBy: iso(24),
    dueAt: iso(24),
    createdAt: iso(-96), updatedAt: iso(-50),
  },
  {
    id: "w2", source: "SYNCED", provider: "clickup",
    title: "Contractor to send the revised lab fit-out quote",
    disposition: "WAITING", projectId: "p2", isMine: false,
    waitingOn: { name: "BuildRight Co.", accent: "accent" },
    delegatedAt: iso(-168),
    expectedBy: iso(-24),
    dueAt: iso(-24), // overdue → flagged in the UI
    createdAt: iso(-168), updatedAt: iso(-168),
  },

  // ── CALENDAR (hard-date actions — a VIEW, not a bucket) ───────────────────
  {
    id: "c1", source: "SYNCED", provider: "clickup",
    title: "Quasar X1 2.0 go/no-go release call",
    nextAction: "Join the release call and make the ship decision",
    disposition: "NEXT", context: "@calls", energy: "high",
    projectId: "p1", isMine: true, important: true,
    dueAt: iso(31), isHardDate: true,
    createdAt: iso(-40), updatedAt: iso(-40),
  },
  {
    id: "c2", source: "LOCAL", provider: "local",
    title: "Dentist appointment",
    disposition: "NEXT", context: "@errands",
    isMine: true,
    dueAt: iso(54), isHardDate: true,
    createdAt: iso(-200), updatedAt: iso(-200),
  },

  // ── SOMEDAY / MAYBE (incubated) ──────────────────────────────────────────
  {
    id: "s1", source: "LOCAL", provider: "local",
    title: "Learn KiCad properly",
    disposition: "SOMEDAY", isMine: true,
    createdAt: iso(-500), updatedAt: iso(-500),
  },
  {
    id: "s2", source: "LOCAL", provider: "local",
    title: "Evaluate moving the lab to a bigger space next year",
    disposition: "SOMEDAY", isMine: true,
    createdAt: iso(-400), updatedAt: iso(-400),
  },
];

/** A curated subset of David Allen's Incompletion Trigger List — memory-joggers
 *  shown during a mind sweep to help pull open loops out of your head. */
export const CAPTURE_TRIGGERS: string[] = [
  "Projects started, not finished",
  "Projects to start",
  "Promises to others",
  "Calls to make",
  "Emails to send",
  "Decisions to make",
  "Waiting-for / follow-ups",
  "Errands & home",
  "Finances",
  "Health",
];

/** Quick-action pills for the assistant rail (wired in a later slice). */
export const QUICK_ACTIONS: { label: string; prompt: string }[] = [
  { label: "Process my inbox", prompt: "Help me clarify my inbox items one by one." },
  { label: "What's my next action?", prompt: "Given my contexts and energy, what should I do next?" },
  { label: "Run weekly review", prompt: "Walk me through my weekly review." },
  { label: "What am I waiting on?", prompt: "Show me everything I'm waiting on and what's overdue." },
];
