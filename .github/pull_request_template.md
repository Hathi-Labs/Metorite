## What changed

<One or two sentences: what this PR changes, and why.>

## The UI gate

Fill this section when the diff changes `workbench/control_plane/src`.
Otherwise, write "Visual review: no UI change" and delete the rest.
The rules are in `workbench/control_plane/DESIGN_SYSTEM.md` §11.

- [ ] The interaction, in one sentence: <who does what, and what they see next>
- [ ] The surface has its loading, empty and error states.
- [ ] A reversible action shows Undo. Only an action that you cannot undo asks first.
- [ ] The diff adds no motion token, no curve and no duration.

Visual review: <the four captures: light mode, compact density, a changed accent, 390 px. Or "no UI change".>

CI fails a PR that changes `workbench/control_plane/src` when no line of the body starts with "Visual review:".
