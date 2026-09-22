# Looking at the operator console

H-122: this app had **no browser rig**, and its test suite renders nothing. So
every UI claim about it was reasoned from CSS rules. Nobody saw it. The owner
reported the same surface as broken three times before anybody rendered it, and
the fourth report is what built this.

The first run found four defects in one screen. None was visible in the code.

## Why it is not the control plane's rig

`workbench/control_plane/e2e/visual/` stubs `/api/**` in the browser. It cannot
work here. **These pages are server components** — `readAiCatalog` fetches from
`CUSTOMER_CONSOLE_URL` inside Next, so Playwright never sees the request.

So the stub is a whole fake Console, and the app is pointed at it.

## Run it

⚠️ `playwright-core` is a devDependency of THIS app. The first version of
this README told you to run the scripts here. The package lived only in the
control plane, so each one died on `ERR_MODULE_NOT_FOUND`.

Three terminals, or three background jobs.

```
node e2e/visual/fake-console.mjs

CUSTOMER_CONSOLE_URL=http://127.0.0.1:8199 \
CUSTOMER_CONSOLE_OPERATOR_TOKEN=rig-token \
OPERATOR_CONSOLE_STAFF_SECRET=rig-secret \
OPERATOR_CONSOLE_DEFAULT_DEPLOYMENT_LABEL=gateway \
npx next dev -p 3102

node e2e/visual/capture.mjs ./shots
node e2e/visual/capture-open.mjs ./shots
```

Then **open the PNGs and look at them**. That is the point.

`capture.mjs` walks the pages. `capture-open.mjs` clicks a control first, which
is where the editors live and where three of the four defects were.

## The four traps, all measured

1. **`data-theme="light"`, not a `.light` class.** The control plane uses a
   class and this app does not. Set the class and every capture comes back dark
   while the filename says light.
2. **The `NEXTJS-PORTAL` badge sits over the sidebar.** In `next dev` it draws a
   circle in the bottom-left corner. It sits on top of the theme toggle and
   clips "Dark" to "ark". It is not our UI. Confirm with `elementFromPoint`
   first.
3. **`position: fixed` in a `fullPage` capture.** The rail is fixed. On a tall
   page it renders once, at the viewport position. That can read as an
   overlap. Take a viewport-sized capture before you believe it.
4. **The staff cookie is the whole door.** `identityMode` is `interim` unless
   the session flag is set, so `operator_staff` holding
   `OPERATOR_CONSOLE_STAFF_SECRET` is enough. No login flow, no Supabase.

## The fixtures are REAL

`fixtures.json` is the live production payload, captured 2026-09-22 from the
box. So what renders is what the owner sees. An unpriced rate card, seven unbound
tiers, one vendor credential. Tidy invented data would have shown none of
these defects.

Re-capture them the same way when production moves:

```
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8090/catalog/models
```

⚠️ **No secret belongs in here.** The payloads carry model ids, prices and org
slugs. They must never carry a key, and `fake-console.mjs` serves them to
localhost only.

## What it does not cover

It runs the **dev** bundle and it **stubs** the Console. A capture is **one
frame**. Hover, focus and keyboard states need their own step, and a control
is usually weakest there. It measures no contrast.

It is deliberately **not wired into CI**. A capture rig asserts nothing. A
suite of tests that cannot fail teaches people to ignore the suite.
Turn what you find into an assertion instead — `formrow.test.ts` and
`actionrow.test.ts` are what this rig's findings became.
