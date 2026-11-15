# I8d

Tier: STD

Gate: AGENT-SAFE. After I8a. Dark: with either flag off, the read answers 404 and the card draws nothing

## What

🔨 **BUILT, dark (2026-11-14), task 3217.** **The chat card** (an Opus audit of 2026-11-14 narrowed it). No new chat tool and no new pack: the studio pack holds 23 tools, and a 24th would drop the whole pack. The card rides the finished, successful `studio_save` and `studio_create_from_template` events. It reads `result.package_id` (a UUID), and draws one card for each package in a turn (`lib/studio/connectCard.ts`, `components/studio/StudioConnectCard.tsx`, mounted by `StudioToolCards`). The card reads `GET /api/studio/packages/{id}/connections` of I8a, in the cache of the IDE. It draws the I8a `ConnectLines` and opens the I8a `ConnectSheet`, and only when a name is unmet. A 404 (a flag is off, or the member is not the owner) draws nothing. The return stays `/studio/build?pkg=<id>`, and `STUDIO_PREFIX` is not wider. A save drops the connections reads (`announceAssistantWrite`), so the card reads again. **Mira text: skipped.** A `connections` note in the save result needs a new HTTP read on each save of the live studio pack, so it waits for a decision. `_STUDIO_RULES` and the tool docstrings did not change. **Tests:** `src/components/studio/StudioConnectCard.test.ts` (it holds `src/lib/studio/connectCard.ts`), `src/lib/chatCardConformance.test.ts`, `tests/unit/test_studio_connect_card.py`

## Done when

In an org that both flags list, a chat `studio_save` of a draft that names an unmet `mcp:<server>` shows one Connect card in that turn. Its Connect opens the I8a sheet with the same server link. A test holds three facts. No Studio tool parameter is a secret name (`is_secret_key`). The card source holds no Input or textarea. The save result holds only server names, and none of them looks like a secret. Visual review in four contexts. ⚠️ A test cannot stop a model from putting a typed key into a tool argument or the transcript. The manifest validator refuses a secret-shaped string in a draft (`SECRET_SHAPES`), and a connection name must match `CONNECTION_NAME_RE`, so a key never gets STORED. A full guarantee needs a transcript scrub, which is out of scope. The done-when is: Mira never asks and never carries a key
