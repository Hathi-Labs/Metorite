# AF-7

Tier: LOW

Gate: AGENT-SAFE. After AF-1

## What

**Page headers and sections that fold** (task 3155). Rung 6. `header` gives a page one sentence and a count, through `PageHeader`. `sections` put cells in groups, and each group folds through `Collapsible`

## Done when

A folded section stays folded after a reload. A page with no `header` draws no `PageHeader`. `test_studio_manifest.py` holds the caps. Visual review
