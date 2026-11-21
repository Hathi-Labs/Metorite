/**
 * The sample run of the step player (`ai_studio.md` §14 D14a).
 *
 * Ten steps of one run, and a door that plays the store: on an update it stamps the sign-off
 * with the member and the time, as `objects_store` stamps `updated_by` and `updated_at`. The
 * real door drops those two (`flattenRecords`), so only this door shows a sign-off line until
 * D14b. Nothing is read from the network and nothing is stored. A reload brings the first rows back.
 *
 * It does NOT change `sampleDoor.ts`. It borrows the picture address (`sampleFileAddress`).
 */
import type { ActionResult, Row, StudioDataDoor } from "./dataDoor";
import { sampleFileAddress } from "./sampleDoor";
import { RESULT_KEYS, orderSteps } from "./stepPlayerModel";

/** The member that the sample door stamps on a sign-off. */
export const SAMPLE_MEMBER = "Asha Rao";

/** The step with a required check. */
export const CHECK_STEP = 4;
/** The step with no photo. */
export const NO_PHOTO_STEP = 7;

const STEPS: ReadonlyArray<{ title: string; instruction: string }> = [
  { title: "Clear the work area", instruction: "Remove tools and loose parts from the bench. Switch the machine off." },
  { title: "Fit the base plate", instruction: "Set the plate on the four pins. Hand-tighten the bolts first." },
  { title: "Torque the bolts", instruction: "Tighten each bolt in a cross pattern, to the value on the card." },
  { title: "Check the guard", instruction: "Confirm that the guard is closed and the lock holds. Do not skip this check." },
  { title: "Load the first part", instruction: "Place the part against the stop. Keep your hands clear of the press." },
  { title: "Run one test cycle", instruction: "Press the green button once. Watch the full stroke." },
  { title: "Record the reading", instruction: "Read the gauge. Write the value in the note of this step." },
  { title: "Clean the tray", instruction: "Wipe the tray with a dry cloth. Put the cloth in the bin." },
  { title: "Label the part", instruction: "Stick the label on the top face. The batch number must be visible." },
  { title: "Close the run", instruction: "Switch the machine off. Hang the key on the board." },
];

/**
 * The ten sample steps. `doneCount` steps from the first are already signed, by another member.
 * The stored order is not the step order (steps 3 and 4 swap), so the player has to sort.
 */
export function sampleStepRows(doneCount = 0): Row[] {
  const rows = STEPS.map((s, i): Row => {
    const n = i + 1;
    const row: Row = { id: `st${n}`, order: n, title: s.title, instruction: s.instruction, done: n <= doneCount };
    if (n !== NO_PHOTO_STEP) row.photo = { id: `file-${n % 10}` };
    if (n === CHECK_STEP) row.check = false;
    if (n <= doneCount) {
      row.updated_by = "Ravi Menon";
      row.updated_at = `2026-11-14T09:${String(10 + n).padStart(2, "0")}:00Z`;
    }
    return row;
  });
  [rows[2], rows[3]] = [rows[3], rows[2]];
  return rows;
}

const REFUSED_ORDER = "A step is signed after the steps before it.";

/**
 * A fake door over the steps. `run` takes `object:step.update` with `{id, data}`. It keeps the
 * result keys only, so a key of identity in `data` is dropped. It refuses a sign-off whose earlier
 * steps are not done (a rule refusal). It answers with the stamp of the store.
 */
export function makeSampleStepDoor(
  rows: Row[] = sampleStepRows(), delayMs = 250, member = SAMPLE_MEMBER, scope = "sample:steps",
): StudioDataDoor {
  const store: Row[] = rows.map((r) => ({ ...r }));
  const wait = () => new Promise<void>((r) => setTimeout(r, delayMs));
  return {
    scope,
    fileAddress: sampleFileAddress,
    async read() {
      await wait();
      return store.map((r) => ({ ...r }));
    },
    async run(action, args): Promise<ActionResult> {
      await wait();
      const id = typeof args?.id === "string" ? args.id : "";
      const at = action.endsWith(".update") ? store.findIndex((r) => r.id === id) : -1;
      if (at < 0) return { ok: false, message: "That step is gone." };
      const earlier = orderSteps(store).slice(0, orderSteps(store).findIndex((r) => r.id === id));
      if (earlier.some((r) => r.done !== true)) {
        return { ok: false, message: "That did not save.", data: { rule: "steps_in_order", message: REFUSED_ORDER } };
      }
      const data = args?.data && typeof args.data === "object" ? (args.data as Record<string, unknown>) : {};
      const result: Row = {};
      for (const k of RESULT_KEYS) if (k in data) result[k] = data[k];
      const stamp = { updated_by: member, updated_at: new Date().toISOString() };
      store[at] = { ...store[at], ...result, ...stamp };
      return { ok: true, message: "Signed. Nothing is stored.", data: { id, ...stamp } };
    },
  };
}
