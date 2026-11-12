/**
 * The words of the "How to connect" panel (WS-CAL CE-10c, task 66).
 *
 * ONE module holds them, so the panel and any other page that quotes them never drift.
 * The long guide is `docs/calendar_connect.md` (CE-14). This module keeps its steps short.
 * Docs are not served by the app, so the path shows as plain text.
 *
 * Fence: `connectGuide.test.ts` (four non-empty lists, under 20 words a sentence, no semicolon).
 */

export interface GuideList {
  id: "google-personal" | "google-workspace" | "outlook-personal" | "microsoft-365";
  title: string;
  steps: string[];
}

export const GOOGLE_ADMIN_PATH =
  "Google Admin console, Security, Access and data control, API controls, Manage third-party app access, allow Metorite.";

export const ENTRA_ADMIN_PATH =
  "Entra admin centre, Enterprise applications, Metorite, Permissions, Grant admin consent.";

/** The one line a member copies to the administrator of a Microsoft 365 tenant. */
export const ADMIN_NOTE =
  "Please approve the Metorite app for these delegated permissions: Calendars.ReadWrite, User.Read, offline_access.";

export const GUIDE_LISTS: GuideList[] = [
  {
    id: "google-personal",
    title: "Google, personal account",
    steps: [
      "Press Connect Google Calendar.",
      "Choose your Google account.",
      "Read the permissions. Keep every box on, then press Continue.",
      "If Google says it has not verified the app, press Advanced, then Go to Metorite.",
      "Wait for the first sync. Your events appear in about a minute.",
    ],
  },
  {
    id: "google-workspace",
    title: "Google Workspace, company account",
    steps: [
      "Press Connect Google Calendar and choose your work account.",
      "If Google says Access blocked, your administrator must allow Metorite first.",
      "Send your administrator the path below.",
      GOOGLE_ADMIN_PATH,
      "When your administrator says it is done, press Connect Google Calendar again.",
    ],
  },
  {
    id: "outlook-personal",
    title: "Outlook, personal account (outlook.com, hotmail)",
    steps: [
      "Press Connect Outlook.",
      "Choose your Microsoft account.",
      "Press Accept on the permission page.",
      "Wait for the first sync. Your events appear in about a minute.",
    ],
  },
  {
    id: "microsoft-365",
    title: "Microsoft 365, work or school account",
    steps: [
      "Press Connect Outlook and choose your work or school account.",
      "If Microsoft says Need admin approval, your administrator must approve Metorite first.",
      "Send your administrator the note below.",
      ENTRA_ADMIN_PATH,
      "When your administrator says it is done, press Connect Outlook again.",
    ],
  },
];

export const SYNC_TITLE = "What syncs which way";

export const SYNC_NOTES: string[] = [
  "Google and Outlook events come in read only. They arrive within about a minute.",
  "Metorite tasks with a time go out to a calendar named Metorite.",
  "Move one of those events there, and the task moves here.",
  "Disconnect removes the copied events. Your tasks stay.",
];

export const FULL_GUIDE_PATH = "docs/calendar_connect.md";
export const FULL_GUIDE_LINE = `Full guide: ${FULL_GUIDE_PATH}`;

/** Every sentence the panel shows, for the fence. */
export function guideSentences(): string[] {
  const all = [
    ...GUIDE_LISTS.flatMap((l) => l.steps),
    ...SYNC_NOTES,
    ADMIN_NOTE,
  ];
  return all.flatMap((s) => s.split(/[.!?]\s+|[.!?]$/).map((p) => p.trim()).filter(Boolean));
}
