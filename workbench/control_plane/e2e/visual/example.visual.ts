import { test } from "@playwright/test";
import {
  captureContexts,
  clickAndWait,
  gotoAndSettle,
  stubApi,
  watchErrors,
  writeNotes,
} from "./harness";

/**
 * A worked example, and the file to copy when you review a surface.
 *
 * ⚠️ Named `.visual.ts`, not `.spec.ts`, so `npx playwright test` does NOT pick
 * it up. A capture rig asserts nothing, and a suite full of tests that cannot
 * fail teaches people to ignore the suite. Run it by name:
 *
 *     npx playwright test e2e/visual/example.visual.ts --project=chromium
 *
 * To review a different surface, copy this file, change the route and the
 * stubs, and delete the parts you do not need. Do not commit your copy — the
 * captures are the deliverable, not the rig.
 */

const OUT = process.env.UX_OUT || "ux-shots/example";

test("capture a surface in every context", async ({ page }) => {
  // Eight contexts plus navigation. The 45s default is not enough.
  test.setTimeout(240_000);

  const seen = new Set<string>();
  const errors = watchErrors(page);

  // Keys are path fragments; the longest match wins, so order does not matter.
  await stubApi(
    page,
    {
      "auth/me": { email: "you@example.com", name: "You" },
    },
    { seen },
  );

  await gotoAndSettle(page, "/projects");

  // Some surfaces only draw once you pick something. `clickAndWait` waits for
  // the thing the click OPENS, which mounts with its data and not with the
  // click.
  await clickAndWait(page, "Firmware");

  await captureContexts(page, "surface", { out: OUT });

  writeNotes(OUT, seen, errors);
});
