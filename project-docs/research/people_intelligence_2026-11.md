# People intelligence: what other products do, and the law (research record)

> **Research record for `project-docs/specs/people_intelligence.md`** (task 3867, 2026-11-22). This file is evidence, not a contract. The spec owns every rule.
>
> **Method.** A research agent ran web searches on 2026-11-22. Most claims rest on secondary sources (law firms, vendor pages, news). **UNVERIFIED** marks a claim that no first-hand source confirmed. *(background)* marks prior knowledge that nobody checked again in this session.
>
> **Spot checks by the spec author, 2026-11-22.** Two dates carry the design, so a second search checked them.
> - EU AI Act: Regulation (EU) 2026/1744 (the Digital Omnibus on AI) moves stand-alone Annex III systems to **2 Dec 2027**. White & Case, Orrick and Cuatrecasas agree. Nobody read the EUR-Lex text.
> - India DPDP Act s.7(i): employment is a legitimate use with no consent. Section 7 starts about **13 to 14 May 2027**, 18 months after G.S.R. 843(E) of 13 Nov 2025. One source (Taxmann) lists an earlier date and contradicts itself. Whether "purposes of employment" covers performance monitoring is contested.
>
> **A customer may still need legal advice.** `project_alignment.md` line 381 records that nobody researched monitoring law before. This file is the first pass, not counsel.

---

Method: web search only. Search tool returned summaries, so most claims rest on secondary sources. Items marked **UNVERIFIED** were not confirmed from a first-hand source. Items marked *(background)* come from prior knowledge and were not re-checked in this session.

## 1. Summary: lessons that should shape the design

1. **Evidence, not scores.** The product that gained traction in 2026 (Lattice Evidence-based Review Drafts, Windmill) drafts text from linked evidence and never picks the rating. Copy that: the AI cites evidence, a human decides.
2. **Aggregate-by-default is the industry norm for passive data.** Viva Insights enforces a minimum group size of 5 (admin can raise it). Worklytics pseudonymizes at a proxy. Individual-level views of email/chat/calendar metadata are the line vendors do not cross.
3. **Person-level scoring from activity data is where backlash lives.** McKinsey's 2023 developer-productivity paper drew sustained criticism. DX/Swarmia position team-level. Monitoring tools (keystrokes, screenshots) are the anti-pattern.
4. **Evidence must be work artifacts the employee can see and contest** (tasks closed, PRs merged, deals moved), not behavioural telemetry (typing, idle time, sentiment).
5. **The EU AI Act classes this product as high-risk (Annex III, employment).** Date moved to 2 Dec 2027 by Regulation (EU) 2026/1744 (secondary sources). Emotion inference at work is banned now (Art 5(1)(f)).
6. **Indian first customers: DPDP Act s.7(i) lets an employer process employee data without consent for "purposes of employment", but only with a close nexus.** Core Rules apply from about 13 May 2027. Personal GitHub is outside that nexus and needs real, withdrawable consent.
7. **Personal accounts are a legal minefield.** 27 US states bar demanding access to personal social accounts. Treat personal GitHub as employee-initiated, read-only, public-data-only, revocable, with zero effect on a decision if declined.
8. **Skills inference is the most litigated area.** Eightfold was sued in Jan 2026 (FCRA theory) for hidden scoring. Show every inferred skill, its source, and let the person correct it.
9. **Agent exposure (MCP) multiplies risk.** Lattice shipped an MCP in July 2026. External agents that help with firing or team moves must get scoped, logged, purpose-bound access, never a raw profile dump.
10. **Build the compliance seams first:** purpose limitation, employee self-view, human-in-the-loop decision record, audit log, bias testing, per-region switches (EU/DE works council, California ADMT, NYC).

## 2. Product categories

### 2.1 Work-graph / people analytics from collaboration metadata

| Product | Data sources | Signals | Outputs | Individual vs aggregate | Privacy design | Price | Criticism |
|---|---|---|---|---|---|---|---|
| Microsoft Viva Insights (ex-Workplace Analytics) | M365 email, calendar, Teams, plus HR attributes | Collaboration hours, meeting load, after-hours work, network size | Personal insights, manager/leader dashboards, analyst queries | Individual sees own insights. Manager/leader views are group-level only. Analyst queries are de-identified | Minimum group size default 5, cannot go lower. Metrics de-identified. User opt-out control for row-level outputs. Principle: no user learns about another identified person beyond what they already could know | Bundled with M365 / Copilot licences. Advanced app UNVERIFIED | Early WPA drew "manager surveillance" fears. 2020 productivity-score backlash led Microsoft to remove per-user names *(background)* |
| Worklytics | Google Workspace, M365, Slack, Zoom, others UNVERIFIED (Jira/GitHub not confirmed) | Collaboration, ONA, meeting load, AI-tool adoption | Dashboards, ONA, benchmarks | Team/aggregate | Pseudonymizing proxy on customer side (open-source psoxy). Marketing says "anonymization" but docs say pseudonymization | Custom enterprise. Reviews cite free tier and Business from about USD 2,500/mo. Treat as UNVERIFIED | Vendor-authored comparisons only |
| Humanyze | Badges, email, calendar *(background)* | Interaction patterns | Team analytics | Aggregate | Aggregation. Sociometric badges drew privacy concern *(background)* | UNVERIFIED | Badge sensing seen as invasive *(background)* |
| Time is Ltd | Calendar and email metadata *(background)* | Meeting time and cost | Dashboards | Team/org | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| Confirm | Surveys of who contributes to whom (ONA) *(background)* | Peer-nominated impact network | Performance calibration inputs | Individual (peer-derived) | UNVERIFIED | UNVERIFIED | **Workday acquisition: UNVERIFIED.** Search found no report of it |
| Visier | HRIS, ATS, survey data *(background)* | Attrition risk, workforce metrics | Dashboards, predictions | Mostly aggregate | UNVERIFIED | Enterprise | UNVERIFIED |

Notes. The Viva rule set is the best precedent: individual self-view, aggregate-only for managers, floor of 5, opt-out for row-level exports. Source: Microsoft Learn privacy pages.

### 2.2 Performance management and OKRs

| Product | How goals link to people and evidence | AI | Individual scoring? | Notes |
|---|---|---|---|---|
| Lattice | Company to team to individual goal cascade with roll-up. Goal progress can be tied to a Jira query (Jira Cloud, hourly sync, subtasks excluded unless JQL expanded). GitHub connector: not found | Lattice Agent. **Evidence-based Review Drafts** (July 2026) build from 1:1s, feedback, goals, updates, growth areas and prior reviews. Agent cannot pick ratings or submit reviews. Lattice MCP shipped July 2026 | Ratings by humans | Included with Performance subscription. Closest to the target design |
| 15Five | Check-ins, OKRs, reviews *(background)* | AI summaries *(background)* | Human | UNVERIFIED details |
| Culture Amp | Engagement plus performance *(background)* | UNVERIFIED | Human | Goal-linking not found |
| Leapsome | Goal tree, reviews, surveys, modular pricing | Review summaries, survey analysis, workflow agents | Human | Per-module price, not public |
| Betterworks | Enterprise OKR plus continuous performance *(background)* | UNVERIFIED | Human | UNVERIFIED |
| Quantive (ex-Gtmhub) | Acquired by WorkBoard on 28 May 2025. quantive.com redirects to workboard.com | WorkboardAI (AI Chief of Staff, Leadership Coach) | Mostly team/company | Consolidated |
| WorkBoard | Enterprise OKR and strategy execution | As above | Mostly team | |
| Perdoo | OKR plus KPI, strategy maps, initiatives under key results. Free tier 5 users. AI assistant and coach | | Team | |

Notes. Only Lattice (Jira goal sync) and Windmill (see 2.5) pull work-tool evidence automatically. Most OKR tools rely on self-reported check-ins. Open question: none was found that links individual goals to Slack/email evidence. That gap is the opportunity and the risk.

### 2.3 Engineering intelligence

| Product | Data | Metrics | Stance on person metrics | Notes |
|---|---|---|---|---|
| LinearB | Git, Jira, CI | DORA, cycle time, PR size/pickup | Blog criticizes rivals' individual scoring as a culture risk. Own stance on individuals: UNVERIFIED | Hosted podcast on McKinsey debate |
| Jellyfish | Git, Jira, calendar *(background)* | Allocation, investment, DORA | Sales material claims person-level metrics as first-class | Aimed at engineering leadership. Reviews note individual-level insight |
| Swarmia | GitHub, Jira, surveys | Flow metrics, working agreements, developer surveys | Team-level emphasis | Pairs system data with surveys |
| DX (getdx) | Surveys plus system data | DX Core 4, SPACE-style *(background)* | Reported (by a competitor) as avoiding individual productivity reporting. Verify | |
| Pluralsight Flow | Git, Jira | Cycle time, throughput | "Not about KPIs, leading indicators" | Sold to Appfire, Feb 2025 |

Backlash: McKinsey's Aug 2023 "Yes, you can measure software developer productivity" was widely criticized (Pragmatic Engineer, Dan North, LeadDev) for contribution analysis and a talent-capability score. Critique was about perverse incentives, not measurement as such. Lesson: DORA/SPACE give team flow, and every credible author warns against using them to rank people.

### 2.4 Talent intelligence and internal mobility

| Product | Skills inference | Matching | Risk |
|---|---|---|---|
| Eightfold AI | Deep-learning on job titles, skills, profiles, job history. Complaint says LLM draws on over 1.5B data points | Candidate/employee to role match, 0-5 "likelihood of success" | **Class action filed 20 Jan 2026 (Contra Costa County, CA)**: scores are "consumer reports" under FCRA/ICRAA. Eightfold disputes it. Pending |
| Workday Skills Cloud | Parses resumes, job descriptions, learning content and represents them in a skill space. Suggests skills the worker may not know they have | Workers to jobs, gigs, learning, mentors through Talent Marketplace / Career Hub | Vendor-reported lifts (63% more gigs, 26% higher internal hire rate) are self-reported |
| Gloat | UNVERIFIED (not found) | Talent marketplace | |
| Beamery | UNVERIFIED | | |
| SAP SuccessFactors Opportunity Marketplace | UNVERIFIED | | |

Lesson: inference from documents and job history is standard. Inference from live work artifacts (PRs, tickets, emails) is newer and has less precedent. Surface source and confidence, allow correction, avoid opaque match scores.

### 2.5 AI-native newcomers (2024-2026)

| Product | What it does | Traction (verify) |
|---|---|---|
| Windmill | Slack agent "Windy" drafts and runs reviews, pulls context from GitHub, Jira, Asana, Salesforce, Figma. USD 10/user/mo, first 10 free | USD 12M pre-seed plus seed (Inspired Capital lead, Axios Pro, Mar/Apr 2026). "100+ customers" and "90% faster" are vendor claims. UNVERIFIED independently |
| Lattice Agent and MCP | See 2.2 | Large installed base |
| Workday Sana Coach / Performance Agent | Gathers data across Workday for reviews. Early access | Workday Rising 2026 |
| Peoplelogic | **UNVERIFIED**: nothing found in search |
| Rippling AI | **UNVERIFIED**: not researched |

### 2.6 Employee monitoring (the anti-pattern)

ActivTrak, Hubstaff, Teramind, Insightful: read app/URL usage, active/idle time, screenshots, and for Teramind keystrokes and behavioural rules *(background)*. Hubstaff markets "no keystroke logging" as a point of difference. Independent criticism was not retrieved by search (results were vendor and G2 pages). The generally reported problems *(background, UNVERIFIED here)*: activity is not output, idle time penalizes thinking, screenshots feel punitive, trust erodes. **Signals a respectful product should avoid:** keystrokes, screenshots, mouse movement, idle time, webcam/voice, sentiment/emotion, off-hours personal-device data. Note: emotion inference at work is banned in the EU.

Scoring individuals: most monitoring and ONA tools aggregate. Viva, Worklytics, Swarmia, DX stay team-level. Lattice, Windmill and Jellyfish touch individuals, but through reviews or reports a person can see.

## 3. Regulation (late 2026)

| Rule | Jurisdiction | What it requires of THIS product | Effective date | Source |
|---|---|---|---|---|
| **EU AI Act, Annex III point 4** (recruitment, promotion, termination, task allocation, monitoring and evaluating performance and behaviour) | EU. Applies to non-EU providers when output is used in the EU | High-risk. Provider: risk management, data governance, logs, transparency, human oversight, accuracy/robustness, conformity. Deployer (customer): use per instructions, assign human oversight, inform workers, keep logs, monitor. Art 4 AI literacy | Originally 2 Aug 2026. Digital Omnibus (Regulation (EU) 2026/1744, OJ 24 Jul 2026, in force 27 Jul 2026) fixes Annex III at **2 Dec 2027**. Annex I products 2 Aug 2028. EUR-Lex text not checked: UNVERIFIED | https://www.regulation-ai.eu/en/omnibus/, https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/, https://regdossier.eu/digital-omnibus-ai-act/ |
| **EU AI Act Art 5(1)(f)** emotion inference at work | EU | Do not infer emotions of employees (sentiment from chat/voice/video). Medical/safety exception only. Commission guidelines (4 Feb 2025, non-binding) say intention inference is also covered | In force since 2 Feb 2025 | https://fpf.org/blog/red-lines-under-eu-ai-act-unpacking-the-prohibition-of-emotion-recognition-in-the-workplace-and-education-institutions/ |
| **GDPR Art 22** and CJEU C-634/21 SCHUFA | EU/EEA | A score that strongly shapes a decision counts as automated decision-making. Needs a real human decision with authority and information, or a lawful exception, plus the right to contest | In force. SCHUFA 7 Dec 2023 | https://iapp.org/news/a/key-takeaways-from-the-cjeus-recent-automated-decision-making-rulings |
| **GDPR Art 35 (DPIA), Art 88** and **BetrVG s.87(1) no.6** | EU, Germany | DPIA before launch (employee monitoring and profiling). Art 88 lets states set stricter employment rules. In Germany a works council must co-determine tools that can monitor behaviour or performance. Statutes known *(background)*, no source fetched: UNVERIFIED | In force | First-hand text not fetched |
| **India DPDP Act 2023, s.7(i)** | India | Employer may process without consent for "purposes of employment" and to safeguard the employer. Needs a close nexus, limited to relevant and necessary data, not general surveillance. Post-employment retention is a grey area. Notice duty under s.5 is tied to consent requests, so whether notice is needed for s.7(i) is contested. Treat as required (good practice). Also s.8 duties (security, accuracy, erasure) and rights of access, correction, erasure, grievance | Act passed 2023. Rules notified 13 Nov 2025. Rules 1, 2, 17-21 immediate. Rule 4 (consent managers) after 12 months (about 13 Nov 2026, status UNVERIFIED). Rules 3, 5-16, 22, 23 after 18 months, **about 13 May 2027**. The s.7(i) text and rule numbers beyond this are UNVERIFIED | https://www.barandbench.com/law-firms/view-point/navigating-legitimate-use-exemption-employee-data-digital-personal-data-protection-act-2023, https://yourstory.com/2025/11/digital-personal-data-protection-dpdp-rules-2025-rollout-over-18-months, https://www.taxmann.com/post/blog/legitimate-uses-section-7-dpdp-act |
| **NYC Local Law 144** (AEDT) | New York City | If the tool substantially helps decisions on hiring or **promotion**, annual independent bias audit, published summary, notice to candidates and employees. Penalties USD 500 first, 500 to 1,500 after (sources differ). State Comptroller audit (2 Dec 2025) found enforcement ineffective, tougher enforcement expected | In force since July 2023 | https://www.osc.ny.gov/state-agencies/audits/2025/12/02/enforcement-local-law-144-automated-employment-decision-tools, https://www.dlapiper.com/en/insights/publications/2026/01/critical-audit-of-nyc-ai-hiring-law-signals-increased-risk-for-employers |
| **Illinois HB 3773** (Human Rights Act amendment) | Illinois | No AI use that discriminates (incl. proxies such as zip code) in recruitment, promotion, discipline, discharge. Employers must give notice of AI use. IDHR notice rules proposed 15 May 2026, then reported withdrawn 8 Jun 2026: final rule status UNVERIFIED | Statute effective 1 Jan 2026 | https://www.insideprivacy.com/state-privacy/admt-law-round-up-what-employers-need-to-know-about-recent-admt-laws/, https://www.seyfarth.com/news-insights/illinois-department-of-human-rights-temporarily-withdraws-proposed-rules-on-use-of-artificial-intelligence-in-employment.html |
| **Colorado** SB 24-205 (original AI Act) | Colorado | **Never took effect.** Repealed and replaced by SB 26-189 (signed 14 May 2026). New law is a narrower ADMT disclosure regime: if AI materially contributed to an adverse consequential decision, the deployer must within 30 days disclose the ADMT's role and the reconsideration route. No private right of action. AG enforces. Duty of care, impact assessments and risk programmes dropped. A court stay (27 Apr 2026, xAI litigation) covers replacement laws: current enforceability UNVERIFIED | **1 Jan 2027** (cure period to 1 Jan 2030) | https://www.finnegan.com/en/insights/articles/colorado-replaces-landmark-ai-act-an-overview-of-the-new-sb-26-189-framework.html, https://www.eckertseamans.com/legal-updates/colorado-repeals-and-replaces-its-landmark-ai-statute-what-businesses-need-to-know |
| **California CCPA ADMT regulations** (CPPA) | California (employees count as consumers) | For ADMT used in a "significant decision" (including hiring, allocation of work, compensation, promotion, termination): pre-use notice, access right, opt-out or a human-appeal route (limited exceptions), risk assessment. Separate Civil Rights Council ADS regulations on employment | ADMT compliance **1 Jan 2027** for existing use. Risk assessments due 31 Dec 2027, report to CPPA 1 Apr 2028. Civil Rights Council ADS rules in force 1 Oct 2025 | https://www.akingump.com/en/insights/alerts/new-california-regulations-regarding-employer-use-of-automated-decision-making-technology-compliance-required-by-january-1-2027, https://www.skadden.com/insights/publications/2025/10/california-finalizes-cppa-regulations |
| **NY Civil Rights Law s.52-c(2)** electronic monitoring notice | New York | Written notice at hire with signed or electronic acknowledgement, plus a conspicuous posted notice, for monitoring of email, phone and internet use | In force since May 2022 | https://www.nysenate.gov/legislation/laws/CVR/52-C*2, https://www.mofo.com/resources/insights/211115-new-york-enacts-employee-monitoring-notification-law |
| **Connecticut and Delaware** monitoring notice | CT, DE | Prior written notice of electronic monitoring (CT broader). Statute text not retrieved: UNVERIFIED | UNVERIFIED | https://www.ebglaw.com/insights/publications/monitoring-employee-email-or-other-electronic-usage-new-york-will-require-employers-to-provide-notice |
| **ECPA** (federal) | US | Interception of communications is limited, with consent and business-use exceptions. Reading stored company email under the employer's own system is generally allowed. Not researched in this session: UNVERIFIED | In force | Not fetched |
| **State social-media privacy laws** (27 states per FindLaw, Apr 2026) | US states | Employer may not demand usernames, passwords or access to personal accounts, nor penalize refusal. Exceptions (for example misconduct investigation) vary. Laws target access to login or private content. Whether reading a **public** personal GitHub is covered is UNVERIFIED. Safe design: never require it, never penalize refusal, public data only, employee-initiated | Various, 2012 onward | https://ncsl.org/research/telecommunications-and-information-technology/state-laws-prohibiting-access-to-social-media-usernames-and-passwords.aspx, https://www.findlaw.com/legal/technology/modern-law-practice/social-media-privacy-law-developments-restricting-employer-acce.html |
| **FCRA exposure** (Eightfold suit) | US | Third-party scoring of workers or applicants may be treated as a "consumer report" if the theory prevails. Untested | Case filed 20 Jan 2026, pending | https://www.ebglaw.com/workforce-bulletin/ai-hiring-tools-and-consumer-reports-understanding-the-eightfold-litigation |

Pointers for the first (Indian) customers: DPDP s.7(i) covers company-system work data used for employment purposes. It does not cover personal accounts, so use s.6 consent there. The "free" test matters because the employer holds power over the employee. Any EU-based staff or any customer with EU or California staff pulls in the rows above regardless of where the customer is based.

## 4. Design patterns to copy and anti-patterns to avoid

**Copy**
- AI drafts from linked evidence, human rates (Lattice Evidence-based Review Drafts, Windmill).
- Minimum group size, default 5 or higher, applied to every aggregate shown to a manager (Viva Insights).
- Pseudonymize at the edge, keep keys with the customer (Worklytics psoxy).
- Employee sees their own profile first, can add context and dispute (Viva personal insights, GDPR Art 22, CCPA access/appeal).
- Goal progress linked to a live query on the task store, so evidence is work done, not self-report (Lattice-Jira).
- Team-level flow metrics for teams, surveys alongside system data (Swarmia, DX).
- Every inferred skill carries source, date and confidence, and is editable (Workday Skills Cloud ideas, response to the Eightfold suit).
- Per-region switches and a decision record (who decided, what evidence was shown, AI role) to meet Art 22, Colorado SB 26-189 and California ADMT.
- Scoped, purpose-bound, logged agent access (Lattice MCP is the precedent, add what Lattice lacks: purpose tokens and redaction).
- Annual bias test on any ranking, shortlist or "keep" suggestion (NYC LL144, EU AI Act Art 10, 15).

**Avoid**
- Keystroke, screenshot, idle-time, webcam, mouse data (monitoring vendors).
- Emotion or sentiment inference on employees (EU AI Act Art 5(1)(f)).
- A single per-person productivity number from commits or messages (McKinsey 2023 backlash, Swarmia/DX stance).
- Opaque match scores for internal mobility (Eightfold suit).
- Requiring personal GitHub or any personal account (state social-media laws, DPDP consent fairness). Never let refusal change an outcome.
- Auto-generated recommendations to fire, with no named human decision-maker (GDPR Art 22, SCHUFA).
- Manager views below group-size floor, or "drill to one person" through filters (Viva floor).
- Relying on a vendor claim of "anonymization" when data is pseudonymized (Worklytics wording).
- Bulk profile export to external agents.

## 5. Sources

- EU omnibus: https://www.regulation-ai.eu/en/omnibus/, https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/, https://regdossier.eu/digital-omnibus-ai-act/, https://www.whitecase.com/insight-alert/eu-agrees-digital-omnibus-deal-simplify-ai-rules
- AI Act Art 5(1)(f): https://fpf.org/blog/red-lines-under-eu-ai-act-unpacking-the-prohibition-of-emotion-recognition-in-the-workplace-and-education-institutions/, https://legalblogs.wolterskluwer.com/global-workplace-law-and-policy/the-prohibition-of-ai-emotion-recognition-technologies-in-the-workplace-under-the-ai-act/
- GDPR Art 22: https://iapp.org/news/a/key-takeaways-from-the-cjeus-recent-automated-decision-making-rulings, https://cms.law/en/deu/legal-updates/impact-of-the-cjeu-s-schufa-judgment-on-the-use-of-ai-in-hr
- DPDP: https://yourstory.com/2025/11/digital-personal-data-protection-dpdp-rules-2025-rollout-over-18-months, https://www.barandbench.com/law-firms/view-point/navigating-legitimate-use-exemption-employee-data-digital-personal-data-protection-act-2023, https://www.mondaq.com/india/data-protection/1755320/is-consent-required-to-process-employees-personal-data-under-the-dpdp-act
- US: https://www.osc.ny.gov/state-agencies/audits/2025/12/02/enforcement-local-law-144-automated-employment-decision-tools, https://www.insideprivacy.com/state-privacy/admt-law-round-up-what-employers-need-to-know-about-recent-admt-laws/, https://www.finnegan.com/en/insights/articles/colorado-replaces-landmark-ai-act-an-overview-of-the-new-sb-26-189-framework.html, https://www.akingump.com/en/insights/alerts/new-california-regulations-regarding-employer-use-of-automated-decision-making-technology-compliance-required-by-january-1-2027, https://www.nysenate.gov/legislation/laws/CVR/52-C*2, https://www.findlaw.com/legal/technology/modern-law-practice/social-media-privacy-law-developments-restricting-employer-acce.html
- Products: https://learn.microsoft.com/en-us/viva/insights/privacy/privacy-considerations, https://docs.worklytics.co/psoxy, https://lattice.com/blog/introducing-evidence-based-review-drafts, https://lattice.com/blog/july-2026-product-updates, https://lattice.com/integrations/jira, https://www.gowindmill.com/blog/windmill-raises-12m-seed/, https://www.axios.com/pro/enterprise-software-deals/2026/04/28/hr-tech-windmill-performance-ai, https://www.workday.com/en-us/products/human-capital-management/skills-cloud.html, https://blog.workday.com/en-us/rising-product-announcements.html, https://www.ebglaw.com/workforce-bulletin/ai-hiring-tools-and-consumer-reports-understanding-the-eightfold-litigation, https://leaddev.com/career-development/what-mckinsey-got-wrong-about-developer-productivity, https://dannorth.net/mckinsey-review, https://jellyfish.co/jellyfish-vs-dx/, https://www.swarmia.com/developer-experience-platform

## 6. Gaps to close before the spec is final

Confirm on first-hand sources: EUR-Lex text of Regulation 2026/1744, DPDP Rules rule numbers and whether notice applies under s.7(i), Colorado SB 26-189 employment scope and the court stay, Illinois final rules, CT/DE statutes, BetrVG s.87(1) no.6 case law, Workday-Confirm, Gloat, Beamery, SuccessFactors skills mechanics, Peoplelogic, independent evidence on monitoring backlash, Jellyfish and LinearB official per-person policies. Get counsel to review before any EU or California customer is onboarded.
