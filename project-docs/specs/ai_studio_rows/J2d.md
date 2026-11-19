# J2d

Tier: HIGH

Gate: AGENT-SAFE. Dark behind `STUDIO_ORGS`. The chat tool is in its own pack, `studio-share`, which stays off until the owner adds it to `TOOL_PACKS`

## What

🔨 **BUILT, dark (2026-11-19, task 3765, WS-TOF TOF-4b).** **The chat tool, the Needs row and the purge rule of a skill share.** It builds on J2c (TOF-4a). It adds no table, no migration and no flag.

**The owner's read.** `GET /studio/skills/{id}/share` gives the owner the share columns of one skill (`skills_store.get_share`). Another member, another org and no such skill get 404. The route reads `STUDIO_ORGS` first. J2c has no read for the owner, so this route closes that gap.

**The chat tool.** `studio_skill_share` is the one tool of the pack `studio-share`, of the owner `studio-builder`. The pack has no surface, and it is not in `FOLLOWS`. So a plain Studio turn holds no new tool and pays no new token. The pack loads by its words, such as "share my skill" and "skill share", or by the loader.

**The acts.** The tool takes `skill_id`, `act` and `version`, and no org and no member (R5). Each act sends a J2c route, so the route stays the one rule:

1. `status` sends the owner's read above. It shows no card and writes nothing.
2. `ask` shows ONE card with the text of the asked version. Only `approved` sends `POST /studio/skills/{id}/versions/{n}/share`. The answer says that the ask is pending and that a holder of `studio:publish` must approve it. Version 0 asks for the newest version.
3. `unshare` shows ONE card, then sends `DELETE /studio/skills/{id}/share`. The chat unshares a skill of the member only.
4. `approve` and `reject` are for a holder. The tool reads `GET /studio/skill-shares?state=pending`. It shows ONE card with the full text, the owner and the hash. Only `approved` sends the J2c approve or reject route.
5. Before each card, the tool refuses what the route would refuse, so no card shows for a move that fails. After the card, it reads again and compares. A change gives "The skill changed while the card was open".

**An approve needs the whole text.** J2b default 2 says that the approver reads the full text first. So a card that the card builder cuts cannot approve. The answer tells the holder to ask the owner for a shorter version. A reject still works on a cut card, because a reject only narrows the share.

**The Needs row.** The needs source `studio` gives a holder one row for each pending skill ask of another member. The provider calls `skills.list_skill_shares` and writes no SQL. It drops each ask of the holder, because the store refuses that approve. The row reuses the kind `approval` with app `studio`. It holds the skill name, the version and who asked, and no body and no hash.

**Where the row opens.** The row has no act, because an approval never runs in one click. It opens a new chat with a drafted message (`/chat?do=new-chat&q=...`). The member reads the words, then sends them. The words name the act (approve or reject), the skill id and the version, and no member text.

The model then calls `act=approve`, which shows the card with the whole text. The holder approves or rejects on the card.

**The flags of the source.** `STUDIO_ORGS` off gives `absent`, as before, so the needs answer is the same byte for byte. `PACKAGE_HOST_ORGS` now gates the app rows only, because a skill share needs no install (J2c). This changes the J2b-4a row.

**The member purge** (the orchestrator's decision, 2026-11-19). A skill that is shared with the org stays with the org when its owner leaves. Shared means `visibility = 'org' AND shared_version IS NOT NULL`. The skill keeps `owner_email`, as a shared saved view does, as above. The purge deletes each other skill of the member, as before: a private skill, and a skill with only a pending or a rejected ask.

**The two clauses.** The clauses of `routes/admin/members.py` split the member's skills of the bound org with no overlap and no gap. The answer counts the kept rows as `studio_shared_skills`.

**Known limits.**

- **Unshare of a kept skill.** The follow-up below closes this limit. A holder lists the shared skills and ends a share from the chat. Nobody can pause or publish a kept skill whose owner left.
- **Open owner decision.** Keep a shared skill when its owner is purged, or delete it. The purge keeps it for now.
- **Loads once published.** The loader needs `state = 'published'`, and a new saved version moves a skill to draft. So the tool text says that members load the version once the skill is published. The tool cannot read the state.

- A kept skill keeps each version, also each version after `shared_version`. No other member loads them.
- After a holder unshares a kept skill, the skill is private. No member can reach it, and no purge deletes it.
- A pending ask of a later version of a kept skill stays in the Needs row. A holder may approve or reject it.
- In production, `STUDIO_ORGS` can name each org. Then the Needs row, the owner's read and the purge rule go live at the next release. The chat tool waits for the `TOOL_PACKS` line.

**The follow-up: the holder's list and unshare** (🔨 BUILT, dark, 2026-11-19, task 3765). It adds no table, no migration and no flag. `STUDIO_ORGS` gates it, as it gates each share route. The owner gate is the `TOOL_PACKS` line of `studio-share`, as above.

1. `GET /studio/skill-shares?state=approved` gives a holder of `studio:publish` each skill that the bound org shares. The one test is `skills_store.SHARED_WITH_ORG`. So a skill still shows while a newer ask waits, or after a reject. A private skill never shows. The list is in name order, because a holder looks for a skill by its name. Each row holds the name, the slug, the description, the owner, `shared_version` and the newest ask, and no body. At most 50 rows, with `truncated`.
2. Each row also holds `approved_by` and `approved_at`. They name the approval of `shared_version` only while the newest ask is that approval. A newer ask clears the decision columns, and a reject names the rejecter, so the row then gives NULL. This follow-up adds no column.
3. `skills_store.list_shared_skills` runs `_share_right` before any statement, so a member without the right gets 403 before any session. The statement filters on the bound org, and RLS binds it too. It does not filter on the member, as the pending list does not. Each state other than `pending` and `approved` is 422.
4. The chat tool has a sixth act, `shared`. It reads that list and shows no card. The rows give the id, the name, the owner and the versions. `skill_id` is now optional, because `shared` needs no skill.
5. The chat `unshare` keeps the owner path. When the owner's read gives 404, the tool reads the holder's list. A member without the right gets 403 there, so the tool gives the old 404 text and shows no card. A skill that is not in the list gives no card either.
6. For a skill in the list, ONE card names the skill and its owner. Its detail says "The other members stop loading it". Only `approved` sends `DELETE /studio/skills/{id}/share`, and a steer gives `revised`, which never sends. The tool reads the list again first, and a change gives "The skill changed while the card was open".
7. The DELETE route checks the right again, and the store reads the session member (R5). The unshare clears the share columns as the owner's unshare does. The result text says that only the owner loads the skill now.

Fences: `test_studio_skill_share.py` (the 403, the 422, the statement and the approver rule), `test_studio_skill_share_r8.py` (`test_r8_the_holder_lists_only_the_shared_skills_of_the_bound_org`, two orgs), `test_studio_skill_share_chat.py` (section 9) and `test_studio_skill_share_chat_r8.py` (`test_r8_a_holder_lists_and_ends_the_share_of_another_member_from_the_chat`).

**Not built.** No page and no button. The holder decides in the chat. No feed signal, so My Day shows no card for the row. No share to a group or to named people.

## Done when

Owner Owen asks from the chat. A card shows, and nothing is written before Approve. After Approve, the share reads `pending`. Member Mel and org B get 404 from the tool and the route. Holder Ada sees one Needs row for the ask of Owen, and Owen never sees his own ask.

Ada approves from the chat, and the approve of Owen gets 403. Owen unshares from the chat. With `STUDIO_ORGS` empty, the tool says that AI Studio is not open, and the route answers 404. The needs answer is then the same byte for byte. A purge of Owen keeps his shared skill and deletes his private skill. The skills of org B stay.

Fences: `test_studio_skill_share_chat.py`, `test_studio_skill_share_chat_r8.py` (R8, two orgs, in `r8.must_run`), `test_shell_needs.py`, `test_admin_member_purge.py`, `test_studio_skills_r8.py`, `test_studio_route_gate.py`, `test_tool_packs_registry.py` and `test_pack_cues.py`
