/**
 * The mapper from a manifest view to the block that draws it (STU-AF1, `ai_studio.md` §5.5).
 * Pure: no React, no context, no I/O.
 *
 * It composes the builders that exist. A table uses `tableBlock`, a board uses
 * `objectBoardBlock`, and a chart or a stat uses `chartViewBlock` and `statViewBlock` of D4c.
 * A form and a detail are components, so the mapper gives the fields and the action, and the
 * app frame draws them. A view of another kind gives `null`, and `StudioPage` then shows the
 * "not built yet" card (D11b). The mapper adds no block and no node type.
 *
 * Each read goes through the door. A view whose `source.action` is not a declared action gets
 * `NOT_DECLARED_SENTENCE`, as the door guard answers.
 */
import type { FieldSpec } from "./fieldCheck";
import { tableBlock } from "./blocks";
import { chartViewBlock, statViewBlock } from "./aggregateModel";
import { computedKeys } from "./inlineEdit";
import type { ViewDef } from "./manifest";
import {
  detailFields, fieldSpecs, formFields, noBoardReason, objectBoardBlock, tableFields,
} from "./packagePreview";
import { viewObjectName } from "./pageModel";
import {
  NOT_DECLARED_SENTENCE, READ_ONLY_SENTENCE, verbMap, type DeclaredAction, type ObjectRow,
} from "./realDoor";

/** The most records that a list view reads. The store gives at most 100. */
export const VIEW_LIMIT = 100;

export interface ViewMapContext {
  /** The actions that the door of the page holds. A viewer holds the reads only. */
  actions: readonly DeclaredAction[];
  objects: readonly Pick<ObjectRow, "name" | "label" | "fields" | "rules">[];
  /** The owner may write. A viewer reads only (J2a). */
  owner: boolean;
}

export interface ViewNode {
  kind: "node";
  /** A genUI node that `GenerativeUINode` draws inside `StudioDataContext`. */
  node: { type: "template"; props: Record<string, unknown> };
}

export interface ViewForm {
  kind: "form";
  fields: FieldSpec[];
  action: string;
  title: string;
}

export interface ViewDetail {
  kind: "detail";
  fields: FieldSpec[];
  /** The list that gives the record to show. */
  source: { action: string; args: Record<string, unknown> };
}

export interface ViewProblem {
  kind: "problem";
  /** One plain sentence for the member. */
  reason: string;
}

export type ViewModel = ViewNode | ViewForm | ViewDetail | ViewProblem;

const problem = (reason: string): ViewProblem => ({ kind: "problem", reason });
const WRITE_CREATE = /^object:[a-z][a-z0-9_]{0,47}\.create$/;

/**
 * The block of one view, or `null` when its kind is not built. Never throws.
 */
export function mapView(view: ViewDef, ctx: ViewMapContext): ViewModel | null {
  const label = view.title?.trim() || view.id;
  const kinds = ["table", "board", "chart", "stat", "form", "detail"];
  if (!kinds.includes(view.block)) return null;

  // The door guard: a source that names an undeclared action is refused, and no read is made.
  const sourceAction = view.source?.action;
  if (sourceAction && !ctx.actions.some((a) => a.id === sourceAction)) return problem(NOT_DECLARED_SENTENCE);
  if (view.block === "form" && view.submit && !ctx.actions.some((a) => a.id === view.submit)) {
    return problem(NOT_DECLARED_SENTENCE);
  }

  if (view.block === "chart" || view.block === "stat") {
    const out = view.block === "chart" ? chartViewBlock(view, { actions: ctx.actions })
      : statViewBlock(view, { actions: ctx.actions });
    return out.ok ? { kind: "node", node: out.node } : problem(out.reason);
  }

  const name = viewObjectName(view, ctx.actions);
  const object = name ? ctx.objects.find((o) => o.name === name) : undefined;
  if (!name || !object) return problem(`“${label}” names no object that this app has.`);
  const specs = fieldSpecs(object as ObjectRow);
  const computed = computedKeys(object.rules);
  // A view lists the fields it names, in its order. With none, it lists every field.
  const named = view.fields?.length ? view.fields.map((k) => specs.find((f) => f.key === k)).filter((f): f is FieldSpec => !!f) : specs;
  const shown = named.length ? named : specs;
  const verbs = verbMap(ctx.actions);
  const list = { action: `object:${name}.list`, args: { limit: VIEW_LIMIT } };
  if (verbs[list.action] === undefined && view.block !== "form") {
    return problem(NOT_DECLARED_SENTENCE);
  }

  switch (view.block) {
    case "table": {
      const canUpdate = ctx.owner && verbs[`object:${name}.update`] !== undefined;
      return {
        kind: "node",
        node: tableBlock(tableFields(shown), list, view.title ?? object.label ?? object.name,
          { editable: canUpdate, computed: [...computed] }),
      };
    }
    case "board": {
      const canUpdate = ctx.owner && verbs[`object:${name}.update`] !== undefined;
      const node = objectBoardBlock(object, specs, computed, list, canUpdate);
      return node ? { kind: "node", node } : problem(noBoardReason(specs));
    }
    case "detail":
      return { kind: "detail", fields: detailFields(shown), source: list };
    case "form": {
      if (!ctx.owner) return problem(READ_ONLY_SENTENCE);
      const submit = view.submit ? ctx.actions.find((a) => a.id === view.submit)?.uses : `object:${name}.create`;
      // A declared submit that is not a create needs a chosen record. It is not built yet, so the
      // member sees the "not built yet" card and no door text.
      if (!submit || !WRITE_CREATE.test(submit)) return null;
      return { kind: "form", fields: formFields(shown, computed), action: submit, title: view.title ?? `New ${object.label ?? object.name}` };
    }
    default:
      return null;
  }
}
