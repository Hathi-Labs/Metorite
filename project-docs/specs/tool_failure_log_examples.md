# Tool failure log: the golden fingerprints

<!-- ste-tier: strict -->

**Status: an appendix of `tool_failure_log.md` §3.4, PROPOSED with it,
2026-11-16.** It owns no work. The rules of `tool_failure_log.md` §3.2 make
each row. TOF-2b uses the table as golden cases in
`tests/unit/test_failure_fingerprint.py`.

Each fingerprint below is the real hash of its line, `v1|tool|error_code|exc_type|code_site`.

| # | What happened | `tool` | `error_code` | `exc_type` | `code_site` | Fingerprint |
|---|---|---|---|---|---|---|
| 1 | `crm_get_record` raised `KeyError("Amount for Ravi Kumar")` in org A | `crm_get_record` | `exception` | `builtins.KeyError` | `acb_skills.crm_tools:crm_get_record` | `00bc2542a73b3967` |
| 2 | The same tool raised `KeyError("Amount for Acme Pvt Ltd")` in org B | `crm_get_record` | `exception` | `builtins.KeyError` | `acb_skills.crm_tools:crm_get_record` | `00bc2542a73b3967` |
| 3 | The model called `create_task`, which the turn did not load | `create_task` | `unknown_tool` | `-` | `-` | `62cd618bc53a3db5` |
| 4 | The model called `send_invoice_to_ravi`, which no registry holds | `other` | `unknown_tool` | `-` | `-` | `4ad4e9086e5c7ed2` |
| 5 | `create_tasks` got `tasks="{bad json"`, and HRD-3 refused it | `create_tasks` | `bad_arguments` | `-` | `-` | `9917de113c04eb0f` |
| 6 | `POST /crm/deals/{deal_id}/contacts` raised a foreign key error that names `ravi@acme.in` | `route:POST /crm/deals/{deal_id}/contacts` | `unhandled_500` | `asyncpg.exceptions.ForeignKeyViolationError` | `gateway.routes.crm.deal_contacts:add_deal_contact` | `1203edc40671d7dd` |
| 7 | `GET /crm/aggregate?owner=meera@x.in` raised `ZeroDivisionError` | `route:GET /crm/aggregate` | `unhandled_500` | `builtins.ZeroDivisionError` | `gateway.routes.crm.aggregate:aggregate` | `0686899b24462723` |
| 8 | The chat called the MCP server "shaurrya-app", and it timed out | `mcp` | `mcp_timeout` | `-` | `-` | `c66c2e978c30822d` |
| 9 | A Studio view read the MCP server "acme-erp" of org C, and it timed out | `mcp` | `mcp_timeout` | `-` | `-` | `c66c2e978c30822d` |
| 10 | The Projects pack tool `create_task` raised `KeyError` in its body | `create_task` | `exception` | `builtins.KeyError` | `skill_projects.writes:create_task` | `bbdc60d2363e37a9` |

Rows 1 and 2, and rows 8 and 9, show that tenant text does not split or
enter a fingerprint. Row 7 shows that a query string never reaches the
template. Row 10 shows that a frame under `apps/skills` wins over the guard
frame. Rows 6, 7 and 10 use real code at `origin/main` `20ef30cfb`.
