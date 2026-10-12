// ── The setup hub (WS-53 CRM-X0, spec crm_platform.md §19.11) ─────────────
//
// The first card of CRM settings is the ONE place where the setup changes.
// It shows the mode, the connection, the last sync and the record counts, and
// it offers only the actions that fit the state (§19.11 item 1). This file
// holds the states, the actions and their words, so they have tests. The card
// that draws them is `components/SetupHub.tsx`.
//
// It replaces the setup card of CRM-U5b. That card refused a CRM with
// records and gave no way forward. The hub offers a reconnect that keeps the
// records, and a start over that removes them after a download.

import type { ConnectionInfo, CrmMode } from "./connect";

export const SETUP_ANCHOR = "crm-setup";

/** Where the banner and the title-bar line send an admin. */
export const SETUP_HREF = "/crm?tab=settings";

/** The rows of the table in §19.11 item 1, and the case of another account. */
export type HubState =
  | "none"
  | "mirror-connected"
  | "mirror-disconnected"
  | "mirror-needs-reset"
  | "native";

export type HubActionId =
  | "use-metorite"
  | "connect-zoho"
  | "sync-now"
  | "disconnect"
  | "reconnect-zoho"
  | "switch-to-metorite"
  | "start-over"
  | "start-over-new-account";

/** What the start over sets after the records go. */
export type ResetThen = "none" | "native" | "mirror";

/**
 * One action of the hub.
 *
 * `dialog` set: the action removes every record, so it opens the start-over
 * dialog, which sends `then`. No `dialog`: the action runs at once.
 */
export type HubAction = {
  id: HubActionId;
  label: string;
  /** One sentence: what the action does (§19.11 item 7). */
  explain: string;
  dialog?: { then: ResetThen; title: string };
};

export const ACTIONS: Record<HubActionId, HubAction> = {
  "use-metorite": {
    id: "use-metorite",
    label: "Use Metorite",
    explain: "Metorite becomes your CRM, with pipeline stages that you can change.",
  },
  "connect-zoho": {
    id: "connect-zoho",
    label: "Connect Zoho",
    explain: "Metorite shows your Zoho CRM records here and keeps them up to date.",
  },
  "sync-now": {
    id: "sync-now",
    label: "Sync now",
    explain: "Metorite reads the latest changes from Zoho now.",
  },
  disconnect: {
    id: "disconnect",
    label: "Disconnect",
    explain: "The sync stops, and the records stay here but stop updating.",
  },
  "reconnect-zoho": {
    id: "reconnect-zoho",
    label: "Reconnect Zoho (keeps the records)",
    explain:
      "Connect the same Zoho account again, so the records stay and the sync starts again.",
  },
  "switch-to-metorite": {
    id: "switch-to-metorite",
    label: "Switch to Metorite",
    explain: "Every record goes, and Metorite becomes your CRM with new pipeline stages.",
    dialog: { then: "native", title: "Switch to Metorite" },
  },
  "start-over": {
    id: "start-over",
    label: "Start over",
    explain: "Every record goes, and the setup goes too, so you can choose again.",
    dialog: { then: "none", title: "Start over" },
  },
  "start-over-new-account": {
    id: "start-over-new-account",
    label: "Start over with the new account",
    explain:
      "Every record of the first account goes, then Zoho asks you to connect the new account.",
    dialog: { then: "mirror", title: "Start over with the new Zoho account" },
  },
};

/** Connect Zoho from Native mode: the records go first (§19.11 item 5). */
export const CONNECT_FROM_NATIVE: HubAction = {
  id: "connect-zoho",
  label: "Connect Zoho",
  explain:
    "Every record goes, then Zoho asks you to connect, and Metorite imports your Zoho records.",
  dialog: { then: "mirror", title: "Connect Zoho" },
};

/** The state of the hub, from the mode and the connection. */
export function hubState(mode: CrmMode, connection: ConnectionInfo | null): HubState {
  if (mode === "native") return "native";
  if (mode !== "mirror") return "none";
  if (connection?.status === "connected") return "mirror-connected";
  if (connection?.status === "needs_reset") return "mirror-needs-reset";
  return "mirror-disconnected";
}

export type HubPlan = {
  /** The main actions: one, two for the first choice, none in Native mode. */
  main: HubAction[];
  other: HubAction[];
};

/**
 * The actions of each state, as the table of §19.11 item 1 lists them.
 *
 * `mirrorOn` is false when the gateway answers 404 for the mirror routes.
 * Then the hub offers no action that needs Zoho.
 */
export function hubActions(state: HubState, mirrorOn: boolean): HubPlan {
  const zoho = (action: HubAction): HubAction[] => (mirrorOn ? [action] : []);
  switch (state) {
    case "none":
      return { main: [ACTIONS["use-metorite"], ...zoho(ACTIONS["connect-zoho"])], other: [] };
    case "mirror-connected":
      return {
        main: [ACTIONS["sync-now"]],
        other: [ACTIONS.disconnect, ACTIONS["switch-to-metorite"], ACTIONS["start-over"]],
      };
    case "mirror-disconnected":
      return {
        main: zoho(ACTIONS["reconnect-zoho"]),
        other: [ACTIONS["switch-to-metorite"], ACTIONS["start-over"]],
      };
    case "mirror-needs-reset":
      return {
        main: zoho(ACTIONS["start-over-new-account"]),
        other: [
          ...zoho(ACTIONS["reconnect-zoho"]),
          ACTIONS["switch-to-metorite"],
          ACTIONS["start-over"],
        ],
      };
    case "native":
      return { main: [], other: [...zoho(CONNECT_FROM_NATIVE), ACTIONS["start-over"]] };
  }
}

/** The mode, as the hub says it. */
export function modeLabel(mode: CrmMode): string {
  if (mode === "native") return "Metorite is your CRM";
  if (mode === "mirror") return "Synced from Zoho";
  return "Not set up";
}

/** The connection, as the hub says it. */
export function connectionLabel(state: HubState, connection: ConnectionInfo | null): string {
  if (state === "native" || state === "none") return "No Zoho connection";
  switch (connection?.status) {
    case "connected":
      return "Connected";
    case "needs_reconnect":
      return "Zoho needs you to connect again";
    case "needs_reset":
      return "Another Zoho account. Nothing syncs until you choose";
    default:
      return "Disconnected";
  }
}

/** The one line under the card's title. */
export function hubNote(state: HubState): string {
  switch (state) {
    case "mirror-disconnected":
      return "The records stay here, but they do not update. Reconnect Zoho to update them again.";
    case "mirror-needs-reset":
      return "The new Zoho account is not the one these records came from. The two accounts never mix.";
    case "mirror-connected":
      return "Zoho owns these records. Metorite keeps them up to date.";
    case "native":
      return "Your team keeps its leads, deals and contacts here.";
    default:
      return "Choose where your leads, deals and contacts live.";
  }
}

// ── The record counts (GET /crm/settings/counts) ──────────────────────────

export type RecordCounts = {
  counts: Record<string, number>;
  total: number;
};

/** The entities, in the order the hub and the dialog list them. */
export const COUNT_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["leads", "Leads"],
  ["contacts", "Contacts"],
  ["organizations", "Companies"],
  ["deals", "Deals"],
  ["activities", "Activities"],
  ["module_records", "Other Zoho records"],
];

export function countLines(
  counts: Record<string, number> | null | undefined,
): { key: string; label: string; count: number }[] {
  return COUNT_LABELS.map(([key, label]) => ({ key, label, count: counts?.[key] ?? 0 }));
}

/** "1,234", with the grouping that the import progress uses. */
export function formatCount(n: number): string {
  return n.toLocaleString("en-US");
}
