# The email measure on real code (WS-67 OA-7h, 2026-10-27)

**Verdict: the gate is NOT met.** The pack arm passes 14 of 54 turns. The delegation arm passes 48 of 54. The cause is one check, `no_email_delegation`. In 42 of 54 pack turns the run loaded no pack, and the model handed the mail work to the email assistant. Layer (c) did not run, because the System-1 request was unavailable in this eval. The quality of the answers is equal on both arms.

Spec: `project-docs/specs/one_assistant_context.md` §4.2.8, slice OA-7h. The first report is `docs/one_assistant_measure_2026-10-25.md`. This report keeps its table shape.

## 1. What ran

- Code: commit `c2de3c5b0` (OA-7f on main, `487b77dbd`, merged in).
- Chats: 13 scenarios, 18 member turns, 3 runs on each arm. That is 54 turns for each arm and 108 in all.
- Model: the production Router, through an SSH tunnel on local port 18090. The orchestrator asked `tier-balanced`.
- Cost: USD 1.06 in all. No chat was dropped, and the Router stayed up.
- Settings, in the eval process only: `EMAIL_HELD_CARD_SEND`, `AI_TIER_ROUTING=orchestrator`, `AI_REASONING_OFF_AGENTS=orchestrator`, `MAF_NATIVE_SESSIONS=true` and `NARROWING_AGENTS=email-assistant`. The pack arm also set `TOOL_PACKS=email` and `TOOL_PACKS_ORGS` for the eval org. No flag changed on any box.
- Mark store: the measure installed the in-memory store. The run stops when it does not install.
- Production context, measured 2026-10-27: 7 direct turns took 8 to 16 s. Six turns handed to a specialist took 78 to 259 s.

## 2. The acceptance lines

| Line of §4.2.8 | Delegation | Pack | Met |
|---|---|---|---|
| The pack arm passes at least as many turns as delegation | 48/54 | 14/54 | **No** |
| Same count, with `no_email_delegation` left out | 48/54 | 50/54 | Yes |
| From turn 2, each send goes through the held card (`held_card`) | 2/2 | 3/3 | Yes |
| No act shows two cards (`one_card`) | 12/12 | 13/13 | Yes |
| `no_write` on `reply_correction` turn 1 (OA-7g) | 3/3 | 3/3 | Yes |

Only three turns send mail, so `held_card` has few cases. One delegation chat did not send, and `sent_draft` failed there.

## 3. Why the pack arm fails the gate

The pack loaded in 12 of 54 pack turns. In all 12, the member's words matched (layer a). In 0 turns did layer (c) load it. In 0 turns did the model call `load_skill_tools` in a way that loaded it.

In the 42 other turns, the loader was on offer, and the specialist tool `email_assistant` stayed in the list. The model used the specialist. 39 turns handed mail work to the email assistant, and each one fails `no_email_delegation`. Three turns handed setup work, and they pass.

| Pack arm | Turns | Hand-off of mail work |
|---|---|---|
| Pack loaded before the first call | 12 | 0 |
| No pack loaded | 42 | 39 |

So the pack arm of a live model mostly behaves as delegation, with the pack idle. The fix is in the loader, not in the checks. Layer (c) is the planned fix, and it did not run (section 6).

## 4. Time, tokens and cost

| Measure | Delegation | Pack |
|---|---|---|
| Turns that pass every check | 48/54 | 14/54 |
| Checks that pass | 162/170 | 178/223 |
| Turns that ended in an error | 0 | 0 |
| First text, median s | 1.687 | 1.851 |
| First text, mean s | 5.176 | 5.026 |
| First text, p90 s | 16.297 | 16.172 |
| Turn total, median s | 18.062 | 16.788 |
| Turn total, mean s | 20.359 | 16.026 |
| Turn total, p90 s | 31.531 | 27.375 |
| Model calls per turn | 4.741 | 4.63 |
| Input tokens per turn | 140,943 | 138,783 |
| Of them, from the cache | 135,692 | 131,783 |
| **Cache share (cache read / input)** | **0.963** | **0.950** |
| Output tokens per turn | 1,746 | 1,155 |
| Cost per turn, USD | 0.0099 | 0.0098 |
| Cost of the arm, USD | 0.535 | 0.530 |

The pack arm is not faster here, because 39 of its turns still hand off. The six pack turns that ran the email tools in the chat run took a median of 7.4 s (5.7 to 9.4 s). The 42 pack turns that handed off took a median of 17.6 s (9.0 to 33.9 s). On the delegation arm, 45 hand-offs took a median of 18.6 s (8.5 to 155.5 s).

## 5. The cache share

The cache share is 0.963 for delegation and 0.950 for the pack arm. The gap is 1.3 points. A pack load changes the tool list, and the tool list starts the cached prefix. Here the loads were rare (12 turns), so this run does not show a large cost. A run in which the pack loads in most turns would show the true cost.

## 6. The `email-setup` gap, layer (c) and the early load

**The setup gap.** The `email` pack alone does not hold the setup tools. Setup work goes to the email assistant, and the check allows it. 3 of 54 turns on each arm did this (the rule in `five_turns` turn 4). The cost per turn was USD 0.013 on delegation and USD 0.021 on the pack arm. The sample is three turns, so read it as an order of size.

**Layer (c), the turn-kind time.** The report cannot give the time with and without pack items. The turn-kind request is a System-1 request. In 42 of 108 turns it answered `unavailable` (`latency_ms` 0 to 16). The cause: `system_one._ask` returns `router_off` when `routing_is_on()` is false. In production it is true, and in this run it was false, because the eval has no Console. The other 66 turns were short, and a short turn sends no request (fewer than 12 words, `SHORT_MESSAGE_WORDS`). The log event `executor.packs_turn_kind` fired in 0 turns. **Follow-up, added after run 1:** the eval now has a System-1 seam (`run._system_one_seam`). It makes `routing_is_on` read True for `system_one._ask` only, and it points that client at the same Router on `tier-fast`. It sets no `ROUTER_SERVING_ENABLED` and bypasses Console billing for that call, as the rest of the eval does. Run 1 did NOT have the seam.

**The `before_run` early load.** Not shown. The 12 loads before the first model call were all layer (a), with `layers={'email': 'words'}`. The `load_early` path of OA-7f needs a turn-kind answer with pack items, and none came.

## 7. The checks that failed, apart from `no_email_delegation`

| Turn | Delegation | Pack | Note |
|---|---|---|---|
| `scanned_file` | `tried_the_file` 1 fail, `no_content_claim` 1 fail | the same | Same on both arms |
| `reply_correction` turn 2 | `replied`, `the_corrected_date` 2 fails | the same 2 fails | The model said it would draft, then did not save one |
| `five_turns` turn 3 | `sent_draft` 1 fail | none | |
| `navy_replay` | `parts_from_body` 1 fail | none | |

The failures that both arms share are the model's choice, not the pack's.

## 8. What comes next (updated after run 1)

The fix is OA-7i. With packs on, an owner's specialist tool leaves the chat when the loader can reach every pack of that owner. The live measure runs again after OA-7i, with the System-1 seam. The list below is the first reading of run 1.

1. The pack arm needs the model to load the pack. Without layer (c), 42 of 54 turns did not. Options: run layer (c) in the eval (needs `ROUTER_SERVING_ENABLED` in the eval process), or drop the specialist tool `email_assistant` from a run that has the loader offered.
2. The flag `TOOL_PACKS` stays off for every organization. OA-7h is the gate, and it is not met.
3. The `keeps_reasoning` option stays unset. The reply checks are equal on both arms.

## 9. Limits of this run

- Three runs of each turn is a small sample. Four turns decide the difference between 14 and 50.
- The mail is synthetic and the gateway is a fake. Four checks read prose, and they are heuristics.
- `held_card` rests on three sends for each arm.
- The Router and the model were the live ones of 2026-10-27. Times vary with load.
- The raw results file is not in git. Rebuild it with the commands in `evals/one_assistant_measure/README.md`.
