# I8b-1a

Tier: HIGH

Gate: AGENT-SAFE. A migration

## What

🔨 **BUILT, dark (2026-11-14), task 3215.** The store and the routes of an ask, backend only (§8.2). No UI, no Needs-you provider and no new flag. **The migration** (443 at build) adds `integration_attention`: the kind (`ask` or `broken`), the provider, `member_email`, `package_id` and `asked_at`. The key of the package is `(organization_id, package_id)` to `apps`, with ON DELETE CASCADE. A partial unique index keeps one ask for each org, provider and member. The table has the tenant `organization_id` and FORCE RLS, and the generated phases carry it. **The store:** `connections/attention.py` (`ask`, `withdraw`, `asked_by`, `open_asks`, `clear_on_connect` and `prune`). **The clear:** the six registry writers of `connections/store.py` call one hook. It deletes the asks of the provider only when the row is `connected` at org scope, and only while `STUDIO_CONNECTION_GATE_ORGS` holds the org. It runs in a savepoint, so a failure never stops a connect. **The routes:** `POST` and `DELETE /studio/packages/{id}/connections/ask`, and `asked` on the read of I8a. **Retention:** a prune hook of the job worker deletes each ask older than 30 days. **The purge:** `connection_asks` in `routes/admin/members.py`. **Tests:** `test_studio_connection_ask.py` and `test_studio_connection_ask_r8.py` (R8)

**With I9a (merged on main):** `already_met` and `met` on the line of the read use one rule, `_need_met`. It reads the set of the account of the need. So an `org` need reads the org rows only, and a member row of the asker never makes an org ask met.

## Done when

An ask of a member makes one row, and a second ask makes no second row. A connect at org scope clears it. A withdraw deletes the row of the asker only. With either flag off, the routes answer 404, and the hook sends no SQL when the gate (`STUDIO_CONNECTION_GATE_ORGS`) is off. The 11th new live ask of one member answers 429 `too_many_asks`. A second ask from another draft keeps the time and moves the row to the newest draft. R8: a session of org B reads and deletes no row of org A
