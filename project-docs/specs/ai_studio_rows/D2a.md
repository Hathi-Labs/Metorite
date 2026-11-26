# D2a

Tier: STD

Gate: AGENT-SAFE

## What

🔨 **BUILT, preview only (2026-11-02).** The blocks run on the fake door at `/studio/blocks-preview`, which is not in the nav. The Table, Form and Detail blocks, from the §3.1 field types, on a mocked door. **Table:** `DataGrid` in `genUITemplates.tsx` grows a filter box and takes its cell kinds from the field types, on the D1 bind path. **Form:** all 16 types, a check on blur and on submit, a submit through `door.run`, and a 422 shown under its field. **Detail:** a read-only record sheet. **Field check:** `src/lib/studio/fieldCheck.ts` is the TypeScript twin of `check_value` and `validate_record`. Python is the one source. `tests/unit/test_studio_field_cases_parity.py` writes the golden file `packages/acb_common/acb_common/studio_field_cases.json` and fails when the committed file differs. **Preview:** `/studio/blocks-preview`, with a fake door and sample records, labelled "Sample data". It is not in `nav.ts`, and the longest-prefix match of `/studio` gates it by `workflows`

## Done when

`fieldCheck.test.ts` reads the golden file and holds at least 48 cases (16 types, 3 each). `test_studio_field_cases_parity.py` holds the file and the six limits equal to the Python module. `blocks.test.ts`: each type renders in the Form and the Detail. The Table shows its loading, empty, error and row states. A bad value blocks the submit, sets `aria-invalid` and `aria-describedby`, and focuses the first bad field. A double submit calls `door.run` once. No `createContext` stands outside a "use client" file under `lib/studio/`. `dataDoor.test.ts` and the lockstep test stay green. Visual review in four contexts
