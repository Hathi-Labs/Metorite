/**
 * Rooms — the client contract for a conversation several people share.
 *
 * A room is a chat session with other people in it. Everything here mirrors the
 * gateway's `/chat/sessions/{id}/room` surface (apps/services/gateway/gateway/
 * routes/rooms.py) one-for-one; there is no client-side model of membership,
 * because a second model of who-may-do-what is a second answer waiting to
 * disagree with the server's.
 *
 * The one thing this module DOES decide is the vocabulary the UI speaks:
 * `role` is a capacity in this room (owner / member / viewer), never an org
 * role. The two are different axes and the UI must never present them as one.
 */

export type RoomRole = "owner" | "member" | "viewer";
export type Visibility = "private" | "people" | "org";
export type SubjectKind = "person" | "group" | "org";

export interface RoomParticipant {
  subject: string;              // email | group:<slug> | org
  role: RoomRole;
  kind: SubjectKind;
  displayName: string;
  avatarUrl: string | null;
  /** Only meaningful for people. A suspended member caps the whole room. */
  status: "active" | "invited" | "suspended" | "removed";
  addedBy: string | null;
  addedAt: string | null;
}

export interface RoomAgent {
  agentName: string;
  /** `primary` answers an unaddressed turn; `mentioned` answers when @named. */
  role: "primary" | "mentioned";
  addedBy: string | null;
  addedAt: string | null;
}

export interface PresenceEntry {
  email: string;
  at: number;                   // epoch SECONDS, server clock
  state: "viewing" | "typing";
}

/**
 * What this room cannot reach, and who is the reason.
 *
 * A shared run acts at the intersection of every participant's access, so
 * adding someone can silently remove an integration the agent had yesterday.
 * The UI's job is to make that loss legible — never to hide it behind a
 * generic error when the agent later says it can't reach Zoho.
 */
export interface CapabilityCap {
  capped: { capability: string; missingFor: string[] }[];
  /** Suspended participants. A suspended member zeroes the room entirely. */
  inactive: string[];
  /** True when the cap could not be computed — say so rather than imply none. */
  degraded: boolean;
}

export interface RoomYou {
  email: string;
  role: RoomRole | null;
  canRead: boolean;
  canSend: boolean;
  canCancel: boolean;
  canInvite: boolean;
  canManage: boolean;
  /** Late-joiner waterline: history before this ms is not yours to read. */
  sinceMessageTs: number | null;
}

export interface RoomState {
  sessionId: string;
  /** False for a thread whose session row does not exist yet. */
  exists: boolean;
  title?: string | null;
  createdBy?: string | null;
  you: RoomYou;
  participants: RoomParticipant[];
  agents: RoomAgent[];
  presence: PresenceEntry[];
  settings: {
    visibility: Visibility;
    floorMode: "open" | "driver";
    historyVisibility: "full" | "since_join";
  };
  isShared?: boolean;
  cap: CapabilityCap;
}

export interface DirectoryEntry {
  subject: string;
  displayName: string;
  kind: "person" | "group";
  avatarUrl?: string | null;
  memberCount?: number;
}

/** Live events on `cc:room:{threadId}` — see gateway/room_stream.py. */
export type RoomEvent =
  | { type: "PARTICIPANT_JOINED"; subject: string; role: RoomRole; addedBy: string; ts: number }
  | { type: "PARTICIPANT_LEFT"; subject: string; removedBy: string; self: boolean; ts: number }
  | { type: "PARTICIPANT_ROLE_CHANGED"; subject: string; role: RoomRole; changedBy: string; ts: number }
  | { type: "AGENT_ADDED"; agentName: string; role: RoomAgent["role"]; addedBy: string; ts: number }
  | { type: "AGENT_REMOVED"; agentName: string; removedBy: string; ts: number }
  | { type: "ROOM_SETTINGS_CHANGED"; changedBy: string; settings: Record<string, string>; ts: number }
  | { type: "USER_MESSAGE"; author: string; agentName: string; content: string; ts: number }
  // Somebody redirected the run that was already going instead of superseding
  // it (docs/multiplayer/README.md §4.6). Deliberately a ROOM event, not a run
  // event: run events are folded into the transcript by both
  // gateway/chat_fold.py and lib/chatStream.ts, and a steer belongs to the room
  // rather than to the assistant message the turn produces.
  | {
      type: "STEER_INJECTED";
      author: string;
      agentName: string;
      text: string;
      appliedAt: string;
      delivered: boolean;
      ts: number;
    }
  | { type: "ROOM_REPLAY_DONE" }
  | { type: "ROOM_PING" };

/**
 * A room of one. Used before the server answers and for threads that have no
 * session row yet, so the composer never renders in a "can I type?" limbo —
 * the honest default for your own new conversation is: yes, it's yours.
 */
export function soloRoom(sessionId: string, email: string): RoomState {
  return {
    sessionId,
    exists: false,
    you: {
      email,
      role: "owner",
      canRead: true,
      canSend: true,
      canCancel: true,
      canInvite: true,
      canManage: true,
      sinceMessageTs: null,
    },
    participants: [],
    agents: [],
    presence: [],
    settings: { visibility: "private", floorMode: "open", historyVisibility: "full" },
    isShared: false,
    cap: { capped: [], inactive: [], degraded: false },
  };
}

/**
 * True when somebody other than you is in this conversation.
 *
 * Every piece of room UI keys off this rather than off participant count, so a
 * solo thread renders exactly as it did before rooms existed: no rail, no
 * banner, no share affordance shouting at a person working alone.
 */
export function isShared(room: RoomState | null | undefined): boolean {
  // A malformed payload (an array, a missing `you`) is solo, never a crash:
  // this runs on every render of the chat header (S8 fix round 5).
  if (!isRoomState(room)) return false;
  if (room.isShared === true) return true;
  return room.participants.some((p) => p?.subject !== room.you.email);
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/**
 * Whether a value has the shape every room surface reads. `fetchRoom` refuses
 * anything else, so `useRoom` falls back to a room of one instead of handing
 * `RoomHeader` a payload it would crash on (for example `[]` from a proxy
 * that answered an unknown path with an empty array).
 */
export function isRoomState(v: unknown): v is RoomState {
  if (!isObject(v)) return false;
  const you = v.you;
  const cap = v.cap;
  return (
    isObject(you) &&
    typeof you.email === "string" &&
    Array.isArray(v.participants) &&
    Array.isArray(v.agents) &&
    Array.isArray(v.presence) &&
    isObject(v.settings) &&
    isObject(cap) &&
    Array.isArray(cap.capped) &&
    Array.isArray(cap.inactive)
  );
}

/** People only — group and `org` rows are subjects, not faces. */
export function peopleOf(room: RoomState): RoomParticipant[] {
  return room.participants.filter((p) => p.kind === "person");
}

export function displayNameFor(room: RoomState, email: string): string {
  const hit = room.participants.find((p) => p.subject === email);
  return hit?.displayName || email.split("@")[0] || email;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      (body as { detail?: string; error?: string }).detail ??
        (body as { error?: string }).error ??
        `Request failed (${res.status})`
    );
  }
  return body as T;
}

export async function fetchRoom(sessionId: string): Promise<RoomState> {
  const body = await json<unknown>(
    await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/room`, {
      cache: "no-store",
    })
  );
  // A payload of the wrong shape is refused here, so the caller's fallback
  // (a room of one) renders instead of a crash further down.
  if (!isRoomState(body)) throw new Error("The room answer was malformed.");
  return body;
}

export async function addParticipant(
  sessionId: string,
  subject: string,
  role: RoomRole = "member"
): Promise<{ cap: CapabilityCap }> {
  return json(
    await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/participants`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject, role }),
    })
  );
}

export async function setParticipantRole(
  sessionId: string,
  subject: string,
  role: RoomRole
): Promise<void> {
  await json(
    await fetch(
      `/api/chat/sessions/${encodeURIComponent(sessionId)}/participants/${encodeURIComponent(subject)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role }),
      }
    )
  );
}

export async function removeParticipant(
  sessionId: string,
  subject: string
): Promise<{ left: boolean }> {
  return json(
    await fetch(
      `/api/chat/sessions/${encodeURIComponent(sessionId)}/participants/${encodeURIComponent(subject)}`,
      { method: "DELETE" }
    )
  );
}

export async function updateRoomSettings(
  sessionId: string,
  patch: {
    visibility?: Visibility;
    floor_mode?: "open" | "driver";
    history_visibility?: "full" | "since_join";
  }
): Promise<void> {
  await json(
    await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/room`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    })
  );
}

export async function addRoomAgent(
  sessionId: string,
  agentName: string,
  role: RoomAgent["role"] = "mentioned"
): Promise<void> {
  await json(
    await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/agents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_name: agentName, role }),
    })
  );
}

export async function removeRoomAgent(
  sessionId: string,
  agentName: string
): Promise<void> {
  await json(
    await fetch(
      `/api/chat/sessions/${encodeURIComponent(sessionId)}/agents/${encodeURIComponent(agentName)}`,
      { method: "DELETE" }
    )
  );
}

export async function sendPresence(
  sessionId: string,
  state: PresenceEntry["state"] = "viewing"
): Promise<PresenceEntry[]> {
  const body = await json<{ presence: PresenceEntry[] }>(
    await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/presence`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state }),
    })
  );
  return body.presence ?? [];
}

export async function fetchDirectory(q = ""): Promise<DirectoryEntry[]> {
  const body = await json<{ people: DirectoryEntry[]; groups: DirectoryEntry[] }>(
    await fetch(`/api/chat/directory?q=${encodeURIComponent(q)}`, { cache: "no-store" })
  );
  return [...(body.people ?? []), ...(body.groups ?? [])];
}

/**
 * Human-readable name for a capability, for the cap banner.
 *
 * "integrations:use:zoho-crm" is a permission string; "Zoho CRM" is what a
 * person reading a header needs. Unknown shapes fall back to the raw string
 * rather than to a lie.
 */
export function capabilityLabel(capability: string): string {
  const [domain, , ...rest] = capability.split(":");
  const slug = rest.join(":");
  if (!slug) return capability;
  const pretty = slug
    .split("-")
    .map((w) => (w.length <= 3 ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1)))
    .join(" ");
  return domain === "agents" ? `${pretty} (agent)` : pretty;
}
