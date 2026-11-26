# TOF-1d

Tier: STD

Gate: AGENT-SAFE, dark. Task 3413, epic 3393 (WS-TOF, D224 rung 1). No migration. Dark behind `STUDIO_SKILL_OFFER_ORGS`. The owner preview (D145) gates the flag flip

## What

🔨 **BUILT, dark (2026-11-16, task 3413).** **The skill offer reads its flag for each org, and it counts the reads of the chat tools.** Before this slice, the offer of F6a was dark behind `NEXT_PUBLIC_SKILL_OFFER`. Next inlines that flag at build time, so a flip needed a rebuild and held for every org. The offer also mapped only Knowledge and `run_workflow`, so a CRM, Projects or Email turn got no offer.

**The flag.** `STUDIO_SKILL_OFFER_ORGS` has the grammar of the other `*_ORGS` flags. Its one reader is `skill_offer_allowed` in `gateway/routes/studio/flags.py`. `GET /auth/me/shell` serves the answer as `skill_offer`, beside `guide` and `module_runs`.

The answer is true only when `STUDIO_ORGS` holds the org too, because the catalog needs it. The org comes from the session. `READ_ONLY_ANSWER_KEYS` drops the key from a save, and no row stores it. This slice adds no endpoint and no second way to read a flag.

**The client.** `lib/shell/shellPrefs.ts` holds `useSkillOfferOn`, the twin of `useModuleRunsOn`. The shell read is its one writer. A member change resets it to off. `AgentChat`, `MessageBubble` and `SkillOfferCard` read it.

With the flag off, the card draws nothing and the chat reads no catalog for it. `NEXT_PUBLIC_SKILL_OFFER` and `lib/studio/skillOfferFlag.ts` are gone.

**The rule for a chat tool call.** A tool of `SUCCESS_MARKS` still counts by its mark alone. Any other tool counts as a READ when four things hold. The call finished. It is no `Refused:` text and no `ok:false` JSON. Its answer is not empty. Its answer does not start with a word of `NON_ANSWER_HEADS`.

After the catalog loads, `onlyReadRows` also needs the row of the call. The row has the source `tool` and the class A, and it is the one row of its tool. A write or a send never counts. A turn of an org with no `tool:` row maps no call, so it gets no offer.

**How the rule was checked, and its limit.** The chat tools of CRM, Projects and Email answer in plain text. A gateway refusal starts with `Refused:` (`acb_common.tool_refusals`), and the chat already drops it. Other failures have no common mark.

Examples are `CRM_OFF`, `CRM_CLOSED` and the email `_CANNOT_SEE`. The Projects writes answer "A task needs a title." and "Nothing to change.". So a generic mark for all tools cannot be safe.

The slice limits the generic tier to the tools that say `read_only` (class A). It lists the plain-text non-answers that it found, and a test reads each source file to hold each text real. The tier is weaker than a mark, because a failure text that no entry names counts as an answer.

The cost is one card that the member can close. The draft holds no argument and no result, and Save makes a private draft only.

**Not changed.** The draft, `MIN_CALLS = 3`, the private draft save, the typed-words guarantee and the rest of the five rules.

## Done when

With the flag on for an org, a turn of three CRM or Projects reads shows the offer, and Save makes a private draft skill. With the flag off, nothing renders and the chat reads no catalog for it.

Fences: `skillOffer.test.ts` (the offer on chat tool calls), `SkillOfferCard.test.ts` (the flag-off render test), `shellPrefs.test.ts`, `tests/unit/test_auth_me_shell.py` and `tests/unit/test_flag_reload.py`. Flag reload: `docs/flag_reload.md`. A flip in production is OWNER-GATE (`enforcement-flip`), after the owner preview.

## Before the flip

1. **Core CRM reads have no row.** The core `crm` family has no owner (TOF-1a, "Before the flip", item 2). So `crm_list_records` and its twins map no call. The CRM reads of the `crm-records` pack, such as `search_crm`, do map. The owner must decide whether the core reads join the scope.
2. **Email reads are class B.** The email reads say `read_only=False` (TOF-1a, decision 1), so they are class B rows. The read tier needs class A, so an Email turn gets no offer today. The owner decides whether the email reads may say `read_only=True`. The other choice is that the offer counts a class B read, and that choice also counts each write.
3. **The flag needs the catalog.** The offer maps a call only while the org is on `STUDIO_CHAT_TOOLS_ORGS` and `STUDIO_ORGS`. Flip the three together for the preview org.
