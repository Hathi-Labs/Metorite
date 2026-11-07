/**
 * The Explorer tree of the Studio IDE (`ai_studio.md` §6.3, slice G3a).
 *
 * `buildOutline` turns the DRAFT manifest into a tree: the app, then seven branches. The
 * tree is the same for a small app and a large one. A branch with nothing in it shows a
 * muted "None yet" child, so a member sees what a complex app can hold. The function is
 * pure and never throws: a manifest that is not an object gives an empty tree and a hint.
 */
import type { StudioManifest } from "./manifest";

export type OutlineKind =
  | "app" | "branch" | "object" | "field" | "view" | "action" | "rule" | "workflow" | "skill" | "code" | "none";

/** The centre tab that a click on a node shows. */
export type OutlineTab = "preview" | "data" | "views" | "code";

export interface OutlineNode {
  /** Unique in the tree. */
  id: string;
  kind: OutlineKind;
  label: string;
  /** The muted words after the label: a type, a count, a block. */
  hint?: string;
  /** The object that a click selects, for an object, a field or a rule of it. */
  object?: string;
  /** The tab that a click shows. */
  tab?: OutlineTab;
  children?: OutlineNode[];
}

export interface Outline {
  /** The app node, or null when the manifest is not one. */
  root: OutlineNode | null;
  /** The sentence for an empty tree: what happened and the next step. Null when the tree stands. */
  hint: string | null;
}

export const NONE_YET = "None yet";
export const EMPTY_HINT =
  "The draft is empty or not readable. Ask Mira what the app should track, and the parts show here.";

/** The seven branches, in order. The ids are the ones that `branchOf` returns. */
export const BRANCHES = ["data", "views", "actions", "rules", "workflows", "skill", "code"] as const;
export type BranchId = (typeof BRANCHES)[number];

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const list = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isObject) : []);
const text = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

const none = (branch: string): OutlineNode => ({ id: `${branch}:none`, kind: "none", label: NONE_YET });

const branch = (id: BranchId, label: string, tab: OutlineTab, children: OutlineNode[]): OutlineNode => ({
  id: `branch:${id}`, kind: "branch", label, hint: children.length ? String(children.length) : undefined, tab,
  children: children.length ? children : [none(id)],
});

function fieldNode(object: string, f: Record<string, unknown>, i: number): OutlineNode {
  const key = text(f.key) || `field ${i + 1}`;
  const type = text(f.type);
  return {
    id: `field:${object}:${key}:${i}`, kind: "field", label: text(f.label) || key,
    hint: f.required === true ? `${type} · required` : type || undefined, object, tab: "data",
  };
}

function ruleNodes(manifest: Record<string, unknown>): OutlineNode[] {
  const out: OutlineNode[] = [];
  for (const o of list(manifest.objects)) {
    const object = text(o.name);
    const rules = Array.isArray(o.rules) ? o.rules.filter(isObject) : [];
    rules.forEach((r, i) => {
      out.push({
        id: `rule:${object}:${i}`, kind: "rule", label: `${object || "object"} · ${text(r.name) || text(r.kind) || `rule ${i + 1}`}`,
        hint: text(r.kind) || undefined, object, tab: "data",
      });
    });
  }
  return out;
}

/** The entries of `contributes`, one node per point. The manifest has no other code part yet. */
function codeNodes(manifest: Record<string, unknown>): OutlineNode[] {
  const c = manifest.contributes;
  if (!isObject(c)) return [];
  return Object.entries(c)
    .filter(([, entries]) => Array.isArray(entries) && entries.length > 0)
    .map(([point, entries]) => ({
      id: `code:${point}`, kind: "code" as const, label: point, hint: `${(entries as unknown[]).length}`, tab: "code" as const,
    }));
}

export function buildOutline(manifest: unknown): Outline {
  if (!isObject(manifest)) return { root: null, hint: EMPTY_HINT };
  const m = manifest as Partial<Record<keyof StudioManifest, unknown>> & Record<string, unknown>;
  const name = text(m.name) || text(m.id) || "Untitled app";

  const objects = list(m.objects).map((o, i): OutlineNode => {
    const key = text(o.name) || `object ${i + 1}`;
    const fields = list(o.fields).map((f, j) => fieldNode(key, f, j));
    return {
      id: `object:${key}`, kind: "object", label: text(o.label) || key, object: key, tab: "preview",
      hint: fields.length ? `${fields.length} field${fields.length === 1 ? "" : "s"}` : undefined,
      children: fields.length ? fields : undefined,
    };
  });
  const views = list(m.views).map((v, i): OutlineNode => ({
    id: `view:${text(v.id) || i}`, kind: "view", label: text(v.title) || text(v.id) || `view ${i + 1}`,
    hint: text(v.block) || undefined, object: text(v.object) || undefined, tab: "views",
  }));
  const actions = list(m.actions).map((a, i): OutlineNode => ({
    id: `action:${text(a.id) || i}`, kind: "action", label: text(a.title) || text(a.id) || `action ${i + 1}`,
    hint: text(a.uses) || undefined, tab: "code",
  }));
  const workflows = list(m.workflows).map((w, i): OutlineNode => ({
    id: `workflow:${text(w.id) || i}`, kind: "workflow", label: text(w.id) || `workflow ${i + 1}`, tab: "code",
  }));
  const skill: OutlineNode[] = isObject(m.skill)
    ? [{ id: "skill:main", kind: "skill", label: text(m.skill.description) || "Skill", tab: "code" }]
    : [];

  return {
    hint: null,
    root: {
      id: "app", kind: "app", label: name, tab: "preview",
      children: [
        branch("data", "Data", "data", objects),
        branch("views", "Views", "views", views),
        branch("actions", "Actions", "code", actions),
        branch("rules", "Rules", "data", ruleNodes(m)),
        branch("workflows", "Workflows", "code", workflows),
        branch("skill", "Skill", "code", skill),
        branch("code", "Code blocks", "code", codeNodes(m)),
      ],
    },
  };
}

/** Every id of a node that has children, so the page can open the tree. */
export function branchIds(outline: Outline): string[] {
  const out: string[] = [];
  const walk = (n: OutlineNode) => {
    if (n.children?.length) { out.push(n.id); n.children.forEach(walk); }
  };
  if (outline.root) walk(outline.root);
  return out;
}
