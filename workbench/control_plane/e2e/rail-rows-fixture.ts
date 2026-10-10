/**
 * A Projects tree with the owner's own names (2026-10-10), for
 * `rail-rows.spec.ts`. The long names are the point: at a 256 px rail they
 * are the ones that the old rows cut early.
 */

type Node = {
  id: string;
  name: string;
  kind?: "project" | "folder";
  status?: string;
  tasks?: number;
  done?: number;
  cancelled?: number;
  children: Node[];
};

let seq = 0;
const node = (name: string, extra: Partial<Node> = {}, children: Node[] = []): Node => ({
  id: `n-${++seq}`,
  name,
  status: "active",
  kind: "project",
  tasks: 0,
  done: 0,
  cancelled: 0,
  ...extra,
  children,
});
const folder = (name: string, children: Node[] = []) => node(name, { kind: "folder" }, children);

/** A name long enough to be cut at any rail width, and its row's parent. */
export const LONG_NAME = "Fracktal Care Support and Customer Escalations Desk";
/** A name short enough never to be cut. */
export const SHORT_NAME = "HR";
/** A row with no count, so its width at rest and on hover differ only by the actions. */
export const NO_COUNT_NAME = "Founders Office";

export const RAIL_TREE = {
  rows: [
    node("Company Operations", { tasks: 41, done: 12, cancelled: 2 }, [
      node("Finance & Accounts", { tasks: 9, done: 3 }),
      node(NO_COUNT_NAME),
      node(SHORT_NAME, { tasks: 4, done: 1 }),
      node("Operations", { tasks: 14, done: 6 }),
    ]),
    node("Fracktal Care", { tasks: 22, done: 8 }, [
      node(LONG_NAME, { tasks: 7, done: 2 }),
      node("Issue Escalations", { tasks: 3 }),
      folder("Knowledge Base"),
      folder("manufacturing"),
      node("Quality Control", { tasks: 5, done: 5 }),
    ]),
    node("Fracktory", { tasks: 18, done: 4 }, [
      folder("3d printing services"),
      folder("Engineering Services"),
      node("Fracktory Operations", { tasks: 6, done: 1 }),
    ]),
    node("Research & Development", { tasks: 57, done: 20, cancelled: 3 }, [
      node("Accessories", { tasks: 2 }),
      node("Application Engineering", { tasks: 11, done: 4 }),
      folder("Hardware", [
        node("Penrose Pellet Extruder", { tasks: 8, done: 2 }),
        node("Julia Series", { tasks: 6, done: 6 }),
        node("MDS", { tasks: 3, done: 1 }),
        node("IISc CST Clay Printer", { tasks: 9, done: 2 }, [
          node("Clay Extruder Nozzle Redesign", { tasks: 4, done: 1 }),
        ]),
      ]),
    ]),
  ],
  total: 0,
};
