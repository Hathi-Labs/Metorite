// ── The shapes of the /knowledge gateway (WS-61 KB-1) ──────────────────────
//
// Read from `apps/services/gateway/gateway/routes/knowledge/core.py`
// (`space_dict`, `page_dict`, `read_versions`) and from `spaces.py` and
// `pages.py`. The client mirrors them and adds nothing.

/** A member's level on a space (`core.level_on`). */
export type SpaceLevel = "read" | "write" | "manage";

/** The kind of a space (§4.1). `agent` is reserved by the gateway's order. */
export type SpaceKind = "company" | "team" | "me" | "agent";

/** One row of `GET /knowledge/spaces`. */
export interface Space {
  id: string;
  slug: string;
  name: string;
  purpose: string;
  icon: string;
  kind: SpaceKind;
  owner_email: string | null;
  archived_at: string | null;
  created_at: string | null;
  level: SpaceLevel;
  page_count: number;
}

/** The state of a page (§4.2). */
export type PageState = "draft" | "published" | "archived";

/** One row of `GET /knowledge/pages`: a page with no body. */
export interface PageSummary {
  id: string;
  space_id: string;
  parent_id: string | null;
  position: number;
  title: string;
  state: PageState;
  author_email: string;
  reviewers: string[];
  review_at: string | null;
  source_ref: string | null;
  published_at: string | null;
  archived_at: string | null;
  history_from: number | null;
  archived_from_state: string | null;
  updated_at: string | null;
  created_at: string | null;
}

/** Why a version was written (§4.2). */
export type VersionReason =
  | "create"
  | "edit"
  | "publish"
  | "move"
  | "archive"
  | "restore"
  | "revert"
  | "agent_proposal"
  | "accept"
  | "import";

/** One version in `GET /knowledge/pages/{id}`. The body is not in it. */
export interface PageVersion {
  version: number;
  title: string;
  author_email: string;
  reason: VersionReason | string;
  via: string;
  created_at: string | null;
}

/** One link in `GET /knowledge/pages/{id}`. KB-2b draws them. */
export interface PageLink {
  id: string;
  target_kind: string;
  target_id: string;
  label: string | null;
  created_by: string;
  created_at: string | null;
}

/** `GET /knowledge/pages/{id}`: one page with its body and its history. */
export interface PageDetail extends PageSummary {
  body_md: string;
  origin: string | null;
  level: SpaceLevel | null;
  can_write: boolean;
  versions: PageVersion[];
  links: PageLink[];
}

/** The answer of each page write: the page and the number of its new version. */
export interface PageWritten extends PageSummary {
  body_md: string;
  origin: string | null;
  version: number;
}

/** `POST /knowledge/pages`. With no `space_id`, the page lands in `me`. */
export interface PageCreateBody {
  space_id?: string;
  parent_id?: string;
  title: string;
  body_md?: string;
}

/** `PATCH /knowledge/pages/{id}`. Only the fields that changed. */
export interface PagePatchBody {
  title?: string;
  body_md?: string;
}

/** `POST /knowledge/pages/{id}/revert`. */
export interface PageRevertBody {
  version: number;
}
