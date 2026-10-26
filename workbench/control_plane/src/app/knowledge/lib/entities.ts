// ── The entity directories of Knowledge (WS-61 KC-8b, `knowledge_curation.md` §6) ─
//
// The client of `gateway/routes/knowledge/entities.py`. An entity is a thing
// that a page can be about: the company, a product, a person, a customer, a
// workstream, a workflow or a topic. A directory lists the entities of one
// type that the member may see.
//
// ⚠️ The client holds NO permission rule. The gateway asks the app that owns
// each record, and answers only with the types and rows that the member may
// see. This file only draws what it is given. Fence: `entities.test.ts`.

import { KnowledgeError, call } from "./api";
import { KNOWLEDGE_HOME } from "./links";

/** The seven types, in the order of §6.1. */
export const ENTITY_TYPES = [
  "company",
  "product",
  "person",
  "customer",
  "workstream",
  "workflow",
  "topic",
] as const;

export type EntityType = (typeof ENTITY_TYPES)[number];

export interface Entity {
  /** `null` until a page needs the pointer: the owner row has no entity yet. */
  id: string | null;
  type: EntityType;
  ref_table: string;
  ref_id: string;
  name: string;
  aliases: string[];
  space_id: string | null;
  page_id: string | null;
}

export interface EntityList {
  entities: Entity[];
  /** True when a rule hit its cap, so a row may be missing. */
  truncated: boolean;
}

/** The types the member sees at least one row of. The cache key of the rail. */
export const ENTITY_TYPES_KEY = "knowledge/entity-types";

/** The path of the directories: `/knowledge/directories/<type>`. */
export const DIRECTORIES_HOME = `${KNOWLEDGE_HOME}/directories`;

export function isEntityType(value: string | null | undefined): value is EntityType {
  return (ENTITY_TYPES as readonly string[]).includes(value ?? "");
}

export function directoryHref(type: EntityType): string {
  return `${DIRECTORIES_HOME}/${type}`;
}

/** The type of a directory path, or `null` off a directory or on an unknown type. */
export function directoryTypeOf(pathname: string | null): EntityType | null {
  const m = /^\/knowledge\/directories\/([^/?#]+)\/?$/.exec(pathname ?? "");
  if (!m) return null;
  const type = decodeURIComponent(m[1]);
  return isEntityType(type) ? type : null;
}

/** The cache key of one directory read. Under the Knowledge prefix, so a write re-reads it. */
export function entitiesKey(type: EntityType, q: string): string {
  return `knowledge/entities?type=${type}&q=${encodeURIComponent(q)}`;
}

const LABELS: Record<EntityType, string> = {
  company: "Company",
  product: "Products",
  person: "People",
  customer: "Customers",
  workstream: "Workstreams",
  workflow: "Workflows",
  topic: "Topics",
};

export function typeLabel(type: EntityType): string {
  return LABELS[type];
}

/** The type as one thing, for a sentence: "This customer already has a page." */
export function typeNoun(type: EntityType): string {
  return type;
}

const ICONS: Record<EntityType, string> = {
  company: "Building2",
  product: "Package",
  person: "Users",
  customer: "Handshake",
  workstream: "FolderKanban",
  workflow: "Workflow",
  topic: "Tag",
};

export function typeIcon(type: EntityType): string {
  return ICONS[type];
}

const HELP: Record<EntityType, string> = {
  company: "Your organization. Write the page that tells people what it is.",
  product: "What you make or sell. A product lives in Knowledge.",
  person: "The people of your team, from People.",
  customer: "The customers you know, from CRM.",
  workstream: "The projects you can open, from Projects.",
  workflow: "The workflows you can open, and the procedures you wrote.",
  topic: "A subject that pages can be about. A topic lives in Knowledge.",
};

export function typeHelp(type: EntityType): string {
  return HELP[type];
}

/** The app that owns the record of each type, for "Open in {App}". */
const OWNER_APPS: Partial<Record<EntityType, string>> = {
  person: "People",
  customer: "CRM",
  workstream: "Projects",
  workflow: "Workflows",
};

/** The name of the app behind `ownerHref`, or `null`. */
export function ownerApp(type: EntityType): string | null {
  return OWNER_APPS[type] ?? null;
}

/**
 * The link to the record in the app that owns it, or `null` when the record
 * has no page of its own there. A written workflow (`ref_table` of
 * Knowledge) has none. No permission logic: the gateway listed the row, so
 * the member may open it.
 *
 * ⚠️ The customer link is `/crm?organization=<id>`. `crm_companies` is the
 * table of the CRM's organizations sheet (`routes/crm/core.py` ORGANIZATIONS),
 * and `parseRecord` in `app/crm/lib/urlState.ts` reads `organization`.
 */
export function ownerHref(entity: Pick<Entity, "type" | "ref_table" | "ref_id">): string | null {
  const id = encodeURIComponent(entity.ref_id);
  switch (entity.type) {
    case "person":
      return `/people/${id}`;
    case "customer":
      return `/crm?organization=${id}`;
    case "workstream":
      return `/projects?project=${id}`;
    case "workflow":
      return entity.ref_table === "workflows" ? `/workflows/${id}` : null;
    default:
      return null;
  }
}

/**
 * The warning the "Write the page" form shows (§6.2, the KC-8b decision). A
 * page is read by everyone who can read its space. The owner app may be
 * narrower than that, so the form says so. A company, a product and a topic
 * live in Knowledge and need no warning.
 */
export function pageWarning(type: EntityType): string {
  const lead = "Everyone who can read this space will see this page and the name of this";
  switch (type) {
    case "customer":
      return `${lead} customer — including people who can't open it in CRM.`;
    case "workstream":
      return `${lead} workstream — including people who can't open it in Projects.`;
    case "person":
      return `${lead} person — including people who can't open them in People.`;
    case "workflow":
      return `${lead} workflow — including people who can't open it in Workflows.`;
    default:
      return "";
  }
}

// ── The calls ───────────────────────────────────────────────────────────────

/** The types with a row the member may see, in the order of §6.1. */
export async function listEntityTypes(): Promise<EntityType[]> {
  const out = await call<{ types?: string[] }>("/entity-types");
  if (!out || !Array.isArray(out.types)) throw new Error("The types answer has no list.");
  return out.types.filter(isEntityType);
}

export async function listEntities(type: EntityType, q = ""): Promise<EntityList> {
  const query = `type=${type}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`;
  const out = await call<Partial<EntityList>>(`/entities?${query}`);
  if (!out || !Array.isArray(out.entities)) throw new Error("The directory answer has no list.");
  return { entities: out.entities, truncated: out.truncated === true };
}

/** Get or make the entity of an owner row. The gateway reads the row under its rule first. */
export function entityByRef(type: EntityType, refId: string): Promise<Entity> {
  return call<Entity>("/entities/by-ref", { method: "POST", body: JSON.stringify({ type, ref_id: refId }) });
}

/** The page the entity gets. `spaceId` is absent for the default space. */
export function makeEntityPage(
  entityId: string,
  spaceId?: string,
): Promise<{ entity: Entity; page: { id: string } }> {
  return call(`/entities/${encodeURIComponent(entityId)}/page`, {
    method: "POST",
    body: JSON.stringify(spaceId ? { space_id: spaceId } : {}),
  });
}

/** The two types that Knowledge makes itself. The gateway refuses any other (KC-8d). */
export type CreatableType = "product" | "topic";

export interface EntityCreateBody {
  type: CreatableType;
  name: string;
  aliases: string[];
  space_id: string;
}

/** The line a member reads when the gateway refuses the space (403 or 404). */
export const CANNOT_ADD_LINE = "You cannot add to that space.";

/**
 * Make a product or a topic in a space the member can write in. The gateway
 * checks the space again and answers 403 or 404 for one it will not write.
 * ⚠️ A second call with the same name makes a second entity: the route is not
 * get-or-make for a Knowledge-owned type. The form checks the name first.
 */
export async function createEntity(body: EntityCreateBody): Promise<Entity> {
  try {
    return await call<Entity>("/entities", { method: "POST", body: JSON.stringify(body) });
  } catch (err) {
    if (err instanceof KnowledgeError && (err.status === 403 || err.status === 404)) {
      throw new KnowledgeError(CANNOT_ADD_LINE, err.status);
    }
    throw err;
  }
}
