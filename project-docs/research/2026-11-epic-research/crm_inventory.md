# Metorite CRM: inventory and gap analysis (2026-11-10, read-only)

Sources: origin/main specs crm_app.md (3184 lines), crm_platform.md (12247), crm_conversational.md (1120, NOT read);
gateway routes/crm/* (route lists and module headers, not bodies); migration file names 144..386; app/crm file tree;
acb_skills/crm_tools.py tool names; Metorite CRM project list (81 rows, 2026-11-10). Evidence = path or task number.
Status codes: BUILT-LIVE / BUILT-DARK (flag off or empty org list) / SPEC (specified, not built) / MISSING / OTHER-APP.

## 0. What the CRM is today
A Zoho-replacement CRM (WS-26, then WS-53). Two modes per org: Mirror (Zoho is system of record, two-way sync) and
Native (Metorite owns data). Core tables: crm_companies, crm_contacts, crm_leads, crm_deals, crm_deal_contacts (M:N with role),
crm_activities (timeline spine), crm_status_changes, lost reasons, statuses as data (migration 144). Plus generic module records
(251: Zoho custom modules, Products, Quotes as mirrored generic records), field defs/rules/studio, views, settings, change log,
digests, note labels/vectors, sync runs/holds. Users: Fracktal Works, DEWiN, Shaurrya Tele. INR. Zoho cutover (CRM-Z10) due 2026-12-15.

## 1. Checklist

### CONTACTS and COMPANIES
| Item | Status | Evidence |
|---|---|---|
| Records: leads, contacts, companies, deals, CRUD | BUILT-LIVE | routes/crm/records.py; 144_crm.sql |
| Relations (deal-contacts with role, lead convert) | BUILT-LIVE | deal_contacts.py; convert_mapping.py; CRM-U16 |
| Custom modules (Zoho style) | BUILT-LIVE | modules.py; 251; CRM-Z10d done (802) |
| Custom fields editor and layout (field studio) | BUILT-DARK | field_studio.py, 260; CRM_FIELD_STUDIO_ORGS; field-type list NOT verified |
| Lifecycle / lead status as data | BUILT-LIVE | crm_lead_statuses; 288 |
| Owner field and owner list | BUILT-LIVE | records.py GET /owners |
| Dedupe / merge | MISSING in code; SPEC (CRM-S5, I6) | WS-26i merge audited NO-GO; task 739 (E-40) |
| Enrichment | MISSING (CRM-M9 deleted) | |
| Tags | SPEC only (CRM-Q13). No route, no table | crm_platform 20.11 |
| Lists / segments | MISSING (saved views are filters only) | views.py, 250 |
| Import CSV | MISSING in code; SPEC (CRM-S3). Only Zoho import exists | import_zoho.py; 739 |
| Export CSV | BUILT-LIVE | export.py |
| Bulk edit | MISSING in code (no "bulk" in routes); SPEC (S4, WS-26i-bulk) | 739 |
| Activity timeline | BUILT-LIVE (activities, status changes, email threads) | activities.py |
| Notes | BUILT-LIVE; AI labels (Q26) and meaning search (Q27) DARK | 278, 279; CRM_NOTE_SEARCH_ORGS |
| Files on a record | PARTIAL. Zoho attachments not copied (task 803). Native files: not verified | 803 |
| Recycle bin | MISSING (20.11 "not in spec at all") | |
| Change history | BUILT for settings with Undo (change_log.py, 341). Per-record field history SPEC (S10) | 1431, 2597 |

### DEALS and PIPELINES
| Item | Status | Evidence |
|---|---|---|
| Multiple deal pipelines | BUILT-DARK (create, CRM_PIPELINES_ORGS). Stage edit, move deal, per-pipeline win chance open | pipelines.py; 2024 done; 2041 in progress; 2042, 2043, 2046 open |
| Lead pipelines | 2040 done (migration 386); 2045 open | |
| Stages and win probability, rot limit | BUILT-LIVE | stage_metadata.py; 169, 288; WS-26h |
| Board and list views | BUILT-LIVE | KanbanBoard.tsx, RecordList.tsx |
| Value and currency, split per currency | BUILT-LIVE | currency.py; 737, 887, 888 |
| Products, line items | Zoho Products/Quotes only as mirrored modules. Native MISSING; SPEC slice only (CRM-S9, D22). No task | crm_app 1.x |
| Quotes, proposals | same. Native MISSING, no task | |
| E-sign | MISSING, not in any spec | |
| Won/lost reasons | BUILT-LIVE | admin.py /lost-reasons |
| Forecast | BUILT-LIVE by stage (reports.py /reports/pipeline, WS-26g). By month/owner/target SPEC (I2, task 2593) | |
| Rotting / stalled alerts | PARTIAL: card warning BUILT. Alerts SPEC (S11, I1, M7 task 1435) | |
| Quotas / targets | SPEC CRM-I2 (needs crm_targets) | 2593 |
| Deal risk | SPEC | 2437 |

### ACTIVITIES
| Item | Status | Evidence |
|---|---|---|
| Tasks / follow-ups in the one task store | SPEC (S1). CRM keeps its own activities now | 663; 1961 |
| Calls, meetings, notes (manual log) | BUILT-LIVE | activities.py POST /{entity}/{id}/activities |
| Email logging | BUILT-LIVE (thread join, migration 154) | test_crm_email_timeline.py |
| Calendar sync | MISSING on the record. Pre-call brief SPEC | 2436 |
| Reminders | SPEC (S11, through the shell needs provider) | |
| Call recording | MISSING; explicit non-goal | crm_app 1 |
| WhatsApp thread on the record | MISSING. WhatsApp is a chat channel for CRM questions only. Voice note update SPEC | 2057 |
| Next activity on card | BUILT-LIVE (CRM-X3) | nextActivity.ts |

### COMMUNICATION
| Item | Status | Evidence |
|---|---|---|
| Email sync and send | OTHER-APP (email app). CRM reads threads. Send-from-record not verified | WS-26d-email |
| Templates, merge fields | MISSING | 20.11 |
| Open/click tracking | MISSING | |
| Sequences / cadences | MISSING; v1 non-goal; waits UX-Q4 | crm_platform 19.3 |
| Bulk email | MISSING | |
| SMS | MISSING | |
| WhatsApp send | OTHER-APP (epic 2650) | |
| Forms and lead capture | SPEC (1438, HIGH risk, Native only). Auto-lead from unknown mail BUILT-DARK (CRM_AUTO_LEAD off) | auto_lead.py; 163 |
| Chat widget | MISSING, no task | |
| Meeting scheduler link | OTHER-APP (epic 2675 Booking, Calendar App) | |
| Campaigns | SPEC | 1436 (M8), 1098 (M4) |
| Consent / opt-out | SPEC | 1439 |

### AUTOMATION
| Item | Status | Evidence |
|---|---|---|
| Workflow rules and triggers | MISSING (20.11 "not in spec at all"); CRM-M14 deleted | |
| Business rules | BUILT, but they steer the AI answers (rules.py, 283), not save-blocking validation | rules.py |
| Assignment, round robin | MISSING | 20.11 |
| Lead scoring | MISSING (CRM-X14 scores deals only, SPEC) | |
| SLAs | MISSING; non-goal | |
| Webhooks | MISSING for CRM events (Zoho broker handlers only) | broker_handlers.py |
| Approval flows | MISSING | |
| Stage entry requirements | BUILT-LIVE | WS-26h, h2 |

### AI
| Item | Status | Evidence |
|---|---|---|
| Ask your CRM (read tools, aggregate, dwell, note search) | BUILT. Answer-first, answer-check, catalogue, questions DARK behind *_ORGS | crm_tools.py (17 read/propose tools); Q25..Q33 |
| Chat writes | PARTIAL: M2b done (2033); M2c..M2k open | 2048..2057; WS-26d-write built 2026-08 |
| Weekly digest | BUILT-DARK (CRM_DIGEST_ORGS, 281). In-app page open | digests.py; 1060 |
| Deal brief | SPEC (I3) | 2593 |
| Pre-call brief, customer card | SPEC | 2436, 2435 |
| Next best action | MISSING | |
| Call/meeting notes to CRM | MISSING | |
| Duplicate detection | MISSING; SPEC (I6) | |
| Deal risk | SPEC | 2437 |
| AI field mapping from Zoho | BUILT-DARK (CRM_DECIDE_ORGS empty) | Q20 |
| AI metering | Spec warns each insight needs the meter (H-171) | crm_platform 9 |

### REPORTING
| Item | Status | Evidence |
|---|---|---|
| Pipeline, funnel, win/loss, owner leaderboard | BUILT-LIVE | reports.py (4 routes) |
| Stage dwell / velocity | BUILT | aggregate.py; crm_status_changes |
| Typed aggregate over any field | BUILT | aggregate.py |
| Custom dashboards, report builder | MISSING | Reports.tsx is fixed |
| Source attribution, campaign ROI | SPEC | 1436 |
| Forecast by month, targets | SPEC | 2593 |
| Per-pipeline reports | SPEC | 2046 |
| Export | BUILT-LIVE (CSV lists) | export.py |

### ADMIN
| Item | Status | Evidence |
|---|---|---|
| CRM access: admins / everyone / groups | BUILT-LIVE | access.py |
| Record visibility Mine / Team / All | SPEC; Zoho per-owner CRM-Q12 | 1434 |
| Field-level security | MISSING | |
| Teams / territories | MISSING; non-goal in crm_app 1 | |
| Audit log | PARTIAL (settings log + Undo built; record audit SPEC) | change_log.py |
| Import from Zoho | BUILT-LIVE (backfill, two-way sync every 10 min) | import_zoho.py, crm_sync/ |
| Import from HubSpot / Salesforce / CSV | MISSING. Connector seam is Zoho only | crm_sources/registry.py |
| Cutover Mirror to Native | BUILT-DARK (CRM_CUTOVER_ORGS); run is task 806, due 2026-12-15 | 804 done |
| Public API / webhooks | MISSING as a CRM feature (WS-55 external MCP exists elsewhere) | |
| Sandbox | MISSING | |
| GDPR / DPDP | SPEC consent 1439; purge bug 1570; subject export/erase MISSING | |

### PLATFORM FIT: do not rebuild in the CRM
- Email sync, compose: email app. Calendar, meetings, public booking, payment: epic 2675 WS-BK.
- WhatsApp number, bots, templates: epic 2650 WS-WB. Knowledge and battle cards: epic 2713 WS-KS.
- Tasks and follow-ups: the one task store (D52-54); CRM-S1 (663) is the link.
- Public door, consent: AI Studio STU-O1 (1506), STU-O16 (2420), STU-O5 (1510: crm_contacts is the store for outside people), shared with CRM-M10/M11.
- Automation authoring: Studio and Projects seams. CRM needs the triggers, not a second engine.
- Chat, voice, image: the one assistant (D99).

### INDIA FIT
| Item | Status |
|---|---|
| INR default, per-currency totals | BUILT (currency.py; NULL reads as INR) |
| Lakh/crore display | useCompactMoney.ts exists; lakh/crore NOT verified |
| GST invoice, GSTIN, HSN, state tax | MISSING; billing is out of scope (crm_app 1) |
| UPI / Razorpay payment link | MISSING. Booking epic 2675 mentions payment, possible reuse |
| WhatsApp-first sales | PARTIAL: chat channel only. No thread on the record, no send from the record |
| Regional languages | MISSING in CRM; shell not verified |
| Zoho to Metorite switch | BUILT path (Z10) |

## 2. Deleted 2026-11-10: which the checklist needs back
| Deleted | Verdict |
|---|---|
| SIG-4 renewal and churn watch | YES, reframed. AMC and service-contract renewals are core for Fracktal. Needs renewal date and health on the company. Tasks 1757, 1760 still open as demand signals |
| SIG-5 competitor mentions to battle cards | No. Knowledge epic 2713 owns it |
| SIG-6 customer requests linked to a Goal | No (Projects concern) |
| SIG-7 repeated support question | No (tickets, knowledge) |
| M9 Apollo into a campaign | YES, reshaped as enrichment and prospect import. Waits on M8 |
| M12 thank-you mail after a form | YES, small. Fold into M10 acceptance, not a separate task |
| M13 CRM notices and proposes structure | No. SH-4 (949) covers setup checks |
| M14 automations and modules from chat | YES for the automation half (CRM has no workflow rules). Modules-from-chat half stays out (field studio covers it) |
| Digest email setting | Maybe. Digest exists (dark). Re-add only as a toggle on page 1060 |

## 3. Open CRM tasks
Cover a gap:
- 739 E-40 CSV import, bulk, dedupe. 663 E-39 follow-ups into task store.
- 2593 E-42 insights I1-I6 (stub, no description). 2594 E-43 Zoho realtime and MCP write (stub).
- 1097 family: 2041 (in progress), 2042, 2043, 2046. 1098 family: 2045.
- 1431 M1 change history. 1432 M2 chat parity (2048, 2049, 2050, 2051, 2052, 2054, 2055, 2056, 2057).
- 1433 phases. 1434 scopes. 1435 stuck deals. 1436 campaigns. 1438 forms. 1439 consent.
- 2435, 2436, 2437 customer card, pre-call brief, deal risk. 1060 digest page. 949 setup checks.
- Z10 family: 806 cutover run, 803 attachments, 1827 switch dialog bug.
Off the path (maintenance, defect, internal, owner chore): 2597, 2313, 1895, 1570, 1444, 1121, 738, 736, 735, 734, 2595, 668, 1318, and use-case rows 1757, 1760, 1780 (under parent d1608261, keep as demand signals).

## 4. Biggest gaps, ranked
1. Native CSV import, dedupe/merge, bulk edit not built (739). Blocks any non-Zoho customer.
2. No workflow automation, assignment/round robin, lead scoring, CRM webhooks.
3. No Native products, line items, quotes, GST invoice, e-sign, payment link.
4. Lead capture: forms (1438) and consent (1439) unbuilt; auto-lead dark.
5. Email from the record: templates, tracking, sequences, bulk. No task.
6. WhatsApp on the record (thread, send, template). No CRM task.
7. Tasks, calls, reminders not in the shared task store/Calendar (663, S11).
8. Targets, quotas, forecast by month and owner (2593); no custom dashboards.
9. Record security: Mine/Team/All (1434), field-level, territories, recycle bin.
10. Tags, lists, segments, enrichment.
11. Renewals and AMC with health (deleted SIG-4).
12. Admin: public API/webhooks, HubSpot/Salesforce import, DPDP subject export/erase.

## 5. Contradictions
- crm_app.md non-goals say no custom-field editor, no saved views table, INR only. All built since (studio, 250, currency.py).
- crm_app 1 says quoting/billing out; 1.x (D22) says products and proposals are CRM scope; crm_platform S9 says "spec slice only". No owner, no task.
- crm_platform 20.11 ranks import/bulk/dedupe 6th-7th, task 739 says "plan demoted it", but Z10 (806) asks customers to switch to Metorite, and S3 says import is "the first thing a new customer does".
- 739 done-when says "On staging", but staging retired (D144).
- 2593 and 2594 are stubs with no acceptance criteria.
- M2k (2057) WhatsApp voice-note writes overlap WhatsApp epic 2650.
- Many "built" items are dark behind empty *_ORGS flags. Spec says built, product shows nothing. Flag values per org not checked.
- crm_tools.py on main lists read and propose tools only, while crm_app says write tools were BUILT (WS-26d-write). Not resolved.

## 6. Not read in full
crm_conversational.md (not opened). crm_platform.md and crm_app.md: headers, scope, section 6, section 9, 20.11 and the status paragraphs only (about 4 percent of about 15k lines). No route bodies (route decorators only; docstrings of access.py and auto_lead.py). Not verified: field-type vocabulary, UI behaviour, production flag values, lakh/crore, native attachments, CRM chat write tools. Task descriptions read: 1438, 1439, 739, 2593. Titles only for the rest, and for epics 2675, 2650, 2713.
