// The Studio package manifest, schema v1 (STU-A2). Types only: no runtime code.
// The twin of packages/acb_common/acb_common/studio_manifest.py.
// tests/unit/test_studio_manifest_ts_parity.py holds the two equal.

export type StudioVersion = 1;

export type ClassLetter = "A" | "B" | "C" | "X";

export type FieldType =
  | "text"
  | "longText"
  | "number"
  | "money"
  | "date"
  | "datetime"
  | "checkbox"
  | "select"
  | "multiSelect"
  | "person"
  | "relation"
  | "file"
  | "url"
  | "email"
  | "phone"
  | "geo";

export type BlockName =
  | "table"
  | "form"
  | "detail"
  | "board"
  | "chart"
  | "stat"
  | "calendar"
  | "timeline"
  | "mindMap"
  | "processFlow"
  | "checklist"
  | "map"
  | "markdown";

export interface FieldDef {
  key: string;
  type: FieldType;
  label?: string | null;
  required?: boolean;
  many?: boolean;
  options?: string[] | null;
  to?: string | null;
}

export interface ObjectDef {
  name: string;
  label?: string | null;
  fields?: FieldDef[];
}

export interface ActionDef {
  id: string;
  uses: string;
  title?: string | null;
  set?: Record<string, unknown> | null;
}

export interface Source {
  object?: string | null;
  action?: string | null;
  args?: Record<string, unknown> | null;
}

export interface Card {
  title?: string | null;
  meta?: string[];
}

export interface ViewDef {
  id: string;
  block: BlockName;
  title?: string | null;
  source?: Source | null;
  object?: string | null;
  fields?: string[];
  submit?: string | null;
  group_by?: string | null;
  card?: Card | null;
  start?: string | null;
  end?: string | null;
  columns?: string[];
  sort?: string | null;
  actions?: string[];
  text?: string | null;
}

export interface Trigger {
  record?: string | null;
  when?: Record<string, unknown> | null;
  schedule?: string | null;
}

export interface WorkflowDef {
  id: string;
  trigger?: Trigger | null;
  graph?: string | null;
}

export interface SkillExample {
  prompt: string;
  action?: string | null;
  args?: Record<string, unknown> | null;
}

export interface Skill {
  description: string;
  instructions?: string;
  actions?: string[];
  cards?: string[];
  knowledge?: string[];
  examples?: SkillExample[];
}

export interface ShellJob {
  verb: string;
  view: string;
}

export interface ShellNeed {
  object: string;
  where?: Record<string, unknown> | null;
}

export interface Shell {
  jobs?: ShellJob[];
  needs?: ShellNeed[];
  search?: string[];
}

// PKG-5a (package_host.md sections 3.1, 4, 5.4, 8). The shapes are loose on purpose.
// PKG-14 to PKG-16 give `slots`, `needs` and `overrides` their meaning.
export type ManifestKind = "studio" | "system";

export type ContributionEntry = Record<string, unknown>;

// A contribution point (section 4, or a P0-only key) to its entries.
export type Contributes = Record<string, ContributionEntry[]>;

export type SlotDecl = Record<string, unknown>;

export type NeedDecl = Record<string, unknown>;

export type OverrideDecl = Record<string, unknown>;

export interface StudioManifest {
  studio: StudioVersion;
  kind?: ManifestKind;
  id: string;
  // Required for `studio`. A `system` file holds neither.
  name?: string | null;
  purpose?: string | null;
  team?: string | null;
  connections?: string[];
  objects?: ObjectDef[];
  actions?: ActionDef[];
  views?: ViewDef[];
  workflows?: WorkflowDef[];
  skill?: Skill | null;
  shell?: Shell | null;
  contributes?: Contributes;
  extends?: string | null;
  slots?: SlotDecl[];
  needs?: NeedDecl[];
  overrides?: OverrideDecl[];
}

// One refusal of the validator: a JSON path and one sentence.
export interface ManifestError {
  path: string;
  message: string;
}
