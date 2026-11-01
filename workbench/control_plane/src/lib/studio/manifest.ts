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
  // The field gets a row in the index side table of C1 (studio_record_hot).
  hot?: boolean;
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

export interface StudioManifest {
  studio: StudioVersion;
  id: string;
  name: string;
  purpose: string;
  team?: string | null;
  connections?: string[];
  objects?: ObjectDef[];
  actions?: ActionDef[];
  views?: ViewDef[];
  workflows?: WorkflowDef[];
  skill?: Skill | null;
  shell?: Shell | null;
}

// One refusal of the validator: a JSON path and one sentence.
export interface ManifestError {
  path: string;
  message: string;
}
