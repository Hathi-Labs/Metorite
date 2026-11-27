# STU-FIX

Tier: STANDARD

Gate: AGENT-SAFE. Not built here: the string form of a manifest (HRD-3, task 2982)

## What

🔨 **BUILT (2026-11-14, task 3227). Live for every org on `STUDIO_ORGS` (production: `*`) at the next release.** **The chat builds into a draft with one id and one card.** Production showed three faults. (1) `studio_patch` needed `ops`. Now `ops` defaults to nothing, and a manifest alone is only checked. (2) The "New app" button makes a draft with an id such as `new-app-nufdl`. The model saved a manifest with another id, and the route refused it. The model then changed its id, the approved plan no longer matched, and the member approved two cards. Now `studio_save` into a draft that no one published sets the manifest id to the id of the draft. When the member approved the plan of the manifest as it came, the approval moves to the saved manifest. The two differ in the id only. The moved record keeps the old time and the remaining life of the old record. A withdrawn or expired approval moves nothing. So the publish needs no second card. A published package keeps the refusal "The manifest id cannot change." (3) `studio_draft` takes the id from the name part of the brief, up to a word such as "for" or "at". When the member holds a package with that id, it takes `-2` to `-9`, then four random characters. A brief with no Latin word gives `app-` and four random characters. The pack rules (`_STUDIO_RULES`) say one id and one card, and keep working notes out of the answer.

## Done when

`uv run pytest tests/unit/test_studio_package_tools.py tests/unit/test_studio_builder_tools.py evals/trajectories/test_studio_build_trajectory.py -q`. **R7 fence:** `test_one_plan_card_covers_a_build_into_a_draft_with_another_id` and `test_a_save_that_changes_the_id_of_a_published_package_shows_the_route_text` fail when the rule goes.
