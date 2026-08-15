# Pull-request history — inherited from CommandCenter

This repository is a full-history mirror of `FracktalWorks/CommandCenter`. GitHub cannot transfer pull-request
conversations between repositories outside GitHub Enterprise Cloud, so all **449 pull requests** are archived here instead:

- **Code** — every PR's head commit is preserved as a git ref, `refs/archive/pr/<number>`.
  Fetch them with `git fetch origin 'refs/archive/pr/*:refs/archive/pr/*'`.
- **Conversation** — titles, descriptions, reviews, code-review comments and discussion
  are in the per-PR files below, with the raw API payloads under `../raw/`.

437 of 449 were merged. PR numbers refer to the *original* CommandCenter
repository and do not continue into this repository's own numbering.

| PR | Title | State | Author | Created | Merge commit | Archived ref |
|---|---|---|---|---|---|---|
| [#1](pr-0001.md) | chore(skills): upstream sync 28777107597 | `merged` | @github-actions[bot] | 2026-06-01 | `492552ea` | `refs/archive/pr/1` |
| [#2](pr-0002.md) | [WIP] Fix failing GitHub Actions job Deploy to Hostinger | `closed` | @Copilot | 2026-06-13 | `11cf2fea` | `refs/archive/pr/2` |
| [#3](pr-0003.md) | [WIP] Copilot Request | `closed` | @Copilot | 2026-06-19 | `0bc1ad44` | `refs/archive/pr/3` |
| [#4](pr-0004.md) | fix(email): make Outlook/Gmail mail show up in the email app | `merged` | @vjvarada | 2026-06-20 | `9c9d0b46` | `refs/archive/pr/4` |
| [#5](pr-0005.md) | fix(email,memory): reconnect UI for stale OAuth + route mem0 through LiteLLM tier | `merged` | @vjvarada | 2026-06-20 | `26af0471` | `refs/archive/pr/5` |
| [#6](pr-0006.md) | fix(email): correct OAuth post-auth redirect (404 after reconnect) | `merged` | @vjvarada | 2026-06-20 | `abf494fe` | `refs/archive/pr/6` |
| [#7](pr-0007.md) | Make email app a fully-featured, two-way-synced client | `merged` | @vjvarada | 2026-06-20 | `2a9c2926` | `refs/archive/pr/7` |
| [#8](pr-0008.md) | docs: consolidate ai-company-brain planning folder (15 → 5 + specs/) | `merged` | @vjvarada | 2026-06-20 | `ebf0c136` | `refs/archive/pr/8` |
| [#9](pr-0009.md) | fix(deploy): apply Postgres migrations 02+ on deploy (fixes email 500) | `merged` | @vjvarada | 2026-06-20 | `e969e92f` | `refs/archive/pr/9` |
| [#10](pr-0010.md) | fix(db): drop invalid mcp_servers seed unblocking email migration | `merged` | @vjvarada | 2026-06-20 | `72a34c68` | `refs/archive/pr/10` |
| [#11](pr-0011.md) | feat(email): surface stale-OAuth reconnect banner in the email app | `merged` | @vjvarada | 2026-06-20 | `e8b261d3` | `refs/archive/pr/11` |
| [#12](pr-0012.md) | feat(email): trip reconnect banner immediately on live-call 401 | `merged` | @vjvarada | 2026-06-20 | `4c8deaba` | `refs/archive/pr/12` |
| [#13](pr-0013.md) | fix(agents): prune registry, correct MAF/Copilot SDK labels, fix chat picker scroll | `merged` | @vjvarada | 2026-06-21 | `498c7762` | `refs/archive/pr/13` |
| [#14](pr-0014.md) | feat(loader): auto-install agent dependencies into the shared venv | `merged` | @vjvarada | 2026-06-22 | `c8024fd6` | `refs/archive/pr/14` |
| [#15](pr-0015.md) | fix(settings/llm): persist model visibility + tiers in Postgres | `merged` | @vjvarada | 2026-06-23 | `d4a4343e` | `refs/archive/pr/15` |
| [#16](pr-0016.md) | Task Manager: capture story feature-complete — sync pull, AI clarify + agent rail, atomizer/dedup, email→task, settings | `merged` | @vjvarada | 2026-07-03 | `70ff26e4` | `refs/archive/pr/16` |
| [#17](pr-0017.md) | feat(tasks): clarify × ClickUp live upgrades, dense inbox list, capture attachments | `merged` | @vjvarada | 2026-07-04 | `de511f17` | `refs/archive/pr/17` |
| [#18](pr-0018.md) | Dev-velocity tooling: complexity/correctness gates, code-graph MCP, weekly code-health review | `merged` | @vjvarada | 2026-07-04 | `f1e29ba1` | `refs/archive/pr/18` |
| [#20](pr-0020.md) | Redesign Reply Zero as Rapid Inbox (category browser, inline reply, keyboard triage) | `merged` | @vjvarada | 2026-07-06 | `0e910807` | `refs/archive/pr/20` |
| [#21](pr-0021.md) | Rapid Inbox: read existing classification + reuse the draft flow | `merged` | @vjvarada | 2026-07-06 | `c7e8748c` | `refs/archive/pr/21` |
| [#22](pr-0022.md) | feat(email): default reply-all with toggle in Rapid Inbox, EmailDetail, toolbar, and ConversationView | `merged` | @vjvarada | 2026-07-06 | `b051edbe` | `refs/archive/pr/22` |
| [#23](pr-0023.md) | Observability: live cross-app agent & model activity, cost, and 8-bit office (E2 Phases 5–6.2) | `merged` | @vjvarada | 2026-07-10 | `a7c23b4c` | `refs/archive/pr/23` |
| [#24](pr-0024.md) | Observability: fix blank page (auth gate) + durable History view | `merged` | @vjvarada | 2026-07-10 | `343291c1` | `refs/archive/pr/24` |
| [#25](pr-0025.md) | Observability: pixel-art agent office (desks, sleeping, war-room table) | `merged` | @vjvarada | 2026-07-10 | `34a607ad` | `refs/archive/pr/25` |
| [#26](pr-0026.md) | feat(email): full-HTML signature, Reply/Reply-All in draft card, honest Fix | `merged` | @vjvarada | 2026-07-10 | `e163c7b8` | `refs/archive/pr/26` |
| [#27](pr-0027.md) | fix(evals): pin window in oversized-message compression eval (HH-1 drift) | `merged` | @vjvarada | 2026-07-10 | `3ecfbbe0` | `refs/archive/pr/27` |
| [#28](pr-0028.md) | feat(email-assistant): one integrated interactive email list in chat | `merged` | @vjvarada | 2026-07-10 | `6455ce0a` | `refs/archive/pr/28` |
| [#29](pr-0029.md) | Observability: roomed, layered, configurable agent scenes (+ Higgsfield seam) | `merged` | @vjvarada | 2026-07-10 | `41d80cb9` | `refs/archive/pr/29` |
| [#30](pr-0030.md) | Email: fix reply threading + signature, categorized AGUI board, Rapid Inbox Cc fix, and consolidate chat tools (64→41) | `merged` | @vjvarada | 2026-07-11 | `0c9f5c59` | `refs/archive/pr/30` |
| [#31](pr-0031.md) | Conference breathing animation + reusable character sprite library | `merged` | @vjvarada | 2026-07-11 | `165e2e06` | `refs/archive/pr/31` |
| [#32](pr-0032.md) | Move avatar assignment to Agents page + mobile office fixes | `merged` | @vjvarada | 2026-07-12 | `6dc0f42b` | `refs/archive/pr/32` |
| [#33](pr-0033.md) | feat(office): office cast + test-coder in avatar picker, breathing fixes, 11 new avatars | `merged` | @vjvarada | 2026-07-12 | `38fccb92` | `refs/archive/pr/33` |
| [#34](pr-0034.md) | docs: competitive analysis (Hermes/OpenClaw) + documentation reconciliation & consolidation | `merged` | @vjvarada | 2026-07-13 | `4d4db2a7` | `refs/archive/pr/34` |
| [#35](pr-0035.md) | chore(skills): upstream sync 29229743467 | `merged` | @github-actions[bot] | 2026-07-13 | `6c86ad51` | `refs/archive/pr/35` |
| [#36](pr-0036.md) | fix(copilot): BYOK internal-token precedence + de-hardcode tier/model refs | `merged` | @vjvarada | 2026-07-13 | `51d201d3` | `refs/archive/pr/36` |
| [#37](pr-0037.md) | feat(agents): editable display-name aliases + avatar logos across the UI | `merged` | @vjvarada | 2026-07-13 | `311a989a` | `refs/archive/pr/37` |
| [#38](pr-0038.md) | fix(agents): tighter avatar head-crop, live avatar refresh, chat display-name | `merged` | @vjvarada | 2026-07-13 | `bc5a1a4c` | `refs/archive/pr/38` |
| [#39](pr-0039.md) | Reorganize apps/ by lifecycle + fix agent avatar framing | `merged` | @vjvarada | 2026-07-13 | `32e07f19` | `refs/archive/pr/39` |
| [#40](pr-0040.md) | feat(tasks): email capture popup, delete/archive overhaul + founder prioritization matrix | `merged` | @vjvarada | 2026-07-13 | `84ac632f` | `refs/archive/pr/40` |
| [#41](pr-0041.md) | fix(tasks): make ClickUp two-way sync actually two-way | `merged` | @vjvarada | 2026-07-13 | `d5f57a25` | `refs/archive/pr/41` |
| [#42](pr-0042.md) | feat(tasks): priority as suggestion badges, not status buckets | `merged` | @vjvarada | 2026-07-13 | `1bca3a4f` | `refs/archive/pr/42` |
| [#43](pr-0043.md) | feat(tasks): 7 priority levels, plain labels, grouped Priority/Engage views | `merged` | @vjvarada | 2026-07-13 | `c79fb465` | `refs/archive/pr/43` |
| [#44](pr-0044.md) | fix(tasks): delegate badge no longer says 'Delegate to <me>?' | `merged` | @vjvarada | 2026-07-14 | `fdb3779a` | `refs/archive/pr/44` |
| [#45](pr-0045.md) | feat(tasks): map ClickUp statuses to the 4 fixed Next-Actions stages | `merged` | @vjvarada | 2026-07-14 | `0696ed18` | `refs/archive/pr/45` |
| [#46](pr-0046.md) | fix(tasks): keep completed tasks in the Done column until archived | `merged` | @vjvarada | 2026-07-14 | `ba9dc59b` | `refs/archive/pr/46` |
| [#47](pr-0047.md) | feat(tasks): bulk select → archive/delete, back-propagated to ClickUp | `closed` | @vjvarada | 2026-07-14 | `695b4baf` | `refs/archive/pr/47` |
| [#48](pr-0048.md) | feat(tasks): Priority = assigned-to-me only; @context pills on Next Actions | `closed` | @vjvarada | 2026-07-14 | `522f2a50` | `refs/archive/pr/48` |
| [#49](pr-0049.md) | feat(tasks): bulk select → archive/delete, back-propagated to ClickUp | `merged` | @vjvarada | 2026-07-14 | `634e30ad` | `refs/archive/pr/49` |
| [#50](pr-0050.md) | feat(tasks): Priority = assigned-to-me only; @context pills on Next Actions | `merged` | @vjvarada | 2026-07-14 | `f53a0356` | `refs/archive/pr/50` |
| [#51](pr-0051.md) | feat(tasks): My Next Actions = section header with Context / Priority / Engage slices | `merged` | @vjvarada | 2026-07-14 | `cec9e129` | `refs/archive/pr/51` |
| [#52](pr-0052.md) | feat(tasks): priority pill on every card; drop redundant Urgency sort | `merged` | @vjvarada | 2026-07-14 | `8dbdb81f` | `refs/archive/pr/52` |
| [#53](pr-0053.md) | feat(tasks): columnar list view for Next Actions (desktop) + column settings | `merged` | @vjvarada | 2026-07-14 | `8071aa03` | `refs/archive/pr/53` |
| [#54](pr-0054.md) | fix(tasks): Context view crashes with React #185 (infinite render loop) | `merged` | @vjvarada | 2026-07-14 | `77943c0c` | `refs/archive/pr/54` |
| [#55](pr-0055.md) | feat(tasks): Action Mode column + Priority default sort + status-grouping hint | `merged` | @vjvarada | 2026-07-14 | `15ac4f77` | `refs/archive/pr/55` |
| [#56](pr-0056.md) | feat(tasks): simplify Next Actions — Suggestion column, no assignee, single sidebar entry | `merged` | @vjvarada | 2026-07-14 | `e4ca9899` | `refs/archive/pr/56` |
| [#57](pr-0057.md) | feat(tasks): columns on every grouping + unified multi-select facet filter bar | `merged` | @vjvarada | 2026-07-14 | `a47eb150` | `refs/archive/pr/57` |
| [#58](pr-0058.md) | fix(deploy): propagate GATEWAY_INTERNAL_TOKEN to workbench .env.local | `merged` | @vjvarada | 2026-07-14 | `284b48b0` | `refs/archive/pr/58` |
| [#59](pr-0059.md) | feat(agui): eager generative UI, on-brand sandbox + interactivity, no duplicate pill, full reasoning | `merged` | @vjvarada | 2026-07-14 | `bc49a958` | `refs/archive/pr/59` |
| [#60](pr-0060.md) | feat(agents): workbench persistence, memory scopes, side-panel editor + report kit | `merged` | @vjvarada | 2026-07-15 | `a96c4b75` | `refs/archive/pr/60` |
| [#61](pr-0061.md) | feat(email): Outlook-style scoped search bar with filter pills + All folder | `merged` | @vjvarada | 2026-07-16 | `25c90784` | `refs/archive/pr/61` |
| [#62](pr-0062.md) | fix(agents): no-text graceful recovery + guaranteed standard toolset | `merged` | @vjvarada | 2026-07-16 | `f46f1cc5` | `refs/archive/pr/62` |
| [#63](pr-0063.md) | feat: HR-aware task manager (Phases 1-4) + Rapid Inbox / historical-apply email fixes | `merged` | @vjvarada | 2026-07-17 | `0efd3856` | `refs/archive/pr/63` |
| [#64](pr-0064.md) | fix(tasks): ClickUp provider verb-dispatch (deploy unit-gate fix) | `merged` | @vjvarada | 2026-07-17 | `d8132b37` | `refs/archive/pr/64` |
| [#65](pr-0065.md) | fix: real output ceiling (4096→32000) + un-clobber Copilot context compaction | `closed` | @vjvarada | 2026-07-17 | `bfff6822` | `refs/archive/pr/65` |
| [#66](pr-0066.md) | fix(context): budget history to the model's real window (was ~2% of it) | `merged` | @vjvarada | 2026-07-17 | `6c70a627` | `refs/archive/pr/66` |
| [#67](pr-0067.md) | feat(agents): make VS Code-authored Copilot agents first-class in MAF (+ fix MCP never reaching a session) | `merged` | @vjvarada | 2026-07-17 | `42aa74bb` | `refs/archive/pr/67` |
| [#68](pr-0068.md) | fix(llm): one source of truth for model limits (five disagreeing copies -> one) | `merged` | @vjvarada | 2026-07-17 | `2936fac4` | `refs/archive/pr/68` |
| [#69](pr-0069.md) | fix: real output ceiling (4096→32000) + clamp + un-clobber Copilot compaction (replaces #65) | `merged` | @vjvarada | 2026-07-17 | `fe103d55` | `refs/archive/pr/69` |
| [#70](pr-0070.md) | Task manager: Done button, sidebar counts, clarify UX, smart capture, sync visibility | `merged` | @vjvarada | 2026-07-17 | `ed07b3a2` | `refs/archive/pr/70` |
| [#71](pr-0071.md) | feat(calendar): timeboxing app — AI planner, roll-over, mobile scheduling, Now/Next, replan + fixed/flexible, actuals + end-of-day review | `merged` | @vjvarada | 2026-07-17 | `7a5c72b2` | `refs/archive/pr/71` |
| [#72](pr-0072.md) | feat(tasks): card actions — schedule/eliminate popups, right-click menus, card stage selector | `merged` | @vjvarada | 2026-07-18 | `95caf8ed` | `refs/archive/pr/72` |
| [#73](pr-0073.md) | fix(tasks): deterministic lowest-status reverse-map on synced stage move | `merged` | @vjvarada | 2026-07-18 | `a9ffeefc` | `refs/archive/pr/73` |
| [#74](pr-0074.md) | refactor(email): security fixes + provider factory, llm_json seam, AssistantView split (1/2) | `merged` | @vjvarada | 2026-07-19 | `8bb6f1ff` | `refs/archive/pr/74` |
| [#75](pr-0075.md) | refactor(email): invert ingestion↔gateway layering, converge post-sync pipeline, unify upserts (2/2) | `merged` | @vjvarada | 2026-07-19 | `53ac3cbe` | `refs/archive/pr/75` |
| [#76](pr-0076.md) | docs(impeccable): add flex-1 nav anti-pattern to the layout checklist | `merged` | @vjvarada | 2026-07-19 | `760ac794` | `refs/archive/pr/76` |
| [#77](pr-0077.md) | docs(workflows): visual workflow editor — analysis, architecture RFC + interactive mockup | `merged` | @vjvarada | 2026-07-19 | `f76ba588` | `refs/archive/pr/77` |
| [#78](pr-0078.md) | feat(email): Email Cleaner covers the whole mailbox, uncapped; folder-scoped inbox filters | `merged` | @vjvarada | 2026-07-19 | `35662621` | `refs/archive/pr/78` |
| [#79](pr-0079.md) | fix(email): stop the sweep blanket-labelling internal mail; honest restore on non-Gmail | `merged` | @vjvarada | 2026-07-20 | `f94cc508` | `refs/archive/pr/79` |
| [#80](pr-0080.md) | feat(email): backfills don't draft by default; rename Assistant → AI Settings in code | `merged` | @vjvarada | 2026-07-20 | `e8a02ce8` | `refs/archive/pr/80` |
| [#81](pr-0081.md) | fix(email): H5 — thread status parity across both rule paths, and let the classifier reach old mail | `merged` | @vjvarada | 2026-07-20 | `d332ce00` | `refs/archive/pr/81` |
| [#82](pr-0082.md) | fix(email): run Reply Zero classification every sync cycle, not only on new mail | `merged` | @vjvarada | 2026-07-20 | `67561f4d` | `refs/archive/pr/82` |
| [#83](pr-0083.md) | chore(skills): upstream sync 29722143213 | `merged` | @github-actions[bot] | 2026-07-20 | `e63acc82` | `refs/archive/pr/83` |
| [#84](pr-0084.md) | fix(email): repair the follow-up reminder scan, dead in production since it shipped | `merged` | @vjvarada | 2026-07-20 | `167af701` | `refs/archive/pr/84` |
| [#85](pr-0085.md) | fix(email): sign above the quoted thread, and stop the drafter quoting it back | `merged` | @vjvarada | 2026-07-20 | `9419f927` | `refs/archive/pr/85` |
| [#86](pr-0086.md) | fix(email): the client quote splitter must scan from line 0 too | `merged` | @vjvarada | 2026-07-20 | `6de46108` | `refs/archive/pr/86` |
| [#87](pr-0087.md) | fix(email): auto-drafting defaults OFF, including at the column level | `merged` | @vjvarada | 2026-07-20 | `0c98760d` | `refs/archive/pr/87` |
| [#88](pr-0088.md) | fix(email): live reply-drafting defaults back ON; only backfills stay opt-in | `merged` | @vjvarada | 2026-07-20 | `cd46be96` | `refs/archive/pr/88` |
| [#89](pr-0089.md) | feat(email): process past emails skips mail the rules already ran over | `merged` | @vjvarada | 2026-07-20 | `e2f77f26` | `refs/archive/pr/89` |
| [#90](pr-0090.md) | feat(email): rename the "Personal" sender category to "Conversation" | `merged` | @vjvarada | 2026-07-20 | `0ac99bfa` | `refs/archive/pr/90` |
| [#91](pr-0091.md) | feat(email): deterministic bulk signals + stop counting colleagues as backlog | `merged` | @vjvarada | 2026-07-20 | `2dd04694` | `refs/archive/pr/91` |
| [#92](pr-0092.md) | fix(email): the per-cycle sweep budget must bound writes, not reads | `merged` | @vjvarada | 2026-07-20 | `673d31ff` | `refs/archive/pr/92` |
| [#93](pr-0093.md) | feat(email): Clean older mail — fetch history, then categorize it with no AI | `merged` | @vjvarada | 2026-07-20 | `d9d48870` | `refs/archive/pr/93` |
| [#94](pr-0094.md) | fix(email): history hold-back was inert, and it hid mail from AI categorization | `merged` | @vjvarada | 2026-07-20 | `7f2e36a8` | `refs/archive/pr/94` |
| [#95](pr-0095.md) | feat(email): learned patterns need approval before the cleaner projects them | `closed` | @vjvarada | 2026-07-20 | `45672161` | `refs/archive/pr/95` |
| [#96](pr-0096.md) | feat(email): learned patterns need approval before the cleaner projects them | `merged` | @vjvarada | 2026-07-20 | `349bae67` | `refs/archive/pr/96` |
| [#97](pr-0097.md) | fix(email): the auto-learn gate enforced neither of the two bars it claimed | `merged` | @vjvarada | 2026-07-20 | `aa84617c` | `refs/archive/pr/97` |
| [#98](pr-0098.md) | fix(email): the learned-patterns review screen was unreadable | `merged` | @vjvarada | 2026-07-20 | `75d6bb77` | `refs/archive/pr/98` |
| [#99](pr-0099.md) | feat(email): rebuild Analytics around what the user can act on | `merged` | @vjvarada | 2026-07-20 | `bee5f1d7` | `refs/archive/pr/99` |
| [#100](pr-0100.md) | fix(email): rules that changed nothing no longer report that they did | `merged` | @vjvarada | 2026-07-21 | `5c11d48c` | `refs/archive/pr/100` |
| [#101](pr-0101.md) | fix(email): opening a Cleaner category now scopes everything under it | `merged` | @vjvarada | 2026-07-21 | `29f246fb` | `refs/archive/pr/101` |
| [#102](pr-0102.md) | fix(email): repair refused rule runs; stop calling classified mail uncategorized | `merged` | @vjvarada | 2026-07-21 | `949c9c19` | `refs/archive/pr/102` |
| [#103](pr-0103.md) | fix(email): a rule-run repair must never lift mail back out of the bin | `merged` | @vjvarada | 2026-07-21 | `2da974ca` | `refs/archive/pr/103` |
| [#104](pr-0104.md) | feat(email): auto-learn needs a second opinion, and only for bulk senders | `merged` | @vjvarada | 2026-07-21 | `9ec33e2e` | `refs/archive/pr/104` |
| [#105](pr-0105.md) | feat(email): a Fix now teaches the classifier instead of bypassing it | `merged` | @vjvarada | 2026-07-21 | `7d581e7c` | `refs/archive/pr/105` |
| [#106](pr-0106.md) | fix(email): stop AI "Draft with AI" 502 by extending proxy timeout | `merged` | @vjvarada | 2026-07-21 | `1670271a` | `refs/archive/pr/106` |
| [#107](pr-0107.md) | fix(email): the All folder is mail that arrived, not a log of everything | `merged` | @vjvarada | 2026-07-21 | `24ef00a0` | `refs/archive/pr/107` |
| [#108](pr-0108.md) | feat(email): group learned patterns by the force they actually carry | `merged` | @vjvarada | 2026-07-21 | `2ded0ab0` | `refs/archive/pr/108` |
| [#109](pr-0109.md) | fix(agents): a question isn't a failure - stop reporting parked turns as truncated | `merged` | @vjvarada | 2026-07-21 | `1f282331` | `refs/archive/pr/109` |
| [#110](pr-0110.md) | feat(email): a conversation has ONE classification, re-evaluated per message | `merged` | @vjvarada | 2026-07-22 | `3ad17868` | `refs/archive/pr/110` |
| [#111](pr-0111.md) | fix(email): an FYI status row alone does not make a thread a conversation | `merged` | @vjvarada | 2026-07-22 | `4f94aba9` | `refs/archive/pr/111` |
| [#112](pr-0112.md) | chore(email): one-off repair script for damaged conversation threads | `merged` | @vjvarada | 2026-07-22 | `87016fbc` | `refs/archive/pr/112` |
| [#113](pr-0113.md) | fix(email): the cleanup sweep must not label conversation messages | `merged` | @vjvarada | 2026-07-22 | `28941dba` | `refs/archive/pr/113` |
| [#114](pr-0114.md) | fix(email): Phase 1 - stop the lying (11 correctness fixes) | `merged` | @vjvarada | 2026-07-22 | `cf48f097` | `refs/archive/pr/114` |
| [#115](pr-0115.md) | refactor(email): Phase 2 batch 1 - safe convergence (embeddings/attachments/health) | `merged` | @vjvarada | 2026-07-22 | `669424d5` | `refs/archive/pr/115` |
| [#116](pr-0116.md) | refactor(email): 2.9 - one token-guarded JobTracker for both background jobs | `merged` | @vjvarada | 2026-07-22 | `9fd0fd51` | `refs/archive/pr/116` |
| [#117](pr-0117.md) | feat(email): 2.10 - sync resilience (backoff, orphan cleanup, category cache, 429) | `merged` | @vjvarada | 2026-07-22 | `fc11597a` | `refs/archive/pr/117` |
| [#118](pr-0118.md) | feat(email): 2.6 - dedupe Outlook re-keys by internet_message_id | `merged` | @vjvarada | 2026-07-22 | `80ea785a` | `refs/archive/pr/118` |
| [#119](pr-0119.md) | feat(email): 2.4 - drafts carry Cc/Bcc (kill composer full-send branch) | `merged` | @vjvarada | 2026-07-22 | `2ac6ea6d` | `refs/archive/pr/119` |
| [#120](pr-0120.md) | feat(email): 2.7 - digest is a projection (aggregates, self-exclusion, HTML) | `merged` | @vjvarada | 2026-07-22 | `72caea9f` | `refs/archive/pr/120` |
| [#121](pr-0121.md) | refactor(email): 2.2 - classify_matches, one match→resolve enforcement point (#110) | `merged` | @vjvarada | 2026-07-22 | `3c594622` | `refs/archive/pr/121` |
| [#122](pr-0122.md) | fix(email): 2.1 (core) - manual sync stops losing refresh tokens + wiping cursor | `merged` | @vjvarada | 2026-07-22 | `111b27fe` | `refs/archive/pr/122` |
| [#123](pr-0123.md) | feat(email): drafts carry attachments — no more full-send detour (2.4) | `merged` | @vjvarada | 2026-07-22 | `1fd0cf09` | `refs/archive/pr/123` |
| [#124](pr-0124.md) | feat(email): Fix strips the wrong label off the corrected message (3.4 / H6) | `merged` | @vjvarada | 2026-07-22 | `976d5e75` | `refs/archive/pr/124` |
| [#125](pr-0125.md) | feat(email): rule-path draft context parity + compose-assist learning (3.8) | `merged` | @vjvarada | 2026-07-22 | `0004e83e` | `refs/archive/pr/125` |
| [#126](pr-0126.md) | feat(email): search filter UI — date range, sender category, importance (3.12) | `merged` | @vjvarada | 2026-07-22 | `f53c21dc` | `refs/archive/pr/126` |
| [#127](pr-0127.md) | feat(email): reject an in-force pattern, restore a rejected one (3.6) | `merged` | @vjvarada | 2026-07-22 | `9bb181b5` | `refs/archive/pr/127` |
| [#128](pr-0128.md) | feat(email): digest becomes a daily brief — backlog aging + commitments due (3.11) | `merged` | @vjvarada | 2026-07-22 | `cb8a1fbe` | `refs/archive/pr/128` |
| [#129](pr-0129.md) | feat(email): reclassify drains the whole mailbox, resumably + tracked (3.7) | `merged` | @vjvarada | 2026-07-22 | `4c0c17bd` | `refs/archive/pr/129` |
| [#130](pr-0130.md) | docs(email): mark Phase 3 items shipped in the master plan | `merged` | @vjvarada | 2026-07-22 | `d84db10d` | `refs/archive/pr/130` |
| [#131](pr-0131.md) | feat(email): draft in my voice — semantic Sent few-shot in reply context (3.1) | `merged` | @vjvarada | 2026-07-22 | `1558e566` | `refs/archive/pr/131` |
| [#132](pr-0132.md) | feat(email): KB ranked by relevance, and out of the classifier prompt (3.5) | `merged` | @vjvarada | 2026-07-22 | `273b6445` | `refs/archive/pr/132` |
| [#133](pr-0133.md) | feat(email): calendar-aware scheduling replies (3.10) | `merged` | @vjvarada | 2026-07-22 | `fadebf5a` | `refs/archive/pr/133` |
| [#134](pr-0134.md) | docs(email): mark 3.1/3.5/3.10 shipped in the master plan | `merged` | @vjvarada | 2026-07-22 | `4310b9c2` | `refs/archive/pr/134` |
| [#135](pr-0135.md) | feat(email): per-message audit timeline (3.9) | `merged` | @vjvarada | 2026-07-22 | `303c59ec` | `refs/archive/pr/135` |
| [#136](pr-0136.md) | feat(email): conversation collapse in the mailbox list (3.2) | `merged` | @vjvarada | 2026-07-22 | `fdc09baf` | `refs/archive/pr/136` |
| [#137](pr-0137.md) | feat(email): snooze a conversation until later (3.3, part 1) | `merged` | @vjvarada | 2026-07-22 | `a464e6c3` | `refs/archive/pr/137` |
| [#138](pr-0138.md) | docs(email): mark 3.9 / 3.2 / snooze shipped | `merged` | @vjvarada | 2026-07-22 | `233ae054` | `refs/archive/pr/138` |
| [#139](pr-0139.md) | email Phase 2: provider_session helper (2.11 request paths) + stale-plan re-audit | `merged` | @vjvarada | 2026-07-22 | `96b8e17f` | `refs/archive/pr/139` |
| [#140](pr-0140.md) | email 2.2: one apply+watermark enforcement point | `merged` | @vjvarada | 2026-07-22 | `d33138ea` | `refs/archive/pr/140` |
| [#141](pr-0141.md) | email 2.1: revive label-learning on the scheduler sync path | `merged` | @vjvarada | 2026-07-22 | `0e32231b` | `refs/archive/pr/141` |
| [#142](pr-0142.md) | feat(tasks): unified status pill, per-context colours, and lucide priority/suggestion icons in Next Actions | `merged` | @vjvarada | 2026-07-22 | `52fbed9f` | `refs/archive/pr/142` |
| [#143](pr-0143.md) | email 2.6: one-off ghost-row merge script | `merged` | @vjvarada | 2026-07-22 | `c18f14ff` | `refs/archive/pr/143` |
| [#144](pr-0144.md) | email 2.11 (complete): provider_session for /send + background jobs | `merged` | @vjvarada | 2026-07-22 | `6493afd1` | `refs/archive/pr/144` |
| [#145](pr-0145.md) | email 2.1 (complete): trigger_sync collapses onto the one sync core | `merged` | @vjvarada | 2026-07-22 | `675cd007` | `refs/archive/pr/145` |
| [#146](pr-0146.md) | email 2.3: split runner.py + replyzero.py (+ auto-learn LIKE-collision fix) | `merged` | @vjvarada | 2026-07-22 | `64f67aec` | `refs/archive/pr/146` |
| [#147](pr-0147.md) | email 2.7: digest = projection of the analytics aggregates + one dialog (Phase 2 complete) | `merged` | @vjvarada | 2026-07-22 | `2dec638d` | `refs/archive/pr/147` |
| [#148](pr-0148.md) | fix(email): digest send 500 — commitments query uuid=text + poisoned transaction | `merged` | @vjvarada | 2026-07-22 | `6d36eadf` | `refs/archive/pr/148` |
| [#149](pr-0149.md) | feat(tasks): remove Done button, stage on local tasks + ClickUp status mapping, whole-card-clickable details | `merged` | @vjvarada | 2026-07-22 | `163861f3` | `refs/archive/pr/149` |
| [#150](pr-0150.md) | fix(email): review follow-ups — gate-probe rollbacks + attachment download on provider_session | `merged` | @vjvarada | 2026-07-22 | `f41afdf2` | `refs/archive/pr/150` |
| [#151](pr-0151.md) | feat(tasks): surface list-level ClickUp statuses (e.g. Done) + multiple assignees | `merged` | @vjvarada | 2026-07-22 | `fd4e5a5d` | `refs/archive/pr/151` |
| [#152](pr-0152.md) | chore(email): bounded internet_message_id backfill script (2.6 unblock) | `merged` | @vjvarada | 2026-07-22 | `71ab0d84` | `refs/archive/pr/152` |
| [#153](pr-0153.md) | feat(calendar): Focus OS — leverage lens, One Thing, Gap Filler, daily rituals, Focus Mode (F0+F1) | `merged` | @vjvarada | 2026-07-22 | `da2da8a1` | `refs/archive/pr/153` |
| [#154](pr-0154.md) | fix(tasks): scope a synced task's stage picker to its own project + show ClickUp statuses in the tool's case | `merged` | @vjvarada | 2026-07-22 | `0daaaa1e` | `refs/archive/pr/154` |
| [#155](pr-0155.md) | feat(calendar): block context menu, undoable scheduling, mobile parity | `merged` | @vjvarada | 2026-07-22 | `1453b1cf` | `refs/archive/pr/155` |
| [#156](pr-0156.md) | fix(calendar): two-row mobile header — due-soon pill never wraps | `merged` | @vjvarada | 2026-07-22 | `6b8b5265` | `refs/archive/pr/156` |
| [#157](pr-0157.md) | feat(tasks): de-clutter Clarify with progressive disclosure + mobile polish for inbox/clarify/next-actions | `merged` | @vjvarada | 2026-07-22 | `fb99403f` | `refs/archive/pr/157` |
| [#158](pr-0158.md) | feat(calendar): restartable day ritual, unified unscheduled rail, click-block-opens-task | `merged` | @vjvarada | 2026-07-22 | `a94a9bdd` | `refs/archive/pr/158` |
| [#159](pr-0159.md) | refactor(calendar): rail cards use block grammar — click opens, drag places | `merged` | @vjvarada | 2026-07-22 | `5c85da41` | `refs/archive/pr/159` |
| [#160](pr-0160.md) | feat(calendar): first-free-slot via context menus — rail cards + overdue blocks | `merged` | @vjvarada | 2026-07-22 | `0872847c` | `refs/archive/pr/160` |
| [#161](pr-0161.md) | Chat & agent framework review: audit docs + P0–P2 chat fixes + orchestration Phase 0 | `merged` | @vjvarada | 2026-07-22 | `689aafb3` | `refs/archive/pr/161` |
| [#162](pr-0162.md) | docs(calendar): comprehensive AI review + two prompt/context fixes | `merged` | @vjvarada | 2026-07-22 | `03c0fb67` | `refs/archive/pr/162` |
| [#163](pr-0163.md) | feat(email): mailbox Dashboard — open-loop ledgers with actions (+ status truth fixes) | `merged` | @vjvarada | 2026-07-22 | `3ea499c5` | `refs/archive/pr/163` |
| [#164](pr-0164.md) | feat(tasks): full-width single-header inbox + ground every AI prompt in real system state | `merged` | @vjvarada | 2026-07-22 | `8e9dc4ee` | `refs/archive/pr/164` |
| [#165](pr-0165.md) | feat(tasks): board-card priority/suggestion corners + every nudge acts in place | `merged` | @vjvarada | 2026-07-22 | `e5ffab94` | `refs/archive/pr/165` |
| [#166](pr-0166.md) | feat(calendar): AI day-management over chat — planner tools + persisted One Thing | `merged` | @vjvarada | 2026-07-23 | `110d9b63` | `refs/archive/pr/166` |
| [#167](pr-0167.md) | Generative UI 2.0 Phase 1: panel surface, blocking HITL, 11-template library | `merged` | @vjvarada | 2026-07-23 | `285fa44f` | `refs/archive/pr/167` |
| [#168](pr-0168.md) | feat(email): rename Reply -> Needs Reply everywhere (mig 92 + ingest canonicalisation) | `merged` | @vjvarada | 2026-07-23 | `efae822b` | `refs/archive/pr/168` |
| [#169](pr-0169.md) | GenUI cross-runtime HITL review: watchdog/timeout parity for MAF + Copilot SDK agents | `merged` | @vjvarada | 2026-07-23 | `542d366f` | `refs/archive/pr/169` |
| [#170](pr-0170.md) | fix(infra): renumber rename migration to 93 (number collision) | `merged` | @vjvarada | 2026-07-23 | `3bb577eb` | `refs/archive/pr/170` |
| [#171](pr-0171.md) | fix(email): collapse Outlook-desktop reply chains (no-marker quote boundaries) | `merged` | @vjvarada | 2026-07-23 | `2ed239fd` | `refs/archive/pr/171` |
| [#172](pr-0172.md) | feat(email): Fix-anywhere (context menus) + honest Dismiss on the dashboard | `merged` | @vjvarada | 2026-07-23 | `8fd0f180` | `refs/archive/pr/172` |
| [#173](pr-0173.md) | Chat UI: column reorder, conversations rail, and agent-identity polish | `merged` | @vjvarada | 2026-07-23 | `b8068167` | `refs/archive/pr/173` |
| [#174](pr-0174.md) | feat(tasks): minimizable Focus Mode — persistent timer dock across the control plane | `merged` | @vjvarada | 2026-07-23 | `aff9d575` | `refs/archive/pr/174` |
| [#175](pr-0175.md) | fix(email): Uncategorized is a state, not a label — recategorize-or-fix on click | `merged` | @vjvarada | 2026-07-23 | `8dd5582c` | `refs/archive/pr/175` |
| [#176](pr-0176.md) | feat(email): voice & writing-style profile learned from past mail (mig 94) | `merged` | @vjvarada | 2026-07-23 | `9ab36397` | `refs/archive/pr/176` |
| [#177](pr-0177.md) | feat(email): one rules screen — learned patterns nested under their rule | `merged` | @vjvarada | 2026-07-23 | `cfd6bce2` | `refs/archive/pr/177` |
| [#178](pr-0178.md) | feat(email): show every standing policy on the Rules screen, incl. upstream Outlook rules | `merged` | @vjvarada | 2026-07-23 | `3a8e85bc` | `refs/archive/pr/178` |
| [#179](pr-0179.md) | Chat UI follow-ups: consistent clickable collapse rails + two-column breathing agent tiles | `merged` | @vjvarada | 2026-07-23 | `90d686c8` | `refs/archive/pr/179` |
| [#180](pr-0180.md) | refactor(calendar): split the CalendarView monolith into components/calendar/* | `merged` | @vjvarada | 2026-07-23 | `bff0cb3f` | `refs/archive/pr/180` |
| [#181](pr-0181.md) | AI Note Taker: architecture spec + research appendix + slice 0 (schema, acb_stt, /notes API + UI shell) | `merged` | @vjvarada | 2026-07-23 | `5b110143` | `refs/archive/pr/181` |
| [#182](pr-0182.md) | feat(email): dashboard reply queue ranks by priority + category/sender click-through | `merged` | @vjvarada | 2026-07-23 | `0da31ff2` | `refs/archive/pr/182` |
| [#183](pr-0183.md) | feat(email): draft-from-dashboard — ✍️ a needs-reply row opens the thread with an AI draft | `merged` | @vjvarada | 2026-07-23 | `dc33db67` | `refs/archive/pr/183` |
| [#184](pr-0184.md) | AI Note Taker slices 1–2: notes generation, in-browser recorder, and action-item → task loop-closure | `merged` | @vjvarada | 2026-07-23 | `3dcfd28b` | `refs/archive/pr/184` |
| [#185](pr-0185.md) | fix(db): renumber note-taker migration 94→95 (collided with voice-profile 94) | `merged` | @vjvarada | 2026-07-23 | `f7407c02` | `refs/archive/pr/185` |
| [#186](pr-0186.md) | feat(email): per-thread Nudge — one-click AI follow-up draft on waiting-on-them rows | `merged` | @vjvarada | 2026-07-23 | `b432d533` | `refs/archive/pr/186` |
| [#187](pr-0187.md) | feat(email): opt-in AI morning brief — one-liner atop the dashboard and digest | `merged` | @vjvarada | 2026-07-23 | `b27eced1` | `refs/archive/pr/187` |
| [#188](pr-0188.md) | UI: larger agent characters inside the same card frames | `merged` | @vjvarada | 2026-07-23 | `a7fa0ff4` | `refs/archive/pr/188` |
| [#189](pr-0189.md) | feat(agents): coding skill for MAF agents — code_task + run_script with durable script store | `merged` | @vjvarada | 2026-07-23 | `52b1a133` | `refs/archive/pr/189` |
| [#190](pr-0190.md) | feat(agents): proactive "render UI by default" directive for MAF + Copilot agents (genUI Phase 2) | `merged` | @vjvarada | 2026-07-23 | `3e17390f` | `refs/archive/pr/190` |
| [#191](pr-0191.md) | feat(agents): design.md on demand via load_design_system() — off every prompt | `merged` | @vjvarada | 2026-07-23 | `93b93a08` | `refs/archive/pr/191` |
| [#192](pr-0192.md) | feat(calendar): configurable planning philosophy + a humane planner (breaks, lunch, whitespace) | `merged` | @vjvarada | 2026-07-23 | `bee6d6bb` | `refs/archive/pr/192` |
| [#193](pr-0193.md) | feat(calendar): full-day (24h) mode — schedule any hour, day or night | `merged` | @vjvarada | 2026-07-23 | `302a4760` | `refs/archive/pr/193` |
| [#194](pr-0194.md) | feat(calendar): recurring windows — block out habits + reserve times for kinds of work | `merged` | @vjvarada | 2026-07-23 | `f0c79193` | `refs/archive/pr/194` |
| [#195](pr-0195.md) | Calendar: single always-24h grid (Google Calendar model) | `merged` | @vjvarada | 2026-07-23 | `5a51e296` | `refs/archive/pr/195` |
| [#196](pr-0196.md) | feat(tasks): Deep Work axis — flow-state tasks judged at clarify, protected by the planner | `merged` | @vjvarada | 2026-07-23 | `05d7802e` | `refs/archive/pr/196` |
| [#197](pr-0197.md) | harden(agents): post-review hardening for the coding skill + genUI wiring | `merged` | @vjvarada | 2026-07-23 | `5e7cc8d2` | `refs/archive/pr/197` |
| [#198](pr-0198.md) | fix(infra): renumber colliding migrations 93→97, 94→98 (unblocks all PRs) | `closed` | @vjvarada | 2026-07-23 | `—` | `refs/archive/pr/198` |
| [#199](pr-0199.md) | Calendar review: fix planner correctness + a chat-tool crash | `merged` | @vjvarada | 2026-07-23 | `f892af05` | `refs/archive/pr/199` |
| [#200](pr-0200.md) | feat(tasks): full task management over chat — new agent tools + AG-UI task cards | `merged` | @vjvarada | 2026-07-23 | `bdb93838` | `refs/archive/pr/200` |
| [#201](pr-0201.md) | Calendar review follow-ups: confirmation gate, injection fencing, settings IA, geometry tests | `merged` | @vjvarada | 2026-07-23 | `8d854545` | `refs/archive/pr/201` |
| [#202](pr-0202.md) | fix(infra): 3-digit-safe migration numbering + fix 97 collision & stale gtd eval (unblocks all PRs) | `merged` | @vjvarada | 2026-07-23 | `3e5eb338` | `refs/archive/pr/202` |
| [#203](pr-0203.md) | feat(notes): Note Taker v2 — named speakers, summary-first workspace, live captions | `merged` | @vjvarada | 2026-07-23 | `371791ad` | `refs/archive/pr/203` |
| [#204](pr-0204.md) | feat(notes): diarization hint + re-transcribe + create-flow template picker | `merged` | @vjvarada | 2026-07-24 | `e5c11b72` | `refs/archive/pr/204` |
| [#205](pr-0205.md) | feat(whatsapp): WhatsApp Message Manager — official Cloud API vertical (plan → W0–W3) | `merged` | @vjvarada | 2026-07-24 | `75164374` | `refs/archive/pr/205` |
| [#206](pr-0206.md) | fix(email): salutations on AI drafts + mobile bottom nav no longer cuts off composer UI | `merged` | @vjvarada | 2026-07-24 | `3c5b37b5` | `refs/archive/pr/206` |
| [#207](pr-0207.md) | WhatsApp Manager — companion + intelligence layer (groups, nudges, voice, agent, snooze, Pulse) | `merged` | @vjvarada | 2026-07-24 | `90002e61` | `refs/archive/pr/207` |
| [#208](pr-0208.md) | WhatsApp: saved replies + background enrichment scheduler (W8–W9) | `merged` | @vjvarada | 2026-07-24 | `b0b91c59` | `refs/archive/pr/208` |
| [#209](pr-0209.md) | feat(notes): provenance you can touch + Recording Dock (recording follows you) | `merged` | @vjvarada | 2026-07-24 | `e9093432` | `refs/archive/pr/209` |
| [#210](pr-0210.md) | WhatsApp: semantic search + Connect-a-number wizard (W10–W11) | `merged` | @vjvarada | 2026-07-24 | `977345e2` | `refs/archive/pr/210` |
| [#211](pr-0211.md) | WhatsApp: Embedded Signup one-click connect (W12) + connect env docs | `merged` | @vjvarada | 2026-07-24 | `baf31105` | `refs/archive/pr/211` |
| [#212](pr-0212.md) | WhatsApp: multi-number management + Integrations-page entry (W13–W14) | `merged` | @vjvarada | 2026-07-24 | `9746ae94` | `refs/archive/pr/212` |
| [#213](pr-0213.md) | feat(notes): free local diarization via sherpa-onnx — 4 GB trial, Deepgram fallback | `merged` | @vjvarada | 2026-07-24 | `58f2343a` | `refs/archive/pr/213` |
| [#214](pr-0214.md) | fix(deploy): install ffmpeg on the VPS so local diarization actually runs | `merged` | @vjvarada | 2026-07-24 | `dc1c801d` | `refs/archive/pr/214` |
| [#215](pr-0215.md) | Show all apps on the landing page | `merged` | @vjvarada | 2026-07-24 | `2c8e5756` | `refs/archive/pr/215` |
| [#216](pr-0216.md) | fix(notes): meeting-detail header overflow on mobile | `merged` | @vjvarada | 2026-07-24 | `e2946a02` | `refs/archive/pr/216` |
| [#217](pr-0217.md) | fix(notes): audio playback fails on iOS — forward Range, serve inline | `merged` | @vjvarada | 2026-07-24 | `6412d0ec` | `refs/archive/pr/217` |
| [#218](pr-0218.md) | Calendar AI planning: fix silent fallback, clarify prompts, mobile UX | `merged` | @vjvarada | 2026-07-24 | `6768845c` | `refs/archive/pr/218` |
| [#219](pr-0219.md) | Calendar: show reserved lunch/block/focus windows on the grid | `merged` | @vjvarada | 2026-07-24 | `e2214934` | `refs/archive/pr/219` |
| [#220](pr-0220.md) | fix(email): AI-bar instruction now steers reply drafts; composer footer fits on phones | `merged` | @vjvarada | 2026-07-25 | `652ed972` | `refs/archive/pr/220` |
| [#221](pr-0221.md) | WhatsApp W15: connect a personal number by QR (whatsmeow bridge) | `merged` | @vjvarada | 2026-07-25 | `802fed41` | `refs/archive/pr/221` |
| [#222](pr-0222.md) | Remove the mobile top app bar | `merged` | @vjvarada | 2026-07-25 | `f6ed7227` | `refs/archive/pr/222` |
| [#223](pr-0223.md) | deploy: run the whatsmeow WhatsApp bridge as a localhost systemd service | `merged` | @vjvarada | 2026-07-25 | `4db6cfbd` | `refs/archive/pr/223` |
| [#224](pr-0224.md) | refactor(whatsapp): comprehensive review — dead code, dedup, bug fixes across the vertical | `merged` | @vjvarada | 2026-07-25 | `40a617e5` | `refs/archive/pr/224` |
| [#225](pr-0225.md) | deploy: recover a dead Caddy and gate success on the public site serving | `merged` | @vjvarada | 2026-07-25 | `b43597eb` | `refs/archive/pr/225` |
| [#226](pr-0226.md) | Calendar planner: surface why AI ranking fell back to priority order | `merged` | @vjvarada | 2026-07-25 | `e90ff0b6` | `refs/archive/pr/226` |
| [#227](pr-0227.md) | fix(chat): inline AG-UI cards erased by lean re-saves; table headers rendered [object Object] | `merged` | @vjvarada | 2026-07-25 | `2919d99d` | `refs/archive/pr/227` |
| [#228](pr-0228.md) | feat(whatsapp): backfill recent history on link (whatsmeow HistorySync) | `merged` | @vjvarada | 2026-07-25 | `fdb9ff2d` | `refs/archive/pr/228` |
| [#229](pr-0229.md) | Calendar: Rebuild my day / Fit what's left — reshuffle + evict overflow | `merged` | @vjvarada | 2026-07-25 | `864c254a` | `refs/archive/pr/229` |
| [#230](pr-0230.md) | feat(whatsapp-ui): persistent sub-nav, Numbers management, native icons | `merged` | @vjvarada | 2026-07-25 | `9a9075c9` | `refs/archive/pr/230` |
| [#231](pr-0231.md) | Email: fast, streamed, iterative "Draft with AI" | `merged` | @vjvarada | 2026-07-25 | `d79a7208` | `refs/archive/pr/231` |
| [#232](pr-0232.md) | Calendar: Rebuild my day sweeps in prior-day unfinished tasks | `merged` | @vjvarada | 2026-07-25 | `a28e6513` | `refs/archive/pr/232` |
| [#233](pr-0233.md) | feat(whatsapp-ui): move the WhatsApp sub-nav to a left column (match other apps) | `merged` | @vjvarada | 2026-07-25 | `5f43d6a0` | `refs/archive/pr/233` |
| [#234](pr-0234.md) | WhatsApp: mobile-optimized layout (responsive shell + inbox drill-down) | `merged` | @vjvarada | 2026-07-25 | `fd9b6d14` | `refs/archive/pr/234` |
| [#235](pr-0235.md) | Calendar: roll-over returns unfinished tasks to your list (not onto a day) | `merged` | @vjvarada | 2026-07-25 | `10b20a50` | `refs/archive/pr/235` |
| [#236](pr-0236.md) | App Workshop &amp; Custom Apps — RFC, mockups, Phase 0–3b (build/deploy loop, durability, integrations, testing, sharing, agent tools) | `merged` | @vjvarada | 2026-07-25 | `d2477094` | `refs/archive/pr/236` |
| [#237](pr-0237.md) | WhatsApp: align colors + active-nav treatment with the design system | `merged` | @vjvarada | 2026-07-25 | `9513853c` | `refs/archive/pr/237` |
| [#238](pr-0238.md) | WhatsApp: match the triage spine to the email/tasks sidebar | `merged` | @vjvarada | 2026-07-25 | `18a1ed35` | `refs/archive/pr/238` |
| [#239](pr-0239.md) | feat(notes): mic picker + live level check, and auto-name speakers from self-intros | `merged` | @vjvarada | 2026-07-25 | `63f867a6` | `refs/archive/pr/239` |
| [#240](pr-0240.md) | WhatsApp mobile: drive sub-nav from the bottom bar (drawers), not a top strip | `merged` | @vjvarada | 2026-07-26 | `a69ec853` | `refs/archive/pr/240` |
| [#241](pr-0241.md) | App Workshop: live token tracking, response-shape fix, design-system reuse | `merged` | @vjvarada | 2026-07-26 | `ab57de94` | `refs/archive/pr/241` |
| [#242](pr-0242.md) | feat(notes): mobile recording resilience (wake lock + backgrounding safeguard) + meeting-bot docs | `merged` | @vjvarada | 2026-07-26 | `fc7f6c3d` | `refs/archive/pr/242` |
| [#243](pr-0243.md) | WhatsApp: sync native labels/lists from the number and mirror them in the inbox | `merged` | @vjvarada | 2026-07-26 | `17d064ea` | `refs/archive/pr/243` |
| [#244](pr-0244.md) | feat(notes): meeting bot — send a notetaker to join a call by link (Recall.ai) | `merged` | @vjvarada | 2026-07-26 | `7b5eb83d` | `refs/archive/pr/244` |
| [#245](pr-0245.md) | fix(notes): renumber meeting-bot migration 117 → 118 (prefix collision, fixes red main) | `merged` | @vjvarada | 2026-07-26 | `75fcff8e` | `refs/archive/pr/245` |
| [#246](pr-0246.md) | Artifacts: lint generated HTML, and add React artifacts (inline + full-page) | `merged` | @vjvarada | 2026-07-26 | `1f3cb2eb` | `refs/archive/pr/246` |
| [#247](pr-0247.md) | email: contact card on a sender's name/avatar, and the contacts directory it builds | `merged` | @vjvarada | 2026-07-27 | `35b1480c` | `refs/archive/pr/247` |
| [#248](pr-0248.md) | App Workshop: T2 — real React apps (build step, no new infra) | `merged` | @vjvarada | 2026-07-27 | `da82b424` | `refs/archive/pr/248` |
| [#249](pr-0249.md) | App Workshop T2: reuse @cc/ui instead of hand-rolled cc-* markup | `merged` | @vjvarada | 2026-07-27 | `5b4f311e` | `refs/archive/pr/249` |
| [#250](pr-0250.md) | docs: note @cc/ui reuse in the App Workshop T2 status table | `merged` | @vjvarada | 2026-07-27 | `d39ace8b` | `refs/archive/pr/250` |
| [#251](pr-0251.md) | BO-7 cheap wins: real call context in decide(), destructive flag, mutation hardening | `merged` | @vjvarada | 2026-07-27 | `5b05c5fa` | `refs/archive/pr/251` |
| [#252](pr-0252.md) | Agent dep installs: wheels-only by default (RCE guard) | `merged` | @vjvarada | 2026-07-27 | `d27fe161` | `refs/archive/pr/252` |
| [#253](pr-0253.md) | Live transcription: stable speakers, live bus, and the copilot console | `merged` | @vjvarada | 2026-07-27 | `7a901c2c` | `refs/archive/pr/253` |
| [#254](pr-0254.md) | Fix migration 119 collision and link the live console | `merged` | @vjvarada | 2026-07-27 | `d675793c` | `refs/archive/pr/254` |
| [#255](pr-0255.md) | BO-7 phase 2: containerize Copilot-SDK code_task sessions | `merged` | @vjvarada | 2026-07-27 | `a1719c55` | `refs/archive/pr/255` |
| [#256](pr-0256.md) | AssemblyAI speech-to-text on both the batch and live paths | `merged` | @vjvarada | 2026-07-27 | `31ac5c1c` | `refs/archive/pr/256` |
| [#257](pr-0257.md) | BO-7 phase 2: containerize the App Workshop app-builder's session | `merged` | @vjvarada | 2026-07-27 | `7ee450b1` | `refs/archive/pr/257` |
| [#258](pr-0258.md) | Deploy the self-hosted meeting bot with the stack | `merged` | @vjvarada | 2026-07-27 | `2954ef92` | `refs/archive/pr/258` |
| [#259](pr-0259.md) | Live Meeting Copilot Phase B — the orchestrator | `merged` | @vjvarada | 2026-07-27 | `897ec56a` | `refs/archive/pr/259` |
| [#260](pr-0260.md) | chore(skills): upstream sync 30244154709 | `merged` | @github-actions[bot] | 2026-07-27 | `afa44069` | `refs/archive/pr/260` |
| [#261](pr-0261.md) | App Workshop: Advanced view gets a real IDE, Simple/Advanced toggle | `merged` | @vjvarada | 2026-07-27 | `91a48a72` | `refs/archive/pr/261` |
| [#262](pr-0262.md) | Correct AssemblyAI model ids and add model discovery | `merged` | @vjvarada | 2026-07-27 | `0d0f07ec` | `refs/archive/pr/262` |
| [#263](pr-0263.md) | Fix AssemblyAI showing "? unknown" and "No key" in the STT tier | `merged` | @vjvarada | 2026-07-27 | `c5edc916` | `refs/archive/pr/263` |
| [#264](pr-0264.md) | App Workshop: pinned apps in sidebar, usage stats on cards | `merged` | @vjvarada | 2026-07-27 | `2f33eca9` | `refs/archive/pr/264` |
| [#265](pr-0265.md) | App Workshop: fork/remix | `merged` | @vjvarada | 2026-07-27 | `28377533` | `refs/archive/pr/265` |
| [#266](pr-0266.md) | Calendar: 24/7 plan-through horizon + align the now-line with the hour grid | `merged` | @vjvarada | 2026-07-27 | `7dd8a820` | `refs/archive/pr/266` |
| [#267](pr-0267.md) | Live Meeting Copilot Phase C — meeting context | `merged` | @vjvarada | 2026-07-27 | `c7ef3302` | `refs/archive/pr/267` |
| [#268](pr-0268.md) | App Workshop: templates gallery | `merged` | @vjvarada | 2026-07-27 | `d9201d6d` | `refs/archive/pr/268` |
| [#269](pr-0269.md) | Calendar: add a custom end time to the plan-through horizon | `merged` | @vjvarada | 2026-07-28 | `50f4b8a1` | `refs/archive/pr/269` |
| [#270](pr-0270.md) | Fix SQLAlchemy bind-param bug breaking App Workshop create/fork/publish/storage writes | `merged` | @vjvarada | 2026-07-28 | `24ca3d97` | `refs/archive/pr/270` |
| [#271](pr-0271.md) | Side panel: visible close buttons + drag-to-resize, and artifact surface routing | `merged` | @vjvarada | 2026-07-28 | `299c0aa8` | `refs/archive/pr/271` |
| [#272](pr-0272.md) | Copilot agenda + standing instructions (Phase D) | `merged` | @vjvarada | 2026-07-28 | `e650ff5a` | `refs/archive/pr/272` |
| [#273](pr-0273.md) | Note Taker settings + per-meeting-type copilot instructions | `merged` | @vjvarada | 2026-07-28 | `55cbddc1` | `refs/archive/pr/273` |
| [#274](pr-0274.md) | Make a failed Google Meet join explain itself | `merged` | @vjvarada | 2026-07-28 | `55eca500` | `refs/archive/pr/274` |
| [#275](pr-0275.md) | Give the meeting bot live captions | `merged` | @vjvarada | 2026-07-28 | `680edc20` | `refs/archive/pr/275` |
| [#276](pr-0276.md) | App Workshop: per-app npm dependencies for T2 (unblocks Three.js/3D) | `merged` | @vjvarada | 2026-07-28 | `aa304d13` | `refs/archive/pr/276` |
| [#278](pr-0278.md) | Let a meeting be prepared before it starts | `merged` | @vjvarada | 2026-07-28 | `099d6661` | `refs/archive/pr/278` |
| [#279](pr-0279.md) | Note Taker UX redesign: console, library, outcomes, visual pass | `merged` | @vjvarada | 2026-07-28 | `837a7af4` | `refs/archive/pr/279` |
| [#280](pr-0280.md) | Artifact system: token audit — tier design.md, cap compile diagnostics | `merged` | @vjvarada | 2026-07-29 | `1ec76021` | `refs/archive/pr/280` |
| [#281](pr-0281.md) | Calendar: plan on tap (not on open), per-run note in both modes, and note overrides the standing prompt | `merged` | @vjvarada | 2026-07-29 | `35f8e3dd` | `refs/archive/pr/281` |
| [#282](pr-0282.md) | App Workshop: mobile layout for the Workshop editor | `merged` | @vjvarada | 2026-07-29 | `ece50124` | `refs/archive/pr/282` |
| [#283](pr-0283.md) | feat(email): dashboard triage + filter pills, signature-in-body system, rich signature editor | `merged` | @vjvarada | 2026-07-29 | `3d49cb25` | `refs/archive/pr/283` |
| [#284](pr-0284.md) | Actually ask AssemblyAI for speakers on live streaming | `merged` | @vjvarada | 2026-07-29 | `7b8148c9` | `refs/archive/pr/284` |
| [#285](pr-0285.md) | App Workshop: mobile-responsive design philosophy for built apps | `merged` | @vjvarada | 2026-07-29 | `5f01e6dd` | `refs/archive/pr/285` |
| [#286](pr-0286.md) | Org access control: members, roles, and per-user feature/agent access | `merged` | @vjvarada | 2026-07-29 | `92134cf0` | `refs/archive/pr/286` |
| [#287](pr-0287.md) | Show the copilot on the recording screen | `merged` | @vjvarada | 2026-07-29 | `22e91151` | `refs/archive/pr/287` |
| [#288](pr-0288.md) | Fix mobile checkpoints popover rendering off-screen, declutter Workshop header | `merged` | @vjvarada | 2026-07-29 | `57255c05` | `refs/archive/pr/288` |
| [#289](pr-0289.md) | Pin the streaming model that can actually diarize | `merged` | @vjvarada | 2026-07-29 | `da84d020` | `refs/archive/pr/289` |
| [#290](pr-0290.md) | Enforce feature access at the gateway, not just in the UI | `merged` | @vjvarada | 2026-07-29 | `ecaa0354` | `refs/archive/pr/290` |
| [#291](pr-0291.md) | Scope integration credentials and org memory to the acting member | `merged` | @vjvarada | 2026-07-29 | `ea911fad` | `refs/archive/pr/291` |
| [#292](pr-0292.md) | Two bugs a first test would have hit | `merged` | @vjvarada | 2026-07-29 | `8f116b22` | `refs/archive/pr/292` |
| [#293](pr-0293.md) | Separate the service identity token from the LLM key; sign the agent webhook | `merged` | @vjvarada | 2026-07-29 | `ea2fb465` | `refs/archive/pr/293` |
| [#294](pr-0294.md) | WhatsApp: sync profile pictures from the number, render them in the inbox | `merged` | @vjvarada | 2026-07-29 | `c17ec50b` | `refs/archive/pr/294` |
| [#295](pr-0295.md) | App Workshop: proper icons + a UI/UX quality bar for built apps | `merged` | @vjvarada | 2026-07-29 | `5fefd994` | `refs/archive/pr/295` |
| [#296](pr-0296.md) | Default-deny authentication (closes BO-2) + hand off to multiplayer collaboration | `merged` | @vjvarada | 2026-07-29 | `a16b87cf` | `refs/archive/pr/296` |
| [#297](pr-0297.md) | fix(chat): mobile UI — reachable delete button, collapse composer after send, compact bottom nav | `merged` | @vjvarada | 2026-07-29 | `5f403ffa` | `refs/archive/pr/297` |
| [#298](pr-0298.md) | Artifacts: open rendered by default — side panel on desktop, sheet on mobile | `merged` | @vjvarada | 2026-07-29 | `9e7d623c` | `refs/archive/pr/298` |
| [#299](pr-0299.md) | Resolve post-merge collisions with concurrent WhatsApp avatar work | `merged` | @vjvarada | 2026-07-29 | `22701f3c` | `refs/archive/pr/299` |
| [#300](pr-0300.md) | End a live meeting from the console | `merged` | @vjvarada | 2026-07-29 | `5f46ab73` | `refs/archive/pr/300` |
| [#301](pr-0301.md) | Fix icons, logos and images in full-page artifacts | `merged` | @vjvarada | 2026-07-30 | `ae18ddc7` | `refs/archive/pr/301` |
| [#302](pr-0302.md) | feat(email): recipient autocomplete — contact suggestions in To/Cc/Bcc | `merged` | @vjvarada | 2026-07-30 | `4620a595` | `refs/archive/pr/302` |
| [#303](pr-0303.md) | fix(chat): Enter inserts a newline — sending is the Send button's job | `merged` | @vjvarada | 2026-07-30 | `1f7dc614` | `refs/archive/pr/303` |
| [#304](pr-0304.md) | Workflows app — visual automation builder over the agent fleet | `merged` | @vjvarada | 2026-07-30 | `5a851907` | `refs/archive/pr/304` |
| [#305](pr-0305.md) | Stream ASR only while something is reading it | `merged` | @vjvarada | 2026-07-30 | `ae98b615` | `refs/archive/pr/305` |
| [#306](pr-0306.md) | Workflow triggers that actually fire — schedule bootstrap, timezones, honest ticks, real webhook URLs | `merged` | @vjvarada | 2026-07-30 | `3f15f8d2` | `refs/archive/pr/306` |
| [#307](pr-0307.md) | App Workshop: 44px touch targets + filter voice models out of chat picker | `merged` | @vjvarada | 2026-07-31 | `dee8b0c2` | `refs/archive/pr/307` |
| [#308](pr-0308.md) | Typed tool arguments + a golden workflow fixture corpus | `merged` | @vjvarada | 2026-07-31 | `c2b85142` | `refs/archive/pr/308` |
| [#309](pr-0309.md) | Multiplayer rooms, memory compartments, and the BFF identity boundary | `merged` | @vjvarada | 2026-07-31 | `76786252` | `refs/archive/pr/309` |
| [#310](pr-0310.md) | Fix the fold trajectory eval broken by #309 | `merged` | @vjvarada | 2026-07-31 | `a6ef3408` | `refs/archive/pr/310` |
| [#311](pr-0311.md) | Fix the deploy build: gateway headers must be per-request, not per-process | `merged` | @vjvarada | 2026-07-31 | `d7e8dd33` | `refs/archive/pr/311` |
| [#312](pr-0312.md) | fix(email): Email Cleaner bulk archive/delete — correctness, provider verification, and archived mail leaving the list | `merged` | @vjvarada | 2026-08-01 | `0a8f88ee` | `refs/archive/pr/312` |
| [#313](pr-0313.md) | perf(email): page the cleaner's sender drill-down; show real rule labels instead of the synthesized "Conversation" chip | `merged` | @vjvarada | 2026-08-01 | `95072d30` | `refs/archive/pr/313` |
| [#314](pr-0314.md) | docs: qm prior-art — steer before floor control, skills index, no ambient room credentials | `merged` | @vjvarada | 2026-08-01 | `57dbbdd7` | `refs/archive/pr/314` |
| [#315](pr-0315.md) | feat(workflows): editor canvas matches the mockup — richer node cards, branch-aware edges | `merged` | @vjvarada | 2026-08-01 | `67ee23ff` | `refs/archive/pr/315` |
| [#316](pr-0316.md) | feat(email): multi-account audit — the chat assistant is now configured per account, not just scoped to one | `merged` | @vjvarada | 2026-08-01 | `46ef2e1a` | `refs/archive/pr/316` |
| [#317](pr-0317.md) | docs(board): WS-6 audited NO-GO — spec contract fails, board corrected | `merged` | @vjvarada | 2026-08-01 | `dec92c94` | `refs/archive/pr/317` |
| [#318](pr-0318.md) | feat(email): mailbox picker in the chat composer, and mailbox switches marked in the transcript | `merged` | @vjvarada | 2026-08-01 | `bc1e7d97` | `refs/archive/pr/318` |
| [#319](pr-0319.md) | docs(observability): WS-6 remediation — spec becomes dispatchable (NO-GO → GO) | `merged` | @vjvarada | 2026-08-01 | `8d85c450` | `refs/archive/pr/319` |
| [#320](pr-0320.md) | fix(ci): hoist TurnDecision under TYPE_CHECKING — main's blocking ruff gate is red | `merged` | @vjvarada | 2026-08-01 | `73218789` | `refs/archive/pr/320` |
| [#321](pr-0321.md) | feat(whatsapp): place and answer WhatsApp calls from CommandCenter | `merged` | @vjvarada | 2026-08-01 | `a85f3f78` | `refs/archive/pr/321` |
| [#322](pr-0322.md) | docs(board): WS-5 audited NO-GO — CI's blocking gates are decorative (main has no branch protection) | `merged` | @vjvarada | 2026-08-01 | `c7bd0f48` | `refs/archive/pr/322` |
| [#323](pr-0323.md) | fix(whatsapp): stop a slow call from surfacing as "gateway unreachable" | `merged` | @vjvarada | 2026-08-01 | `7ed485b0` | `refs/archive/pr/323` |
| [#324](pr-0324.md) | fix(whatsapp): dial from a chat, and normalise typed numbers before dialling | `merged` | @vjvarada | 2026-08-01 | `50d60f2e` | `refs/archive/pr/324` |
| [#325](pr-0325.md) | feat(whatsapp): play back a call's audio, and prove any of it arrived | `merged` | @vjvarada | 2026-08-01 | `65ada150` | `refs/archive/pr/325` |
| [#326](pr-0326.md) | docs(board): WS-9 audited NO-GO — third 🟢 row with no testable acceptance | `merged` | @vjvarada | 2026-08-01 | `b5102558` | `refs/archive/pr/326` |
| [#327](pr-0327.md) | fix(whatsapp): unmute the calling library's own media diagnostics | `merged` | @vjvarada | 2026-08-01 | `8124fbf4` | `refs/archive/pr/327` |
| [#328](pr-0328.md) | feat(tasks): WS-18 Waiting-For surfacing — and the overdue badge stops lying | `merged` | @vjvarada | 2026-08-01 | `14a58c13` | `refs/archive/pr/328` |
| [#329](pr-0329.md) | feat(whatsapp): two-way call audio — browser microphone and speakers | `merged` | @vjvarada | 2026-08-02 | `06d1b496` | `refs/archive/pr/329` |
| [#330](pr-0330.md) | fix(whatsapp): declare the websocket client the call-audio proxy imports | `merged` | @vjvarada | 2026-08-02 | `e03fa9e5` | `refs/archive/pr/330` |
| [#331](pr-0331.md) | fix(tasks): expected_by means an explicit promise — the Waiting-For badge stops going stale | `merged` | @vjvarada | 2026-08-02 | `b5a218bd` | `refs/archive/pr/331` |
| [#332](pr-0332.md) | fix(whatsapp): the call-audio socket never survived its handshake | `merged` | @vjvarada | 2026-08-02 | `60352325` | `refs/archive/pr/332` |
| [#333](pr-0333.md) | feat(observability): WS-6a + WS-6c — D1's attribution stamp exists as a substrate | `merged` | @vjvarada | 2026-08-02 | `d1fad3ec` | `refs/archive/pr/333` |
| [#334](pr-0334.md) | docs(whatsapp): bring the calling docs up to what actually shipped | `merged` | @vjvarada | 2026-08-02 | `04749347` | `refs/archive/pr/334` |
| [#335](pr-0335.md) | docs(WS-4): remediate BO-20 — the event-bus row was dispatching a build that already exists | `merged` | @vjvarada | 2026-08-02 | `4aa281f5` | `refs/archive/pr/335` |
| [#336](pr-0336.md) | feat(ingestion): BO-20f — Gmail and Zoho webhooks reach ClickUp parity, and two structlog bugs fall out | `merged` | @vjvarada | 2026-08-02 | `10411805` | `refs/archive/pr/336` |
| [#337](pr-0337.md) | docs(WS-10): remediate the multiplayer specs — and specify the subject: surface that was missing | `merged` | @vjvarada | 2026-08-02 | `aa55664c` | `refs/archive/pr/337` |
| [#338](pr-0338.md) | fix(whatsapp): the Talk button never appeared, so there was nothing to press | `merged` | @vjvarada | 2026-08-02 | `91c787db` | `refs/archive/pr/338` |
| [#339](pr-0339.md) | feat(whatsapp): engage mic and speakers when placing the call, not after | `merged` | @vjvarada | 2026-08-02 | `a228a262` | `refs/archive/pr/339` |
| [#340](pr-0340.md) | feat(ingestion): BO-20a — the stream consumer, wired but switched off | `merged` | @vjvarada | 2026-08-02 | `b520b1a0` | `refs/archive/pr/340` |
| [#341](pr-0341.md) | fix(whatsapp): audio tore itself down moments after every call connected | `merged` | @vjvarada | 2026-08-02 | `bb8cc077` | `refs/archive/pr/341` |
| [#342](pr-0342.md) | feat(ingestion): BO-20b slice 1 — emit_event can be strict, and the blocker that isn't closed yet | `merged` | @vjvarada | 2026-08-02 | `2ccff9e0` | `refs/archive/pr/342` |
| [#343](pr-0343.md) | chore(skills): upstream sync 30791424482 | `merged` | @github-actions[bot] | 2026-08-03 | `f31feebd` | `refs/archive/pr/343` |
| [#344](pr-0344.md) | docs(WS-0): truth pass across six workstreams — the board was describing a codebase from weeks ago | `merged` | @vjvarada | 2026-08-03 | `e180682b` | `refs/archive/pr/344` |
| [#345](pr-0345.md) | feat(whatsapp): a debug report you can paste when a call misbehaves | `merged` | @vjvarada | 2026-08-03 | `d785a7a7` | `refs/archive/pr/345` |
| [#346](pr-0346.md) | fix(access): three live defects — Notes was readable, deletable and sendable-as by any colleague | `merged` | @vjvarada | 2026-08-03 | `d2ef7fa0` | `refs/archive/pr/346` |
| [#347](pr-0347.md) | feat(BO-23): a backup path that can restore one table, not just the whole VPS | `merged` | @vjvarada | 2026-08-03 | `74082882` | `refs/archive/pr/347` |
| [#348](pr-0348.md) | fix(WS-13): Centers were unreachable by everyone, owner included — FEATURES never listed them | `merged` | @vjvarada | 2026-08-03 | `bebbd924` | `refs/archive/pr/348` |
| [#349](pr-0349.md) | docs(WS-14): Centers C was pointing at three things that do not exist | `merged` | @vjvarada | 2026-08-03 | `ed785bea` | `refs/archive/pr/349` |
| [#350](pr-0350.md) | feat(WS-24): the gate for letting colleagues onto the platform — and an org-wide HR exposure it found | `merged` | @vjvarada | 2026-08-03 | `007caae2` | `refs/archive/pr/350` |
| [#351](pr-0351.md) | fix(N4): the HR directory stops being org-wide read and write | `merged` | @vjvarada | 2026-08-04 | `891903de` | `refs/archive/pr/351` |
| [#352](pr-0352.md) | fix(N1/N2/N3): Notes was private to its owner in the library only | `merged` | @vjvarada | 2026-08-04 | `5beeabbe` | `refs/archive/pr/352` |
| [#353](pr-0353.md) | N6a — the sign-in queue: 53 knocks, and the system told nobody | `merged` | @vjvarada | 2026-08-04 | `2a41099b` | `refs/archive/pr/353` |
| [#354](pr-0354.md) | N7 — you can remove anyone except yourself, by all three doors | `merged` | @vjvarada | 2026-08-04 | `e911e9d2` | `refs/archive/pr/354` |
| [#355](pr-0355.md) | P0: nobody could connect a mailbox — the OAuth authorize leg was unreachable | `merged` | @vjvarada | 2026-08-04 | `59f9c917` | `refs/archive/pr/355` |
| [#356](pr-0356.md) | N8 — delete a member permanently, everyone except yourself | `merged` | @vjvarada | 2026-08-04 | `63a4548b` | `refs/archive/pr/356` |
| [#357](pr-0357.md) | The user-management contract every app must not deviate from | `merged` | @vjvarada | 2026-08-05 | `bd4b45b9` | `refs/archive/pr/357` |
| [#358](pr-0358.md) | BO-23: two of five backup anchors named tables that do not exist | `merged` | @vjvarada | 2026-08-05 | `d7d5c79b` | `refs/archive/pr/358` |
| [#359](pr-0359.md) | BO-23 backup fix + WS-25: deploys have not reached the box since #347 | `merged` | @vjvarada | 2026-08-05 | `24c95b75` | `refs/archive/pr/359` |
| [#360](pr-0360.md) | fix(WS-25): repoint the deploy guards at the extracted apply script | `merged` | @vjvarada | 2026-08-05 | `8fcbb524` | `refs/archive/pr/360` |
| [#361](pr-0361.md) | docs(WS-25): correct the pull unit — two defects found on first real run | `merged` | @vjvarada | 2026-08-05 | `402a0299` | `refs/archive/pr/361` |
| [#362](pr-0362.md) | WS-26: native CRM — spec, board registration, and the WS-26a slice (schema + core API) | `merged` | @vjvarada | 2026-08-05 | `7900e17a` | `refs/archive/pr/362` |
| [#363](pr-0363.md) | WS-26b: Zoho two-way sync (owner-directed D-CRM-7) — broker-gated single writer, ships OFF | `merged` | @vjvarada | 2026-08-05 | `33c6dca6` | `refs/archive/pr/363` |
| [#364](pr-0364.md) | WS-26c: CRM UI — kanban, lists, record sheets, convert modal + the API addendum | `merged` | @vjvarada | 2026-08-05 | `7881b8db` | `refs/archive/pr/364` |
| [#365](pr-0365.md) | feat(WS-26): the CRM's sync engine and UI - 26b + 26c, concluded and cross-checked | `merged` | @vjvarada | 2026-08-06 | `8d83ca10` | `refs/archive/pr/365` |
| [#366](pr-0366.md) | fix(WS-25): the deploy that reported success without happening | `merged` | @vjvarada | 2026-08-06 | `0c368bf4` | `refs/archive/pr/366` |
| [#367](pr-0367.md) | WS-27: mint the Projects app — Paca research, the native PM spec, and WS-27a+b (schema, grant read model, ClickUp importer) | `merged` | @vjvarada | 2026-08-06 | `4d506834` | `refs/archive/pr/367` |
| [#368](pr-0368.md) | fix(db): one wedged handler could freeze the whole database — three bounds | `merged` | @vjvarada | 2026-08-06 | `c038252b` | `refs/archive/pr/368` |
| [#369](pr-0369.md) | fix(WS-26): every note skipped on the first real backfill | `merged` | @vjvarada | 2026-08-06 | `bca3abc0` | `refs/archive/pr/369` |
| [#370](pr-0370.md) | fix(BO-23): one unremovable old backup dir blocked every deploy | `merged` | @vjvarada | 2026-08-06 | `6c1f4cc7` | `refs/archive/pr/370` |
| [#371](pr-0371.md) | fix(deploy): the lock_timeout prelude was invalid SQL and blocked every deploy | `merged` | @vjvarada | 2026-08-06 | `04278d4c` | `refs/archive/pr/371` |
| [#372](pr-0372.md) | feat(WS-26d): the CRM agent reads - and only the CRM, only GET | `merged` | @vjvarada | 2026-08-06 | `af3d3fc8` | `refs/archive/pr/372` |
| [#373](pr-0373.md) | docs(WS-26d): close B3, B4, B5 and B7 - the three held-back slices become dispatchable | `merged` | @vjvarada | 2026-08-06 | `c97a326b` | `refs/archive/pr/373` |
| [#374](pr-0374.md) | WS-27 + WS-28: the Projects app becomes usable, the People Center ships, and the Tasks app narrows to personal | `merged` | @vjvarada | 2026-08-06 | `7e5bcc97` | `refs/archive/pr/374` |
| [#375](pr-0375.md) | fix(WS-26b): the Deals cursor could never advance, so the pull never converged | `merged` | @vjvarada | 2026-08-06 | `200649fa` | `refs/archive/pr/375` |
| [#376](pr-0376.md) | docs(WS-26): the sync gate said 'never run' after it had run | `merged` | @vjvarada | 2026-08-06 | `45be26d0` | `refs/archive/pr/376` |
| [#377](pr-0377.md) | fix(email): stop holding a transaction across model and provider calls | `merged` | @vjvarada | 2026-08-06 | `73ab190a` | `refs/archive/pr/377` |
| [#378](pr-0378.md) | docs(WS-26): pipeline blueprint — stage order/probability root cause + WS-26f-i tickets | `merged` | @vjvarada | 2026-08-06 | `40c65ee7` | `refs/archive/pr/378` |
| [#379](pr-0379.md) | Theming engine + BO-10 (one DB pool, non-blocking audit) | `merged` | @vjvarada | 2026-08-07 | `552d3590` | `refs/archive/pr/379` |
| [#380](pr-0380.md) | BO-23: schedule the backup, and actually restore one | `merged` | @vjvarada | 2026-08-07 | `364b8bf2` | `refs/archive/pr/380` |
| [#381](pr-0381.md) | docs(WS-26): demo critical path — re-sequence the CRM plan for demo-readiness | `merged` | @vjvarada | 2026-08-07 | `727c1f64` | `refs/archive/pr/381` |
| [#382](pr-0382.md) | fix(BO-23): backup timer never scheduled — unit-sync loop was in the manual script, not the live path | `merged` | @vjvarada | 2026-08-07 | `a8bd038b` | `refs/archive/pr/382` |
| [#383](pr-0383.md) | fix(BO-23): pg seam recursed in docker mode — every apply since #380 segfaulted at the backup gate | `merged` | @vjvarada | 2026-08-07 | `5010fcd6` | `refs/archive/pr/383` |
| [#384](pr-0384.md) | fix(WS-28): migration 148 compared name[] to text[] — no migrations have applied since #374 merged | `merged` | @vjvarada | 2026-08-07 | `523f2788` | `refs/archive/pr/384` |
| [#385](pr-0385.md) | WS-28b-write: the person write half, and the three ways migration 148 had broken it | `merged` | @vjvarada | 2026-08-07 | `c1118a86` | `refs/archive/pr/385` |
| [#386](pr-0386.md) | fix(WS-27i): every project-task file upload was answering 422 — the activity vocabulary mirror went stale | `merged` | @vjvarada | 2026-08-07 | `2404257c` | `refs/archive/pr/386` |
| [#387](pr-0387.md) | WS-27j: notifications and @mentions — assignment stops being silent | `merged` | @vjvarada | 2026-08-07 | `97314d14` | `refs/archive/pr/387` |
| [#388](pr-0388.md) | BO-6 migration ledger + two theming fixes | `merged` | @vjvarada | 2026-08-07 | `58431e9a` | `refs/archive/pr/388` |
| [#389](pr-0389.md) | fix(access): a hidden nav pane now says why — and the catalog, not a code mirror, decides | `merged` | @vjvarada | 2026-08-07 | `edb67b85` | `refs/archive/pr/389` |
| [#390](pr-0390.md) | feat(WS-27b): the ClickUp import was unreachable from the product — build the UI | `merged` | @vjvarada | 2026-08-07 | `2e8032c4` | `refs/archive/pr/390` |
| [#391](pr-0391.md) | WS-26f: pipeline truth + settings UI + weighted pipeline (demo D1) | `merged` | @vjvarada | 2026-08-07 | `622a1b88` | `refs/archive/pr/391` |
| [#392](pr-0392.md) | WS-26d-email: caller-scoped email threads on CRM timelines (demo D2) | `merged` | @vjvarada | 2026-08-07 | `ded21ef7` | `refs/archive/pr/392` |
| [#393](pr-0393.md) | feat(WS-27b): import the ClickUp mirror the Tasks app already holds — one department, no tenant call | `merged` | @vjvarada | 2026-08-07 | `e6f9f5d5` | `refs/archive/pr/393` |
| [#394](pr-0394.md) | fix(WS-27b): the mirror import would have 500'd on the first real click — three defects a real Postgres found | `merged` | @vjvarada | 2026-08-07 | `7da5b4de` | `refs/archive/pr/394` |
| [#395](pr-0395.md) | fix(deploy): six deploys reported success while shipping nothing — the apply script was eating itself | `merged` | @vjvarada | 2026-08-07 | `bb337068` | `refs/archive/pr/395` |
| [#396](pr-0396.md) | docs(WS-26g): funnel semantics defined, false claims corrected, parity fixture named | `merged` | @vjvarada | 2026-08-07 | `c9a58f97` | `refs/archive/pr/396` |
| [#397](pr-0397.md) | feat(WS-26g): forecast & funnel reports — the ?tab=reports slice (D3) | `merged` | @vjvarada | 2026-08-07 | `6f5f0a95` | `refs/archive/pr/397` |
| [#398](pr-0398.md) | feat(WS-27k/l/m/n): filters + saved views · custom fields · tag registry · bulk edit | `merged` | @vjvarada | 2026-08-07 | `60c8ea68` | `refs/archive/pr/398` |
| [#399](pr-0399.md) | WS-27 parity backlog closed · WS-29 · merged with main's MT-1b — and the homonym it would have applied | `merged` | @vjvarada | 2026-08-07 | `b3fb6b51` | `refs/archive/pr/399` |
| [#400](pr-0400.md) | feat(WS-26d-write): four confirmation-gated CRM write tools (D4) | `merged` | @vjvarada | 2026-08-07 | `375f0e04` | `refs/archive/pr/400` |
| [#401](pr-0401.md) | docs(WS-26d-write): D4 status Built -> Merged + Deployed (#400) | `merged` | @vjvarada | 2026-08-07 | `affe0647` | `refs/archive/pr/401` |
| [#402](pr-0402.md) | docs(WS-26d-autolead): close audit blockers G1/G2/G3 — backfill discriminator, content ruling, real dedup | `merged` | @vjvarada | 2026-08-07 | `b09093a8` | `refs/archive/pr/402` |
| [#403](pr-0403.md) | feat(WS-26d-autolead): auto-lead from inbound email, OFF behind CRM_AUTO_LEAD (D5) | `merged` | @vjvarada | 2026-08-07 | `bc9ffe98` | `refs/archive/pr/403` |
| [#404](pr-0404.md) | feat(WS-29): MT-0 blockers + MT-1 scaffolding; migrations 157-159 scratch-verified (H1) | `merged` | @vjvarada | 2026-08-09 | `bd02a647` | `refs/archive/pr/404` |
| [#405](pr-0405.md) | docs(D19): twelve owner calls close every MT-2/3/4 business input; WS-30 minted | `merged` | @vjvarada | 2026-08-09 | `290ea195` | `refs/archive/pr/405` |
| [#406](pr-0406.md) | fix(admin): purge cascade map learns crm_auto_lead_cursors (163) | `merged` | @vjvarada | 2026-08-09 | `7b0ac581` | `refs/archive/pr/406` |
| [#407](pr-0407.md) | docs(D26): ai-company-brain -> project-docs + INDEX.md classification of record | `merged` | @vjvarada | 2026-08-09 | `7ebc5fc1` | `refs/archive/pr/407` |
| [#408](pr-0408.md) | Projects: beyond-parity queue WS-27u–z + Tasks↔Projects UI continuity | `merged` | @vjvarada | 2026-08-10 | `02510897` | `refs/archive/pr/408` |
| [#409](pr-0409.md) | fix(WS-27/WS-29): tenancy alignment - regenerate the RLS set, kill the phantom table, fence it | `merged` | @vjvarada | 2026-08-10 | `7837dfb8` | `refs/archive/pr/409` |
| [#410](pr-0410.md) | chore(skills): upstream sync 31357853938 | `merged` | @github-actions[bot] | 2026-08-10 | `a1b32a42` | `refs/archive/pr/410` |
| [#411](pr-0411.md) | docs(D28): the development doctrine - engineering_practice.md + R6/R7/R8 | `merged` | @vjvarada | 2026-08-10 | `79077e7c` | `refs/archive/pr/411` |
| [#412](pr-0412.md) | docs(D28 fix): silo is placement, not architecture - multi-tenant from customer #1 | `merged` | @vjvarada | 2026-08-10 | `d6f05cd5` | `refs/archive/pr/412` |
| [#413](pr-0413.md) | feat(D29): track the agent harness - cloud instances inherit plan-guard and the review loop | `closed` | @vjvarada | 2026-08-10 | `bf5fc960` | `refs/archive/pr/413` |
| [#414](pr-0414.md) | feat(D29): track the agent harness - cloud instances inherit plan-guard and the review loop | `merged` | @vjvarada | 2026-08-10 | `f5202ccb` | `refs/archive/pr/414` |
| [#415](pr-0415.md) | feat(D30): CLAUDE.md - the always-loaded briefing so cloud instances never start blind | `merged` | @vjvarada | 2026-08-10 | `5d556db6` | `refs/archive/pr/415` |
| [#416](pr-0416.md) | WS-29 H2: central tenant binding + routes/projects converted to tenant_session() | `merged` | @vjvarada | 2026-08-10 | `e2d99c55` | `refs/archive/pr/416` |
| [#417](pr-0417.md) | WS-29 H2 (waves 1+2): nine gateway packages converted to tenant_session — 111 classified sites remain | `merged` | @vjvarada | 2026-08-10 | `43e07ac9` | `refs/archive/pr/417` |
| [#418](pr-0418.md) | WS-27: one design language for /projects and /tasks, the themed categorical ramp, the house shell, and the last two Projects tenancy residues | `merged` | @vjvarada | 2026-08-10 | `bf56c9bc` | `refs/archive/pr/418` |
| [#419](pr-0419.md) | WS-27 S1–S4: Tasks becomes a real slice of Projects — one card, one detail surface, one selection grammar; plus the one-store decision and the fences that found three latent defects | `merged` | @vjvarada | 2026-08-10 | `54e4b880` | `refs/archive/pr/419` |
| [#420](pr-0420.md) | WS-27 S5 + the docked-pane overflow fix: the Projects detail panel stops being a plain form | `merged` | @vjvarada | 2026-08-10 | `0afa05db` | `refs/archive/pr/420` |
| [#421](pr-0421.md) | WS-27 S6 — the Projects card draws the pills its row already carries | `merged` | @vjvarada | 2026-08-10 | `1aec373d` | `refs/archive/pr/421` |
| [#422](pr-0422.md) | Projects: drain the queue (ab/ac/ae), fix a join-table authz gap, and read both references end to end | `merged` | @vjvarada | 2026-08-10 | `ebf68f4e` | `refs/archive/pr/422` |
| [#423](pr-0423.md) | docs(D31): D15 re-tested against Odoo, Salesforce and SAP — it stands, one gap found | `merged` | @vjvarada | 2026-08-10 | `83a678d6` | `refs/archive/pr/423` |
| [#424](pr-0424.md) | Projects Wave 1: the papercut wave (WS-27al/am/bd), sequenced by impact | `merged` | @vjvarada | 2026-08-11 | `00c47c6b` | `refs/archive/pr/424` |
| [#425](pr-0425.md) | WS-26h: CRM stage discipline (entry requirements + rot) · WS-26i audited NO-GO | `merged` | @vjvarada | 2026-08-11 | `d471ae80` | `refs/archive/pr/425` |
| [#426](pr-0426.md) | WS-26i-export: CRM filtered-list CSV export (+ fixes a BOM-stripping bug in the Projects proxy) | `merged` | @vjvarada | 2026-08-11 | `72553441` | `refs/archive/pr/426` |
| [#427](pr-0427.md) | WS-26h2 + D-CRM-13: entry requirements on the CHOSEN create stage | `merged` | @vjvarada | 2026-08-11 | `a06fa6a1` | `refs/archive/pr/427` |
| [#428](pr-0428.md) | WS-26h-fence: convert WS-26h's siting fence to AST reachability (+ record the WS-26i-bulk contract) | `merged` | @vjvarada | 2026-08-11 | `644e88b0` | `refs/archive/pr/428` |
| [#429](pr-0429.md) | WS-27ak slice 1: the Modal primitive on Base UI, and /projects' six dialogs onto it | `merged` | @vjvarada | 2026-08-11 | `6409b1a1` | `refs/archive/pr/429` |
| [#430](pr-0430.md) | WS-27ak slice 2: the Toast primitive on Base UI (verified 2026-08-12) | `merged` | @vjvarada | 2026-08-12 | `2b11f43a` | `refs/archive/pr/430` |
| [#431](pr-0431.md) | WS-27be (DRAFT, unverified): pg_trgm search index — agent died before R8 verification ran | `merged` | @vjvarada | 2026-08-12 | `7e1ac31f` | `refs/archive/pr/431` |
| [#432](pr-0432.md) | WS-1: route BO-1a's unrouted ClickUp writers + BO-1b's queued-write sync state (+ mints BO-1d) | `merged` | @vjvarada | 2026-08-12 | `7d57bb99` | `refs/archive/pr/432` |
| [#433](pr-0433.md) | docs(WS-27): correct the board — Wave 2 is merged and deployed, not "dispatched" | `merged` | @vjvarada | 2026-08-12 | `efd843a2` | `refs/archive/pr/433` |
| [#434](pr-0434.md) | fix(acb_auth): the owner bootstrap could not run on a fresh database | `closed` | @ishaanpilar | 2026-08-12 | `3db04f12` | `refs/archive/pr/434` |
| [#435](pr-0435.md) | feat(WS-27): Project Operations — hierarchy, blockers, time tracking, and the management aggregates | `closed` | @ishaanpilar | 2026-08-12 | `—` | `refs/archive/pr/435` |
| [#436](pr-0436.md) | feat(WS-27bt/bu/ak-2): repaint RapidTool, name the people, add Tooltip | `closed` | @ishaanpilar | 2026-08-12 | `8e9af925` | `refs/archive/pr/436` |
| [#437](pr-0437.md) | WS-27bg: project run state, archive, and the indicator — plus the task-card data audit (D-PM-25…29) | `merged` | @vjvarada | 2026-08-14 | `a0af10fd` | `refs/archive/pr/437` |
| [#438](pr-0438.md) | WS-28g/g-2: the person record, self-service editing, and People Center spec v2 | `merged` | @vjvarada | 2026-08-14 | `a1ac8644` | `refs/archive/pr/438` |
| [#439](pr-0439.md) | fix(WS-27bg): unify the three automation guards, answer D-PM-20, measure its R8 gate | `merged` | @vjvarada | 2026-08-14 | `aa0d7e30` | `refs/archive/pr/439` |
| [#440](pr-0440.md) | feat(WS-28p/q/k): the working week, the display image, and availability | `merged` | @vjvarada | 2026-08-14 | `471aa3ee` | `refs/archive/pr/440` |
| [#441](pr-0441.md) | feat(WS-28j1/j2): the workload dashboard and its department rollup | `merged` | @vjvarada | 2026-08-14 | `44a07830` | `refs/archive/pr/441` |
| [#442](pr-0442.md) | feat(WS-31/30/32): the Control Plane, the billing console, and the organisation's own mark | `merged` | @vjvarada | 2026-08-14 | `4934eb8c` | `refs/archive/pr/442` |
| [#443](pr-0443.md) | docs(WS-27bj): correct three migration-number references the renumber left behind | `closed` | @vjvarada | 2026-08-14 | `—` | `refs/archive/pr/443` |
| [#444](pr-0444.md) | docs: close out the session — record two merges, and correct a fence claim I got backwards | `merged` | @vjvarada | 2026-08-14 | `924e0ed2` | `refs/archive/pr/444` |
| [#445](pr-0445.md) | docs: correct four deploy claims the board had wrong, all in the same direction | `merged` | @vjvarada | 2026-08-14 | `2f97c87c` | `refs/archive/pr/445` |
| [#446](pr-0446.md) | fix(WS-27bj): three migration references the renumber got wrong, one of them mine | `merged` | @vjvarada | 2026-08-14 | `90a9d22d` | `refs/archive/pr/446` |
| [#447](pr-0447.md) | feat(WS-28h/d): structured skills & credentials, and the capability search over them | `merged` | @vjvarada | 2026-08-14 | `f6f48e8e` | `refs/archive/pr/447` |
| [#448](pr-0448.md) | feat(D39): carry pending work across sessions in the repo, not in memory | `merged` | @vjvarada | 2026-08-15 | `52647177` | `refs/archive/pr/448` |
| [#449](pr-0449.md) | docs(WS-32): run the hydration measurement — three hypotheses dead, one left with a mechanism | `merged` | @vjvarada | 2026-08-15 | `6bc6a1bf` | `refs/archive/pr/449` |
| [#450](pr-0450.md) | feat(WS-28e/j3): the directory-backed assignee picker, and the rebalancing suggestions | `merged` | @vjvarada | 2026-08-15 | `28919499` | `refs/archive/pr/450` |
| [#451](pr-0451.md) | WS-28m + WS-28l + WS-28c: coverage & data quality, the Center landing, and the org chart | `merged` | @vjvarada | 2026-08-15 | `aabbde35` | `refs/archive/pr/451` |
