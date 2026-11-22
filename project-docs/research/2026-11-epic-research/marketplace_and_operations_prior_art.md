# Prior art: app marketplace and operations modules (2026-11-11)

Evidence tags: [V] verified by fetch or search this session (URL given). [C] claimed by a search snippet, page not opened. [M] from memory, unverified.
Most vendor pages did not load (Salesforce 403, Odoo and Frappe pages truncated or thin). Treat every [M] as a hypothesis to check before a spec cites it.

## PART A. Marketplaces and customization

### A1. Facts per platform
- Odoo. Module = manifest with `depends`, python models, XML views. [M]
  Customize with `_inherit` (extend a model in place), `_inherits` (delegation), and XML view inheritance with xpath (a patch on the view, not a copy). [M] The ORM page lists an "Inheritance and extension" section but the body was not read (https://www.odoo.com/documentation/18.0/developer/reference/backend/orm.html, [V] only that the section exists).
  Upgrade: modules ship migration scripts (pre/post/end-migrate); upgrading a customised DB is a separate job and often breaks across major versions. [M]
  Store: 30% commission to Odoo S.A.; the publisher supports the buyer; store price must be equal or lower than other channels; licences LGPL-3 (open) or OPL-1 (proprietary) (https://apps.odoo.com/apps/vendor-guidelines , https://apps.odoo.com/apps/faq ; [C] via search snippets, 2026). OPL-1 forbids redistributing copies (https://www.odoo.com/documentation/master/legal/licenses.html, [C]). Review: manual check on upload. [M]
- Frappe/ERPNext. App = python package in `apps/`, with `hooks.py`, `patches.txt` (patches run once, in order, at migrate), `modules.txt`; installed per site with `bench install-app` (https://docs.frappe.io/framework/user/en/basics/apps, [V]; that page does not cover fixtures or Custom Fields).
  Customize Form writes Property Setter and Custom Field records into the site DB, not into app code, so an app upgrade does not overwrite them. Fixtures export those records into an app for shipping. `doc_events` and `override_doctype_class` in hooks extend behaviour. [M]
  ERPNext licence GPL-3. [M] Frappe Marketplace terms: not found.
- Salesforce. Unmanaged package = a copy, no link after install. Managed package = namespace, IP protection, upgradeable; the subscriber edits only components the publisher left editable; subscriber changes on editable components survive an upgrade. [M]
  Security review: a new version of an approved managed package skips the full review; functionality changes can need an initial review; a code scan runs on each upload; older doc versions list USD 2,550 review fee (https://developer.salesforce.com/docs/atlas.en-us.244.0.packagingGuide.meta/packagingGuide/security_review_periodic_reviews.htm and sibling pages, [C], fee from an OLD doc version, confirm). The official packaging page returned 403.
- Microsoft Power Platform. Layers per component: an unmanaged layer (ad-hoc changes, one shared layer) sits on top of managed layers (last installed on top). Most components are "top wins". Only model-driven app, form and sitemap components are merged. A staged upgrade makes a pending top layer, and "Apply upgrade" flattens it. Uninstalling the top managed layer exposes the one below. (https://learn.microsoft.com/en-us/power-platform/alm/solution-layers-alm , updated 2025-05, [V])
  Inference [V-based]: an unmanaged customization on a "top wins" component hides the publisher's later change.
- Shopify. Store apps MUST use a Shopify billing solution. Shopify collects and pays out, handles chargebacks, prices are set in USD and converted. The Billing API is needed for one-time charges and unsupported models. (https://shopify.dev/docs/apps/launch/billing , [V], 2026-11-11). Revenue-share percentage is not on that page. [M] 0% on the first USD 1M a year, then 15%.
- HubSpot, Zoho Marketplace, Zoho Creator, monday, Airtable, Notion: NOT researched. [M] only: Airtable and Notion templates are full copies with no update path. HubSpot, monday and Zoho listings need a review, an OAuth scope list, a privacy policy and a support contact. Verify before use.

### A2. The three models
| Model | Examples | Update with customer changes | Weakness |
|---|---|---|---|
| Template copy | Airtable, Notion, Salesforce unmanaged | None. The copy diverges at once. | No security fixes, no paid upgrades |
| Managed package (locked) | Salesforce managed, Shopify hosted apps | Publisher owns the base. Customer edits only the allowed points. | Publisher must guess extension points. Customer feels trapped. |
| Extension layer (base + overlay) | Frappe custom fields, Odoo inherited views, Power Platform layers | New base replaces old. The overlay is re-applied. | Conflicts and orphaned overlays need a policy |

### A3. Recommended model for Metorite: base version + customer overlay + three-way upgrade
Reason: Metorite apps are data (objects, typed fields, rules, workflows, logic modules, views), not code. Data can be diffed, so a real three-way merge is possible. Odoo and Frappe cannot do that for code. (design inference)
1. Package = immutable, versioned manifest: app id, semver, component list. Each component has a stable component key, a content hash and a customizable flag.
2. Install = write the base into the tenant as rows tagged (app, version, component_key, base_hash). Tenant-scoped (R5).
3. Customization = overlay rows keyed by component_key. Field-level ops: add field, change label/help/default, hide, reorder; add view column or filter; add rule or workflow step; disable a publisher rule. Never mutate the base row. An overlay on a locked component is refused.
4. Upgrade = three-way merge per component using BASE_old (stored), BASE_new (package) and OVERLAY.
   - Only the publisher changed a property: apply.
   - Only the customer changed it: keep.
   - Both changed different properties: merge.
   - Both changed the same property, or the publisher removed or retyped something the overlay uses: CONFLICT. Hold that component on the old version, show the conflict list, and let the customer pick keep mine, take new, or edit.
   - Publisher deprecates a field one version before removal (mirrors R6 expand/contract).
5. Upgrade preview = dry run on the tenant: added, changed, removed components, conflicts, data impact (a new required field needs a default). Data cannot be rolled back, so snapshot the overlay and base-version pointer before apply and restore from that.
6. Detach (fork): a component becomes the customer's and gets no more updates. This is the escape valve that Power Platform lacks.
7. Version channels (stable, beta). A tenant pins a version. Auto-update only for patch versions with no schema change.

### A4. Marketplace mechanics (design recommendations)
- Listing: name, publisher, category, screenshots, what it creates (counts of objects, fields, rules, workflows), permissions, version history, changelog, support contact, data-handling statement, licence, price.
- Search: text, category, "works with", industry preset (India manufacturing, service). Ranking: installs, rating, update recency, verified publisher.
- Ratings: only from tenants with the app installed 14+ days; one per tenant; publisher may reply.
- Publisher verification: legal entity, domain, tax ID, contact; verified badge. First-party apps show "by Metorite".
- Review and scan: automatic manifest scan (no secrets, no outbound URL outside an allow-list, bounded rule and formula complexity, permission check) plus human review for first publish and for any version that adds permissions, logic modules or outbound calls. Salesforce skips the full review for ordinary new versions [C]; copy that.
- Permissions at install: objects created; existing tenant objects read or written; outbound endpoints; roles and features added; AI credit use. Admin ticks them. Grant can be narrowed per role (D12).
- Sample data: optional, flagged is_sample, one-click purge. Install preview plus a try-in-demo-slice mode.
- Uninstall: keep data as plain objects, or delete; always offer CSV/JSON export first; 30-day restore snapshot.
- Paid apps: Metorite is merchant of record, like Shopify [V]. Models: free, per seat, flat, usage. Revenue share 15-30% (Odoo 30% [C]). Money flows are owner-only (CLAUDE.md 3a rule 3). Ask legal about GST on marketplace fees and TCS for e-commerce operators. [M]
- Private catalog: listings visible to one org or a named list, skip public review but not the scan. Needed for implementation partners.
- Bundles (presets): a list of app ids and versions, role setup and sample-data choice, e.g. "Small manufacturer India". Installed in dependency order, one permission screen.
- Dependencies: manifest `requires: app@range`, no cycles. Shared objects (Item, Party, UOM, Location) live in a Foundation app so apps do not each own an Item. Odoo `depends` works like this. [M]

## PART B. Operations modules

All of Part B is [M] (ERPNext, Odoo, Katana, MRPeasy, Fishbowl, Tally, Zoho Inventory, Vyapar, Marg, UpKeep, Fiix, Limble). No vendor page was fetched. Treat as a design proposal. Exceptions are marked.

### B0. Shared foundation (build first)
Objects: Item (code, name, type raw/semi/finished/service/consumable, base UOM, tracking none/batch/serial, HSN/SAC, GST rate, valuation method, reorder point, lead time); UOM and per-item conversion; Party (customer/vendor, GSTIN, state, terms); Location (warehouse > bin; type own, in-transit, job-worker, quarantine, scrap); Stock Ledger Entry (item, location, batch/serial, qty +/-, value, voucher type and id, posting time).
Rule: stock balance is derived from the ledger. No editable on-hand field. (ERPNext and Odoo do this. [M])
India: GST needs a UQC code per UOM (NOS, KGS, MTR...). Batch has mfg date, expiry, supplier lot. Serial is unique per item.

### B1. Inventory and stock
- Model: Item, Location, Stock Ledger Entry, Stock Entry (receipt, issue, transfer, adjustment), Batch, Serial, Stock Count, Reorder rule.
- 5 screens: stock on hand by item and location with batch drill; movement entry; transfer with in-transit state; physical count and adjustment; reorder and shortage list.
- Calc: on_hand = sum(ledger qty); available = on_hand - reserved; reorder when available + on_order <= reorder_point; reorder_point = daily_usage x lead_time_days + safety_stock; moving average rate = (old_value + in_value) / (old_qty + in_qty). FIFO as option.
- SMB uses: receipts, issues, transfers, counts, low-stock alert. Bloat: putaway strategies, wave picking, landed-cost allocation in v1.
- India: transfer between two GSTINs (even same PAN) is a taxable supply, needs invoice and e-way bill above INR 50,000 [C: https://calcguru.in/?p=3507]. Same GSTIN, different place: internal transfer, no GST. Make negative stock blockable.

### B2. Procurement
- Model: Purchase Request, RFQ (optional), Purchase Order, Goods Receipt, Vendor Bill, vendor price list, return to vendor.
- 5 screens: shortage to PO; PO list with pending qty; GRN entry with batch; 3-way match exceptions; vendor performance.
- Calc: pending = ordered - received; match tolerance on qty and rate; vendor on-time % = grn_date <= promised_date.
- SMB uses: PO, GRN, bill match. Bloat: RFQ comparison, blanket orders, approval matrices (one threshold only in v1).
- India: ITC depends on the vendor invoice appearing in GSTR-2B; reverse-charge flag; TDS on vendor payments; return from a job worker references the challan.

### B3. BOM and routing
- Model: BOM (parent, version, basis qty, status), BOM Line (component, qty, UOM, scrap %, substitute), Operation, Work Centre (capacity, cost per hour, calendar), Routing (ordered operations, setup and run time, subcontract flag).
- 5 screens: BOM tree editor; where-used; routing editor; cost roll-up; version compare and activate.
- Calc: cost roll-up = sum(qty x (1+scrap) x unit cost) + sum(op time x work-centre rate) + subcontract charge, recursive with a cycle check. Explosion: child gross = parent qty x line qty x (1+scrap).
- SMB uses: one or two levels, simple routing. Bloat: phantom BOMs, variants, by-products, engineering change orders in v1.

### B4. Production planning and work orders
- Model: demand (SO lines, forecast), Production Plan, Work Order (item, BOM snapshot, qty, dates, status), WO Operation, Job Card (operator, work centre, start/stop, good and reject qty), Material Issue (manual or backflush), Production Receipt.
- 5 screens: planning board by work centre; create WO from demand; WO detail (materials issued vs required, operation progress); operator job card on phone; production summary (planned vs actual, WIP).
- Calc: load = sum(op hours) / available hours per work centre per day; forward scheduling on calendars; WIP = issued material + labour + overhead - receipts. Snapshot the BOM on the WO.
- SMB uses: WO, job card, issue, completion, simple finite scheduling. Bloat: APS optimisers, skill matrices, multi-plant.

### B5. MRP
- Model: demand, supply (on hand, open PO, open WO), Planned Order, MRP Run log.
- 5 screens: run (scope, horizon); exception list; planned orders to PO/WO in bulk; time-phased projection; pegging to the driving SO.
- Calc, per item per bucket, in low-level-code order: net = gross - (on_hand + scheduled_receipts - safety_stock). If net > 0: planned_qty = round_up(net to MOQ or lot); release = need_date - lead_time; explode planned qty through BOM to child gross.
- SMB uses: short horizon, weekly buckets, "what to buy and make". Bloat: multi-level pegging, action-message families, planning fences.

### B6. Shop-floor capture
- Model: Label (QR/Code128 carrying a signed short id, not raw data), Scan Event (who, device, code, action, time), Job Card.
- 5 screens: scan to receive; scan to issue to WO; job-card start/stop with qty and reject reason; label print (item, batch, location, WO traveller); phone scan with offline queue.
- Calc: cycle time = stop - start - paused; yield = good / (good + reject).
- SMB: phone camera, no hardware. Bloat: RFID, PLC/IoT, MES recipe control.

### B7. Quality (NCR, CAPA)
- Model: Inspection Plan (characteristics, limits, sample size), Inspection (incoming, in-process, final), NCR (source, item/batch, defect, qty, disposition use-as-is/rework/scrap/return), CAPA (root cause, corrective, preventive, owner, due, effectiveness check), Quarantine location.
- 5 screens: inspection entry at GRN, job card and dispatch; NCR register; NCR detail with disposition; CAPA tracker with overdue; quality dashboard.
- Calc: reject % = rejected / inspected; supplier PPM = rejected / received x 1e6; first-pass yield = product of step yields.
- Hook: a failed inspection moves stock to quarantine through the ledger and blocks issue and dispatch of the batch.
- SMB uses: pass/fail with reasons, NCR log, simple CAPA. Bloat: SPC, FMEA, PPAP/APQP, calibration programme in v1.

### B8. Asset and maintenance (CMMS)
- Model: Asset (tag, location, parent, make, serial, cost, criticality, meter), Meter Reading, PM Schedule (time or meter), Maintenance Work Order (type, asset, priority, assignee, parts, labour, downtime start/end), Spare Part (an Item), Downtime log.
- 5 screens: asset tree with history; PM calendar; mobile maintenance WO; breakdown request via machine QR; spares and downtime report.
- Calcs: next_due = last_done + interval (or meter); Availability = (planned - downtime) / planned; Performance = (ideal cycle x count) / run time; Quality = good / total; OEE = A x P x Q; MTBF = operating time / failures; MTTR = repair time / failures; PM compliance % = on-time PM / PM due.
- UpKeep, Fiix and Limble centre on request, work order, PM, asset history. SMB uses: PM schedule, breakdown WO, asset QR. Bloat: predictive analytics, multi-store MRO, condition-monitoring IoT.

### B9. Costing
- Model: Cost Element (material, labour, overhead), overhead rate, Standard Cost (per item per version), Actual Cost on WO, Variance.
- 5 screens: product cost sheet; WO actual vs standard; margin by product, customer and order; inventory valuation; variance report.
- Calc: standard = BOM roll-up. Actual WO cost = material at ledger rate + labour (job-card time x rate) + overhead (machine hours x rate) + subcontract; unit cost = WO cost / good qty. Price variance = (actual price - std price) x actual qty; usage variance = (actual qty - std qty) x std price; margin = net invoice - unit cost.
- SMB: standard plus actual WO cost plus margin per order. Bloat: activity-based costing, transfer pricing.

### B10. Dispatch
- Model: Dispatch/Delivery Note (SO link, batch/serial lines, packages), Packing List, Transporter, LR number, e-way bill record, proof of delivery, Return.
- 5 screens: dispatch queue (SO vs stock); pick and pack by scan; challan and invoice print; e-way bill generate and track; in-transit and POD board.
- Calc: ready_to_ship = min(available, ordered - shipped); package weight and volume totals.
- India: e-invoice IRN is mandatory above INR 5 crore aggregate turnover; unchanged as of 2026; a cut to INR 2 crore was proposed, not notified as of June 2026 [C: https://tallysolutions.com/gst/e-invoicing-threshold/ , https://taxguru.in/?p=1089104]. E-way bill above INR 50,000 consignment value, Rule 138 [C]. IRN reporting windows: sources conflict (7 vs 30 days by turnover band). VERIFY on gst.gov.in. A delivery challan covers goods moved without sale (job work, samples). [M]

### B11. India job work
- Principal sends inputs to a job worker under a delivery challan (Section 143 CGST Act). No GST on the movement if inputs return within 1 year (capital goods 3 years); otherwise deemed supply. Principal reports in ITC-04; periodicity depends on turnover (quarterly above INR 5 crore, half-yearly below). [M, NOT verified: the search returned nothing on ITC-04.]
- Model: Job Work Challan (principal and job-worker GSTIN, items, qty, value, date, purpose), challan line balances (sent, returned finished, returned scrap, lost), Return Challan.
- Screens: challan from a WO subcontract operation; ageing against the 1y/3y limit; receive back against challan; ITC-04 data export; job-worker stock statement.
- Calc: pending = sent - returned - scrap - written off; ageing = today - challan date; alert at 300 days.
- Tally has job work vouchers; ERPNext has Subcontracting Order. [M]

## PART C. Accounting

### C1. Scope options
- Statutory accounting (build): double-entry ledger, chart of accounts, journals, trial balance, P&L, balance sheet, bank reconciliation, GSTR-1/2B/3B and filing, e-invoice IRN via the IRP, e-way bill via NIC, TDS/TCS returns, depreciation, audit trail, year-end close, multi-state GST, auditor access.
  Burden: GST rules change by notification (rates, thresholds, tables, reporting windows). IRN and e-way bill need GSP/API registration. Edit-log audit trail is mandatory for company books since 1 Apr 2023 [M]. Effort: person-years to reach Tally or Zoho Books parity. Wrong filings fall on the vendor in the customer's eyes.
- Operational accounting (build, thin): receivables (invoice, receipt, ageing, reminders), payables (bill, payment, ageing, TDS flag), cost and margin views (B9), stock valuation from the ledger, party statements, cash forecast from due dates, expense capture. No filing, no balance sheet.
- Connect: Tally Prime (largest Indian base; XML over HTTP and TDL, desktop-bound, no cloud API [M]); Zoho Books (REST API, GST filing, e-invoice [M]); QuickBooks (weak India fit [M]); Odoo Accounting (India localisation [M]).

### C2. Comparison
| | Build statutory | Connect to one tool | Operational only |
|---|---|---|---|
| Effort | Very high | Medium per connector | Low to medium |
| Compliance liability | Ours | Theirs | None |
| India fit | Customers already have a CA on Tally or Zoho | Matches how Indian SMBs work | Fills the gap between ops and the CA |
| Risk | Head-on rivalry with Tally and Zoho | Connector breakage | Low |

### C3. Recommendation
Do not build statutory accounting. Build thin operational accounting (AR, AP, cost and margin, stock valuation, GST-correct invoices and challans). Ship a voucher export shaped as double entry (sales, purchase, receipt, payment, journal for stock, WIP, COGS) to Tally Prime XML first and Zoho Books second. Reason: Indian SMB CAs file from Tally or Zoho, so re-keying is the real pain, and an export removes it. The statutory surface changes by notification and carries liability. Revisit only when customers ask to retire Tally. For IRN and e-way bill use a licensed GSP API, behind a flag default off, owner-gated (live government credentials).

## The 5 traps
1. Three-way merge works only with stable component keys and field-level overlays. A copy-and-edit install makes it impossible. Decide at install design.
2. "Top wins" layering (Power Platform [V]) silently hides publisher fixes. Require a conflict list and a detach option.
3. Hand-editable balances (stock on hand, AR balance) destroy trust. Derive from ledgers.
4. A BOM edit that changes open work orders. Snapshot the BOM version on the WO.
5. Compliance creep: statutory accounting, e-invoice and ITC-04 look like one more screen but carry notification churn and liability. Marketplace money (revenue share, GST on fees) is also owner-gated.

## Could not verify
Salesforce docs (403); Odoo inheritance and upgrade pages (truncated); Frappe fixtures and Customize Form; HubSpot, Zoho, Zoho Creator, monday, Airtable, Notion listing rules; Frappe Marketplace terms; Shopify revenue-share percent; all Part B vendor feature claims; ITC-04 periodicity and job-work time limits; IRN reporting windows (sources conflict); Tally integration specifics; Companies Act audit-trail date.
