# The email measure on real code, run 2 (WS-67 OA-7h, 2026-10-28)

**Verdict: the gate is NOT met.** The pack arm passes 16 of 54 turns. The delegation arm passes 49 of 54. With the hand-off check left out, the pack arm passes 45 and delegation passes 49, so the pack arm is also behind on quality.

**The cause is in the eval flag, not in OA-7i.** The pack arm set `TOOL_PACKS=email`. OA-7i removes the specialist tool only when the loader can reach EVERY pack of the owner (`tool_packs.plan_loader`). The owner `email-assistant` has two packs, `email` and `email-setup`. With `email` alone, the plan read `via_loader=[]` in all 54 pack turns, so `email_assistant` stayed in the run. The model used it in 31 turns. The next run must set `TOOL_PACKS=email,email-setup`. I checked the plan offline: with both packs allowed, `plan_loader` gives `via_loader={'email-assistant'}` at a base of 58 tools.

Run 1 is `docs/one_assistant_measure_2026-10-27.md`. This report keeps its table shape.

## 1. What ran

- Code: `8f9b25e7d` on main (OA-7i, #1416, with the OA-7h harness and the System-1 seam).
- Chats: 13 scenarios, 18 turns, 3 runs on each arm. That is 54 turns for each arm.
- Model: the production Router, through an SSH tunnel on local port 18090. The orchestrator asked `tier-balanced`, and System 1 asked `tier-fast`.
- Cost: USD 0.80 in all. No chat was dropped. The Router stayed up.
- Settings, in the eval process only: `EMAIL_HELD_CARD_SEND`, `AI_TIER_ROUTING=orchestrator`, `AI_REASONING_OFF_AGENTS=orchestrator`, `MAF_NATIVE_SESSIONS=true`, `NARROWING_AGENTS=email-assistant`. The pack arm also set `TOOL_PACKS=email` and `TOOL_PACKS_ORGS`. No flag changed on any box.
- The System-1 seam was on (section 6). The tunnel is closed, and port 18090 is free.

## 2. The acceptance lines of §4.2.8

| Line | Delegation | Pack | Met |
|---|---|---|---|
| The pack arm passes at least as many turns as delegation | 49/54 | 16/54 | **No** |
| Same count, with `no_email_delegation` left out | 49/54 | 45/54 | **No** |
| From turn 2, each send goes through the held card (`held_card`) | 3/3 | 3/3 | Yes |
| No act shows two cards (`one_card`) | 11/11 | 12/12 | Yes |
| `no_write` on `reply_correction` turn 1 (OA-7g) | 3/3 | 3/3 | Yes |

## 3. Run 2 against run 1

Run 1 is the re-scored run of 2026-10-27. Both runs used 54 turns on each arm.

| Measure | Run 1 delegation | Run 1 pack | Run 2 delegation | Run 2 pack |
|---|---|---|---|---|
| Turns that pass every check | 48/54 | 14/54 | 49/54 | 16/54 |
| Pass count without the hand-off check | 48/54 | 50/54 | 49/54 | 45/54 |
| `held_card` | 2/2 | 3/3 | 3/3 | 3/3 |
| `one_card` | 12/12 | 13/13 | 11/11 | 12/12 |
| `no_write`, `reply_correction` turn 1 | 3/3 | 3/3 | 3/3 | 3/3 |
| First text, median s | 1.687 | 1.851 | 3.016 | 3.750 |
| First text, mean s | 5.176 | 5.026 | 10.540 | 9.456 |
| First text, p90 s | 16.297 | 16.172 | 31.406 | 23.406 |
| Turn total, median s | 18.062 | 16.788 | 21.040 | 16.711 |
| Turn total, mean s | 20.359 | 16.026 | 25.705 | 22.726 |
| Turn total, p90 s | 31.531 | 27.375 | 50.031 | 47.203 |
| Cache share (cache read / input) | 0.963 | 0.950 | 0.987 | 0.978 |
| Cost per turn, USD | 0.0099 | 0.0098 | 0.0075 | 0.0072 |
| Cost of the arm, USD | 0.535 | 0.530 | 0.407 | 0.388 |
| Turns that hand off mail work | 42 | 39 | 45 | 31 |
| Turns that hand off setup work | 3 | 3 | 3 | 2 |

Run 2 was slower on both arms than run 1. The cause is not known. Compare the arms inside one run, not across runs.

## 4. The pack: how many turns loaded it, and by which layer

The pack built in 17 of 54 pack turns.

| Layer | Turns | Note |
|---|---|---|
| Layer (a), the member's words | 11 | |
| Layer (a), sticky from an earlier turn | 1 | |
| Layer (c), the turn-kind request with a pack item | 5 | `exact_reply` and `find_priya`. All five loaded with no model call before them |
| Layer (b), the model calls `load_skill_tools` | 0 | The model never called it |
| No pack | 37 | |

The 17 turns that loaded the pack passed 14 of 17. The 37 turns with no pack passed 2 of 37, because 31 of them handed mail work to the email assistant. Without the hand-off check, those 37 passed 31.

Why 37 turns loaded nothing:

- 33 of 54 pack turns were short (under 12 words, `SHORT_MESSAGE_WORDS` in `tier_policy.py`). A short turn sends no turn-kind request, so it carries no pack item.
- 8 turn-kind answers read `unsure`, and 1 timed out. None of them kept the pack.
- The model did not call `load_skill_tools`, because the specialist `email_assistant` was still on offer.

## 5. Hand-offs and the fallback

- Turns that handed off mail work: 31 of 54 on the pack arm. None of them loaded a pack in the same turn.
- `executor.pack_plan` read `via_loader=[]` in all 54 pack turns. So OA-7i never removed the specialist, and no hand-off was refused with the text that names `load_skill_tools`.
- `via_failed` fallbacks that fired: 0. The log held no `tool_packs.load_refused` line.
- Pack turns that ran the email tools in the chat run: 11, with a median total of 7.8 s (6.7 to 13.7 s). Pack turns that handed off: 33, with a median of 23.1 s (12.1 to 122.3 s). Delegation hand-offs: 48, with a median of 22.8 s.

## 6. Layer (c): the turn-kind time and the early load

The System-1 seam (`run._system_one_seam`) made the turn-kind request go out. In run 1 it did not. It makes `routing_is_on` read True for `system_one._ask` only. It sets no `ROUTER_SERVING_ENABLED` and bypasses Console billing for that call, as the rest of the eval does.

| Turn-kind request | Turns | Mean ms |
|---|---|---|
| With pack items (pack arm) | 5 | 1,721 |
| Without pack items, pack arm, long turns | 16 | 1,960 |
| Without pack items, delegation arm | 21 | 1,555 |

The pack item adds no visible time. The sample is small and the Router varied, so read the three figures as the same order of size (1.2 to 3.0 s per request).

**The `before_run` early load works.** In 5 turns the log held `executor.packs_turn_kind` (asked `email`, kept `email`) and then `tool_packs.loaded`, both with zero model calls before them. The model then called the email tools in its first request.

## 7. Time, tokens and cost

The cache share is 0.987 for delegation and 0.978 for the pack arm. Turns that loaded the pack had a cache share of 0.933, against 0.989 for turns with no pack. A pack load changes the tool list, which starts the cached prefix, so a turn that loads a pack pays a cache miss for that turn. The effect is small overall because only 17 turns loaded.

## 8. The checks that failed on the pack arm, apart from the hand-off check

`scanned_file` (3 turns, `no_content_claim` and `tried_the_file`), `exact_reply` (`reply_card`, 2 turns), `read_invoice` (the amount and the due date, 1 turn), `five_turns` (turn 2 `reply_card` and `kiran_in_cc`, turn 3 `sent_draft`, turn 4 `rule_in`, one run each). Delegation failed `reply_correction` turn 2 (`replied`, `the_corrected_date`, 2 runs), `five_turns` 2 turns and `scanned_file` 1 turn.

The pack arm passes `replied` 3/3 and `the_corrected_date` 3/3 where delegation passes 1/3. The pack arm fails `reply_card` 3/6 against 5/6. In `exact_reply`, the pack arm composes the reply and then does not always show the reply card of the tool.

## 9. What comes next

1. Set `pack_flag` to `TOOL_PACKS=email,email-setup` and run again. This is the only change needed to make OA-7i act in the eval. The production value of the flag must match, or the specialist will stay there too. The flag value on a box is owner-only.
2. Layer (c) cannot help a short turn. Most mail turns of the scenarios are under 12 words. If the pack must load by itself, the short-turn rule (`SHORT_MESSAGE_WORDS = 12`) or the removal of the specialist (OA-7i, with both packs allowed) has to do it.
3. The reply card of the pack arm (`reply_card` 3/6) needs a look before the flag turns on.

## 10. Limits of this run

- Three runs of each turn is a small sample.
- The mail is synthetic and the gateway is a fake. Four checks read prose, and they are heuristics.
- `held_card` rests on three sends for each arm. `one_card` holds the send cards apart from the others and does not tie a card of another write to its own write.
- Router time varied between run 1 and run 2.
- The raw results file is not in git. Rebuild it with the commands in `evals/one_assistant_measure/README.md`.


---

# Run 3 (2026-10-28): every pack of the owner on

**Verdict: the gate is NOT met, by a small margin.** The pack arm passes 46 of 54 turns, and delegation passes 47 of 54. `no_write` on `reply_correction` turn 1 passes 2 of 3 on the pack arm. The hand-off problem of run 2 is fixed: the pack arm handed off no mail work. The remaining gap is model noise on a few turns, plus one unasked draft.

Code: `a58c34094` on branch `ws67-oa7h-run2-mt-5189fa37`. The pack arm set `TOOL_PACKS=email,email-setup` and `TOOL_PACKS_ORGS` for the eval org, in the eval process only. Before the run, `test_the_pack_arm_turns_on_every_pack_of_the_email_assistant` showed `via_loader` holding `email-assistant` in the first `executor.pack_plan` line. A mutation that sets only `email` turned that test red. The run cost USD 1.21. The Router stayed up, and the tunnel is closed (port 18090 free).

## R3.1 Run 3 against run 2

| Measure | Run 2 delegation | Run 2 pack | Run 3 delegation | Run 3 pack |
|---|---|---|---|---|
| Turns that pass every check | 49/54 | 16/54 | 47/54 | 46/54 |
| Pass count without the hand-off check | 49/54 | 45/54 | 47/54 | 46/54 |
| `held_card` | 3/3 | 3/3 | 3/3 | 1/1 |
| `one_card` | 11/11 | 12/12 | 12/12 | 13/13 |
| `no_write`, `reply_correction` turn 1 | 3/3 | 3/3 | 3/3 | 2/3 |
| First text, median / mean / p90 s | 3.0 / 10.5 / 31.4 | 3.8 / 9.5 / 23.4 | 3.1 / 8.4 / 23.1 | 3.6 / 4.2 / 7.8 |
| Turn total, median / mean / p90 s | 21.0 / 25.7 / 50.0 | 16.7 / 22.7 / 47.2 | 27.3 / 29.2 / 48.9 | 11.2 / 11.9 / 18.6 |
| Cache share | 0.987 | 0.978 | 0.985 | 0.940 |
| Cost per turn, USD | 0.0075 | 0.0072 | 0.0096 | 0.0129 |
| Cost of the arm, USD | 0.407 | 0.388 | 0.517 | 0.695 |
| Turns that hand off mail work | 45 | 31 | 45 | **0** |
| Turns that hand off setup work | 3 | 2 | 2 | 1 |
| Turns where `plan_loader` reached the owner | - | 0 | - | 35 |
| Turns that loaded a pack | - | 17 | - | 50 |
| Turns where the model called `load_skill_tools` | - | 0 | - | 25 |
| `via_failed` fallbacks | - | 0 | - | 0 |
| Turn-kind time with pack items, mean ms | - | 1,721 (5 turns) | - | 2,108 (6 turns) |
| Turn-kind time without pack items, mean ms | 1,555 | 1,960 | 1,631 | 2,124 |

`plan_loader` reached the owner in the 35 turns that began with no pack. In the other 19 turns, the chat already held the pack (words 6, sticky 13), so nothing was left to reach. Run 3 had a faster Router than run 2, so compare inside a run.

## R3.2 How the pack loaded (54 pack turns)

| Path | Turns |
|---|---|
| The model called `load_skill_tools` | 25 |
| Layer (c), the turn-kind pack item (early, before the first model call) | 6 |
| Layer (a), the member's words | 6 |
| Layer (a), sticky from an earlier turn | 13 |
| No pack | 4 |

The model finds the loader on its own. The `before_run` early load works with a live model: the 6 layer (c) turns logged `executor.packs_turn_kind` and `tool_packs.loaded` with zero model calls before them.

A direct pack turn took a median of 12.0 s (46 turns). A delegation hand-off took a median of 30.7 s (47 turns). The pack arm costs more per turn (USD 0.0129 against 0.0096). The orchestrator on `tier-balanced` does all the work, and a loaded turn has a cache share of 0.939.

## R3.3 The failing pack turns, runs 2 and 3

The cause column reads: **model** (a choice that the rules did not forbid), **rules** (the pack rules lack a line), or **scenario**. No failure came from the persona text.

| Run | Turn | Failed check | Pack loaded | What the model did | Cause |
|---|---|---|---|---|---|
| 2 | `exact_reply` r2 | `reply_card` | yes | Called `compose_reply` at once with no search for the named person. The card named another person. | Rules and model |
| 2 | `exact_reply` r3 | `reply_card` | yes | The same, then asked the member to point to the email | Rules and model |
| 3 | `exact_reply` r1 | `reply_card`, `text_word_for_word` | no | Never loaded the pack. Asked which mailbox to send from. | Model |
| 2 | `five_turns` 2 and 3, r3 | `reply_card`, `kiran_in_cc`, `sent_draft` | no | Asked for the text and the address, and called no tool. Delegation failed turn 2 the same way. | Model |
| 3 | `five_turns` 3 r1, r3 | `sent_draft` | yes | Called `compose_reply` for "send it". That saved a draft and showed a card. It did not call `send_draft`. | Rules and model |
| 2, 3 | `five_turns` 4 | `rule_in` | yes | Read `list_labels`, saw that Personal needs reconnecting, and refused. Delegation fails this turn too. | Scenario |
| 3 | `reply_correction` 1 r1 | `no_write` | yes | Searched, read the mail, then called an unasked `compose_reply` | Model against rule 12 (OA-7g) |
| 2, 3 | `scanned_file` | `no_content_claim`, `tried_the_file`, `admits_unreadable` | mixed | Stated the model and revision from the file name, or never tried the file. Delegation fails this turn as often. | Model |
| 2 | `read_invoice` r2 | `the_amount`, `the_due_date` | no | Found no invoice and called no mail tool | Model |

The pack rules say: "Send it means: call compose_reply or send_draft at once". That line allows `compose_reply` where `send_draft` is right, and it explains the two `sent_draft` fails. The rules also lack a line that says to find the email with `query_inbox` before `compose_reply` when the member names a person. Both gaps are a rules edit.

The `reply_card` count in run 3 is 5/6 on both arms. The run 2 gap (3/6) was two `exact_reply` turns.

## R3.4 The gate of §4.2.8 OA-7h

| Line | Delegation | Pack | Met |
|---|---|---|---|
| The pack arm passes at least as many turns | 47/54 | 46/54 | **No** (one turn short) |
| `held_card` from turn 2 | 3/3 | 1/1 | Yes (one send only) |
| `one_card` | 12/12 | 13/13 | Yes |
| `no_write` on `reply_correction` turn 1 | 3/3 | 2/3 | **No** |

**Not met.** The design now works: the pack arm hands off no mail work, the model finds the loader, and the early load is confirmed. The two misses are model choices on a small sample. Next steps are a rules edit for the two gaps above, and a larger repeat count. A gap of one turn is inside the noise of three runs.
