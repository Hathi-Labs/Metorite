"""The CRM manifest: every ``/crm`` route, and what each door does with it.

Spec: ``project-docs/specs/crm_conversational.md`` §8 (CRM-M2a) and
``external_agent_access.md`` §19.9 (the classes), §19.3 and D136.1. Board
row WS-CRM (CRM-M2a, task 2047).

**One manifest for the CRM.** CRM-M2a builds this table with the door off.
Each row is ``off_door``, so the MCP door serves no CRM op. MCP-P2a (task
866) sets the door op of each row, and it adds ``CRM`` to ``APPS``. It does
not build this table again.

**What a row says.** Each row of class A, B or C names the chat tool that
does it, or the slice of the CRM-M2 table that plans one (``planned``). A
row of class X carries the reason why no door reaches it. Class X is for a
route that no chat tool should ever send: a provider sign-in and a download.
Each setup write is class B or C, because the owner rule of chat parity
(2026-11-01) gives the chat a tool for it.

**The fence.** ``tests/unit/test_crm_ui_actions.py`` walks the real CRM
router and fails on a route with no row, on a row with no route, on a client
request with no row, and on a tool that the table names but its source does
not send. It also holds the count of planned rows to a ceiling that only
goes down. ``acb_skills.chat_parity`` decides each ``/api/crm`` action from
these rows, as it does for Knowledge.
"""

from __future__ import annotations

from acb_common.app_manifests.base import App, Route

#: The door is off for every row (CRM-M2a). MCP-P2a replaces each reason.
_DOOR_READ = (
    "CRM-M2a builds this table with the door off. MCP-P2a sets the door op of "
    "this row."
)
_DOOR_WRITE = (
    "CRM-M2a builds this table with the door off. MCP-P2a sets the door op of "
    "this row, and a write waits for the approval of the member (AD-18)."
)

#: §19.9: the setup of the CRM is an admin act in CRM settings.
_SETUP = (
    "§19.9 and D134: an admin sets up the CRM in CRM settings. The door stays "
    "off until MCP-P2a decides this row."
)

#: D134.2: a person answers the questions and the suggestions of the AI.
_DECISION = (
    "D134.2: a person answers the questions and the suggestions of the AI in "
    "CRM. The door stays off until MCP-P2a decides this row."
)

#: AD-11: a route that runs a model stays off the door.
_AI_RUN = "AD-11: this route runs a model, so it stays off the door."

#: §19.9: a saved view is the member's own screen.
_VIEW = (
    "§19.9: a saved view is the screen of the member. The door stays off until "
    "MCP-P2a decides this row."
)

#: §19.11: the connection to Zoho, the sync, the import, the cutover and the reset.
_CONNECTION = (
    "§19.11: connect, sync, import, cut over and reset are not for an outside "
    "agent. The door stays off."
)

#: A CSV file is a download of the app. The door gives the records.
_EXPORT = (
    "A CSV file is a download of the app. The door reads the records through "
    "its own ops."
)

#: The exempt kind ``download`` of the chat parity table.
_DOWNLOAD = (
    "The ZIP file of every CRM record is a download for the browser. The chat "
    "reads the records."
)

#: The exempt kind ``oauth`` of the chat parity table.
_OAUTH = (
    "The Zoho sign-in is an act of the member in the browser (§19.11). The "
    "chat does not sign in to a provider."
)

CRM = App(
    id="crm",
    name="CRM",
    reach="The leads, deals, contacts and organizations that the member can read.",
    pane="/crm",
    feature="crm",
    router="gateway.routes.crm",
    routes=(
        # ── records ──
        Route("GET", "/crm/leads", "search_crm", "A",
              also=("crm_list_records",), off_door=_DOOR_READ),
        Route("GET", "/crm/leads/{record_id}", "get_record", "A",
              also=("crm_get_record",), off_door=_DOOR_READ),
        Route("GET", "/crm/leads/{record_id}/timeline", "get_timeline", "A", off_door=_DOOR_READ),
        Route("POST", "/crm/leads/{record_id}/activities", "log_activity", "B",
              off_door=_DOOR_WRITE),
        Route("PATCH", "/crm/leads/{record_id}", "crm_update_record", "B", off_door=_DOOR_WRITE),
        Route("DELETE", "/crm/leads/{record_id}", "", "C", planned="CRM-M2c", off_door=_DOOR_WRITE),
        Route("POST", "/crm/leads", "create_lead", "B", off_door=_DOOR_WRITE),
        Route("GET", "/crm/export/leads.csv", "", "A", planned="CRM-M2l", off_door=_EXPORT),
        Route("GET", "/crm/deals", "search_crm", "A",
              also=("crm_list_records",), off_door=_DOOR_READ),
        Route("GET", "/crm/deals/{record_id}", "get_record", "A",
              also=("crm_get_record",), off_door=_DOOR_READ),
        Route("GET", "/crm/deals/{record_id}/timeline", "get_timeline", "A", off_door=_DOOR_READ),
        Route("POST", "/crm/deals/{record_id}/activities", "log_activity", "B",
              off_door=_DOOR_WRITE),
        Route("PATCH", "/crm/deals/{record_id}", "crm_update_record", "B", off_door=_DOOR_WRITE),
        Route("DELETE", "/crm/deals/{record_id}", "", "C", planned="CRM-M2c", off_door=_DOOR_WRITE),
        Route("POST", "/crm/deals", "", "B", planned="CRM-M2c", off_door=_DOOR_WRITE),
        Route("GET", "/crm/export/deals.csv", "", "A", planned="CRM-M2l", off_door=_EXPORT),
        Route("GET", "/crm/contacts", "search_crm", "A",
              also=("crm_list_records",), off_door=_DOOR_READ),
        Route("GET", "/crm/contacts/{record_id}", "get_record", "A",
              also=("crm_get_record",), off_door=_DOOR_READ),
        Route("GET", "/crm/contacts/{record_id}/timeline", "get_timeline", "A",
              off_door=_DOOR_READ),
        Route("POST", "/crm/contacts/{record_id}/activities", "log_activity", "B",
              off_door=_DOOR_WRITE),
        Route("PATCH", "/crm/contacts/{record_id}", "crm_update_record", "B", off_door=_DOOR_WRITE),
        Route("DELETE", "/crm/contacts/{record_id}", "", "C",
              planned="CRM-M2c", off_door=_DOOR_WRITE),
        Route("POST", "/crm/contacts", "", "B", planned="CRM-M2c", off_door=_DOOR_WRITE),
        Route("GET", "/crm/export/contacts.csv", "", "A", planned="CRM-M2l", off_door=_EXPORT),
        Route("GET", "/crm/organizations", "search_crm", "A",
              also=("crm_list_records",), off_door=_DOOR_READ),
        Route("GET", "/crm/organizations/{record_id}", "get_record", "A",
              also=("crm_get_record",), off_door=_DOOR_READ),
        Route("GET", "/crm/organizations/{record_id}/timeline", "get_timeline", "A",
              off_door=_DOOR_READ),
        Route("POST", "/crm/organizations/{record_id}/activities", "log_activity", "B",
              off_door=_DOOR_WRITE),
        Route("PATCH", "/crm/organizations/{record_id}", "crm_update_record", "B",
              off_door=_DOOR_WRITE),
        Route("DELETE", "/crm/organizations/{record_id}", "", "C",
              planned="CRM-M2c", off_door=_DOOR_WRITE),
        Route("POST", "/crm/organizations", "", "B", planned="CRM-M2c", off_door=_DOOR_WRITE),
        Route("GET", "/crm/export/organizations.csv", "", "A", planned="CRM-M2l", off_door=_EXPORT),
        Route("GET", "/crm/export/all.zip", "", "X", reason=_DOWNLOAD),
        # ── owners ──
        Route("GET", "/crm/owners", "crm_list_owners", "A", off_door=_DOOR_READ),
        # ── activities ──
        Route("GET", "/crm/activities/{activity_id}", "", "A",
              planned="CRM-M2l", off_door=_DOOR_READ),
        Route("PATCH", "/crm/activities/{activity_id}", "crm_update_task", "B",
              off_door=_DOOR_WRITE),
        Route("DELETE", "/crm/activities/{activity_id}", "", "C",
              planned="CRM-M2c", off_door=_DOOR_WRITE),
        # ── change-history ──
        Route("GET", "/crm/change-history", "crm_change_history", "A", off_door=_DOOR_READ),
        Route("POST", "/crm/change-history/{change_id}/undo", "", "B",
              planned="CRM-M2c", off_door=_DOOR_WRITE),
        # ── statuses ──
        Route("GET", "/crm/statuses/{kind}", "crm_aggregate", "A",
              also=("crm_list_records",), off_door=_DOOR_READ),
        Route("POST", "/crm/statuses/{kind}", "", "B", planned="CRM-M2e", off_door=_SETUP),
        Route("PUT", "/crm/statuses/{kind}/order", "", "B", planned="CRM-M2e", off_door=_SETUP),
        Route("POST", "/crm/statuses/{kind}/order/reset", "", "B",
              planned="CRM-M2e", off_door=_SETUP),
        Route("PATCH", "/crm/statuses/{kind}/{status_id}", "", "B",
              planned="CRM-M2e", off_door=_SETUP),
        Route("DELETE", "/crm/statuses/{kind}/{status_id}", "", "C",
              planned="CRM-M2e", off_door=_SETUP),
        # ── lost-reasons ──
        Route("GET", "/crm/lost-reasons", "update_deal_status", "A", off_door=_DOOR_READ),
        Route("POST", "/crm/lost-reasons", "", "B", planned="CRM-M2e", off_door=_SETUP),
        Route("PATCH", "/crm/lost-reasons/{reason_id}", "", "B",
              planned="CRM-M2e", off_door=_SETUP),
        Route("DELETE", "/crm/lost-reasons/{reason_id}", "", "C",
              planned="CRM-M2e", off_door=_SETUP),
        # ── fields ──
        Route("GET", "/crm/fields/{entity}", "crm_list_fields", "A", off_door=_DOOR_READ),
        Route("POST", "/crm/fields/{entity}", "", "B", planned="CRM-M2f", off_door=_SETUP),
        Route("PATCH", "/crm/fields/{entity}/{key}", "", "B", planned="CRM-M2f", off_door=_SETUP),
        Route("DELETE", "/crm/fields/{entity}/{key}", "", "C", planned="CRM-M2f", off_door=_SETUP),
        Route("POST", "/crm/fields/{entity}/{key}/restore", "", "B",
              planned="CRM-M2f", off_door=_SETUP),
        # ── field-sections ──
        Route("POST", "/crm/field-sections/{entity}", "", "B", planned="CRM-M2f", off_door=_SETUP),
        Route("PATCH", "/crm/field-sections/{entity}/{section_id}", "", "B",
              planned="CRM-M2f", off_door=_SETUP),
        Route("DELETE", "/crm/field-sections/{entity}/{section_id}", "", "C",
              planned="CRM-M2f", off_door=_SETUP),
        # ── field-layout ──
        Route("PUT", "/crm/field-layout/{entity}", "", "B", planned="CRM-M2f", off_door=_SETUP),
        # ── field-rules ──
        Route("GET", "/crm/field-rules", "", "A", planned="CRM-M2f", off_door=_DOOR_READ),
        Route("POST", "/crm/field-rules", "", "B", planned="CRM-M2f", off_door=_SETUP),
        Route("PATCH", "/crm/field-rules/{rule_id}", "", "B",
              planned="CRM-M2f", off_door=_DECISION),
        # ── catalogue ──
        Route("GET", "/crm/catalogue", "crm_list_modules", "A", off_door=_DOOR_READ),
        Route("PUT", "/crm/catalogue/{module}", "", "B", planned="CRM-M2f", off_door=_SETUP),
        # ── conversion-mapping ──
        Route("GET", "/crm/conversion-mapping", "", "A", planned="CRM-M2i", off_door=_DOOR_READ),
        Route("PUT", "/crm/conversion-mapping", "", "B", planned="CRM-M2i", off_door=_SETUP),
        # ── reports ──
        Route("GET", "/crm/reports/pipeline", "crm_report", "A", off_door=_DOOR_READ),
        Route("GET", "/crm/reports/funnel", "crm_report", "A", off_door=_DOOR_READ),
        Route("GET", "/crm/reports/win-loss", "crm_report", "A", off_door=_DOOR_READ),
        Route("GET", "/crm/reports/owners", "crm_report", "A", off_door=_DOOR_READ),
        # ── pipeline ──
        Route("GET", "/crm/pipeline", "get_pipeline", "A", off_door=_DOOR_READ),
        # ── records ──
        Route("POST", "/crm/leads/{lead_id}/convert", "convert_lead", "C", off_door=_DOOR_WRITE),
        # ── modules ──
        Route("GET", "/crm/modules", "crm_list_modules", "A", off_door=_DOOR_READ),
        Route("GET", "/crm/modules/{module}/records", "crm_list_module_records", "A",
              off_door=_DOOR_READ),
        Route("GET", "/crm/modules/{module}/records/{record_id}", "crm_get_module_record", "A",
              off_door=_DOOR_READ),
        Route("GET", "/crm/modules/{module}/records/{record_id}/timeline", "", "A",
              planned="CRM-M2l", off_door=_DOOR_READ),
        Route("PATCH", "/crm/modules/{module}/records/{record_id}", "", "B",
              planned="CRM-M2d", off_door=_DOOR_WRITE),
        # ── aggregate ──
        Route("GET", "/crm/aggregate", "crm_aggregate", "A", off_door=_DOOR_READ),
        Route("GET", "/crm/aggregate/stage-dwell", "crm_stage_dwell", "A", off_door=_DOOR_READ),
        # ── analysis ──
        Route("POST", "/crm/analysis/rerun", "", "B", planned="CRM-M2k", off_door=_AI_RUN),
        Route("GET", "/crm/analysis/status", "", "A", planned="CRM-M2l", off_door=_DOOR_READ),
        # ── connection ──
        Route("GET", "/crm/connection", "", "A", planned="CRM-M2l", off_door=_DOOR_READ),
        Route("POST", "/crm/connection/zoho/start", "", "X", reason=_OAUTH),
        # ── oauth ──
        Route("GET", "/crm/oauth/zoho/callback", "", "X", reason=_OAUTH),
        # ── connection ──
        Route("POST", "/crm/connection/disconnect", "", "C",
              planned="CRM-M2k", off_door=_CONNECTION),
        # ── cutover ──
        Route("GET", "/crm/cutover/preview", "", "A", planned="CRM-M2l", off_door=_DOOR_READ),
        Route("POST", "/crm/cutover/start", "", "C", planned="CRM-M2k", off_door=_CONNECTION),
        Route("GET", "/crm/cutover/status", "", "A", planned="CRM-M2l", off_door=_DOOR_READ),
        Route("POST", "/crm/cutover", "", "C", planned="CRM-M2k", off_door=_CONNECTION),
        # ── deals ──
        Route("GET", "/crm/deals/{deal_id}/contacts", "crm_deal_contacts", "A",
              off_door=_DOOR_READ),
        Route("POST", "/crm/deals/{deal_id}/contacts", "", "B",
              planned="CRM-M2d", off_door=_DOOR_WRITE),
        Route("DELETE", "/crm/deals/{deal_id}/contacts/{contact_id}", "", "B",
              planned="CRM-M2d", off_door=_DOOR_WRITE),
        # ── digests ──
        Route("GET", "/crm/digests", "", "A", planned="CRM-M2l", off_door=_DOOR_READ),
        # ── import ──
        Route("POST", "/crm/import/zoho", "", "C", planned="CRM-M2k", off_door=_CONNECTION),
        # ── mapping ──
        Route("GET", "/crm/mapping/plans", "", "A", planned="CRM-M2i", off_door=_DOOR_READ),
        Route("GET", "/crm/mapping/plans/{version}", "", "A",
              planned="CRM-M2i", off_door=_DOOR_READ),
        Route("POST", "/crm/mapping/plans", "", "B", planned="CRM-M2i", off_door=_DECISION),
        Route("POST", "/crm/mapping/plans/{version}/approve", "", "C",
              planned="CRM-M2i", off_door=_DECISION),
        Route("POST", "/crm/mapping/plans/{version}/restore", "", "B",
              planned="CRM-M2i", off_door=_DECISION),
        Route("POST", "/crm/mapping/preview", "", "A", planned="CRM-M2i", off_door=_DOOR_READ),
        # ── notes ──
        Route("GET", "/crm/notes/search", "crm_search_notes", "A", off_door=_DOOR_READ),
        # ── pipelines ──
        Route("GET", "/crm/pipelines", "crm_list_pipelines", "A", off_door=_DOOR_READ),
        Route("GET", "/crm/pipelines/preview", "crm_create_pipeline", "A", off_door=_DOOR_READ),
        Route("POST", "/crm/pipelines", "crm_create_pipeline", "B", off_door=_SETUP),
        Route("PATCH", "/crm/pipelines/{pipeline_id}", "", "B", planned="CRM-M2j", off_door=_SETUP),
        Route("POST", "/crm/pipelines/{pipeline_id}/stages", "", "B",
              planned="CRM-M2j", off_door=_SETUP),
        Route("DELETE", "/crm/pipelines/{pipeline_id}/stages/{status_id}", "", "C",
              planned="CRM-M2j", off_door=_SETUP),
        # CRM-M3b: the win chance and the warning days of a stage in one
        # pipeline. It ships dark with no UI, and CRM-M2j gives the chat its tool.
        Route("PATCH", "/crm/pipelines/{pipeline_id}/stages/{status_id}", "", "B",
              planned="CRM-M2j", off_door=_SETUP),
        # ── source-profile ──
        Route("GET", "/crm/source-profile", "", "A", planned="CRM-M2l", off_door=_DOOR_READ),
        # ── questions ──
        Route("GET", "/crm/questions", "", "A", planned="CRM-M2h", off_door=_DOOR_READ),
        Route("POST", "/crm/questions/{question_id}/answer", "", "B",
              planned="CRM-M2h", off_door=_DECISION),
        Route("POST", "/crm/questions/{question_id}/dismiss", "", "B",
              planned="CRM-M2h", off_door=_DECISION),
        Route("DELETE", "/crm/questions/{question_id}/dismiss", "", "B",
              planned="CRM-M2h", off_door=_DECISION),
        Route("POST", "/crm/questions/accept-best", "", "B", planned="CRM-M2h", off_door=_DECISION),
        Route("GET", "/crm/questions/ask", "crm_aggregate", "A",
              also=("crm_answer_question",), off_door=_DOOR_READ),
        Route("POST", "/crm/questions/{question_id}/reply", "crm_answer_question", "B",
              off_door=_DECISION),
        Route("GET", "/crm/questions/mine", "", "A", planned="CRM-M2h", off_door=_DOOR_READ),
        Route("DELETE", "/crm/questions/{question_id}/mine", "", "B",
              planned="CRM-M2h", off_door=_DECISION),
        Route("GET", "/crm/questions/{question_id}/sum-check", "", "A",
              planned="CRM-M2h", off_door=_DOOR_READ),
        # ── rules ──
        Route("GET", "/crm/rules", "", "A", planned="CRM-M2h", off_door=_DOOR_READ),
        Route("POST", "/crm/rules", "", "B", planned="CRM-M2h", off_door=_SETUP),
        Route("PATCH", "/crm/rules/{rule_id}", "", "B", planned="CRM-M2h", off_door=_SETUP),
        Route("DELETE", "/crm/rules/{rule_id}", "", "C", planned="CRM-M2h", off_door=_SETUP),
        Route("POST", "/crm/rules/propose", "crm_propose_rule", "B", off_door=_AI_RUN),
        Route("POST", "/crm/rules/pending/{rule_id}", "crm_answer_question", "B",
              off_door=_DECISION),
        # ── settings ──
        Route("GET", "/crm/settings", "", "A", planned="CRM-M2l", off_door=_DOOR_READ),
        # ── mode ──
        Route("GET", "/crm/mode", "crm_list_records", "A", off_door=_DOOR_READ),
        # ── settings ──
        Route("PUT", "/crm/settings/labels", "", "B", planned="CRM-M2g", off_door=_SETUP),
        Route("PATCH", "/crm/settings", "", "B", planned="CRM-M2g", off_door=_SETUP),
        Route("GET", "/crm/settings/counts", "", "A", planned="CRM-M2l", off_door=_DOOR_READ),
        # ── reset ──
        Route("POST", "/crm/reset", "", "C", planned="CRM-M2k", off_door=_CONNECTION),
        # ── import ──
        Route("POST", "/crm/import/zoho/stages", "", "B", planned="CRM-M2k", off_door=_CONNECTION),
        # ── mapping ──
        Route("GET", "/crm/mapping/suggestions", "", "A", planned="CRM-M2i", off_door=_DOOR_READ),
        Route("POST", "/crm/mapping/suggestions/{slot}/approve", "", "B",
              planned="CRM-M2i", off_door=_DECISION),
        Route("POST", "/crm/mapping/suggestions/{slot}/undo", "", "B",
              planned="CRM-M2i", off_door=_DECISION),
        Route("POST", "/crm/mapping/suggestions/{slot}/dismiss", "", "B",
              planned="CRM-M2i", off_door=_DECISION),
        # ── sync ──
        Route("GET", "/crm/sync/status", "", "A", planned="CRM-M2l", off_door=_DOOR_READ),
        Route("GET", "/crm/sync/short-reads", "crm_list_modules", "A", off_door=_DOOR_READ),
        Route("GET", "/crm/sync/health", "", "A", planned="CRM-M2l", off_door=_DOOR_READ),
        Route("POST", "/crm/sync/zoho", "", "C", planned="CRM-M2k", off_door=_CONNECTION),
        Route("PUT", "/crm/sync/modules/{key}", "", "B", planned="CRM-M2k", off_door=_CONNECTION),
        # ── terms ──
        Route("GET", "/crm/terms/resolve", "crm_aggregate", "A", off_door=_DOOR_READ),
        # ── views ──
        Route("GET", "/crm/views", "", "A", planned="CRM-M2g", off_door=_DOOR_READ),
        Route("POST", "/crm/views", "", "B", planned="CRM-M2g", off_door=_VIEW),
        Route("PATCH", "/crm/views/{view_id}", "", "B", planned="CRM-M2g", off_door=_VIEW),
        Route("DELETE", "/crm/views/{view_id}", "", "C", planned="CRM-M2g", off_door=_VIEW),
    ),
)

#: The rows, for the chat parity fence and the CRM tools.
MANIFEST: tuple[Route, ...] = CRM.routes

__all__ = ["CRM", "MANIFEST"]
