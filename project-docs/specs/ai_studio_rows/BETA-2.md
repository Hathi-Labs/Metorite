# BETA-2

Tier: STANDARD

Gate: AGENT-SAFE. Part of the opening of task 1795

## What

🔨 **BUILT, dark (2026-11-06).** **The chat builder adds the record actions** (task 1810). The app page (Table, Form, Board) reads and writes an object only through plain `object:<name>.<verb>` actions. A plain action has no `set`, because the web door skips each action with a `set`. The chat tool `studio_record_list` refuses with no list action. A chat-built app could open to an empty screen. **The rule:** (a) each object gets a plain `list` and a plain `get` when it has none. (b) An object with no plain write action (`create`, `update` or `delete`) gets the three. An object with one or more plain write actions keeps its writes, so a member keeps one to opt out of the others. An action id is `<object>_<verb>`. The check is `with_record_actions` in `studio_tools.py`, and `complete_record_actions` adds the size guard. `_checked` runs it for `studio_validate`, `studio_patch` and `studio_plan`, and `studio_save` runs it too. So a save and a plan store and approve one manifest. Nothing is added in silence: the diff has one line for each object (before the op lines), the result has `added_actions`, and the plan card has a line `Record actions added`. When the additions would pass the 24,000 character limit, the check adds nothing. The result has `record_actions_skipped`, and the plan card says that the page cannot list those objects until the app is split. When the plan adds actions, its result returns the completed manifest and says to save it before a publish. `studio_publish` and `studio_outline` do not call the check. `_STUDIO_RULES` and the agent instructions say it.

## Done when

`uv run pytest tests/unit/test_studio_record_actions_beta2.py`. **R7 fence:** `test_validate_adds_the_five_actions`, `test_patch_that_adds_an_object_adds_its_five_actions`, `test_the_plan_card_names_the_added_actions`, `test_an_action_with_a_set_does_not_count` and `test_save_then_plan_then_publish_passes_with_one_card` fail when the check goes.
