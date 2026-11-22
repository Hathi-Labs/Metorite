# People intelligence: what other products do, the law, and workplace games (research record)

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

## Part 1. HR products, people analytics and the law (2026-11-22)

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

---

## Part 2. Workplace games and fair scores across roles (2026-11-22)

> Written for the owner's second message (scores, a leaderboard, fair scores across roles). The same method and labels apply. **[VERIFIED]** means a search result confirmed the claim this session. **[MEMORY]** means prior knowledge that nobody checked again.

Date: 2026-11-22. Method: web search plus a few page reads. Labels: **[VERIFIED]** = confirmed in a search result this session. **[MEMORY]** = my prior knowledge, not re-checked. **[UNVERIFIED]** = could not confirm. Peer-reviewed work and vendor claims are marked apart. Not legal advice.

### 1. Summary (10 lines)

1. Gamification works on average, but effects are small to medium and very context-dependent. The 2019 review of 819 studies found positive leaning results with a "remarkable" amount of mixed ones.
2. Most rigorous evidence comes from education, not work. Work evidence is thin, and much of the pro-gamification work evidence is vendor claims.
3. Consent decides the sign. Mollick and Rothbard: games raised positive affect only for people who consented, and lowered it for those who did not.
4. Public rank is a double-edged tool. It lifts effort for some, but in field experiments it also caused complacency in leaders, disengagement in low ranks, and in one sweater factory a net drop in output.
5. Rank for status alone, with no money, still raises effort, but a minority sabotage peers or cheat (Charness et al.).
6. Linking points to pay, firing or ratings produces gaming: Wells Fargo, Microsoft stack ranking, Google's own OKR pay experiment.
7. Scores that rank unlike roles on one raw scale are unfair. Use each person's attainment against their own role target, then compare inside a role cohort.
8. Never score engineers on one per-person metric. The SPACE authors list "one metric tells everything" as a myth.
9. Safest design: opt-in, self-comparison first, team boards, short seasons, a visible formula, "how to improve" guidance, and a hard wall between the game and pay or appraisal.
10. Legal: a named employee ranking is likely to need works council consent in Germany, a DPIA and transparency under GDPR, and may be Annex III high-risk under the EU AI Act (from 2 Dec 2027). India: no specific rule found, DPDP Act 2023 general duties apply.

### 2. Findings by question

### Q1. Does workplace gamification work?

| Source | Type | Finding | Status |
|---|---|---|---|
| Hamari, Koivisto, Sarsa 2014 (HICSS) | Review, 24 studies | Mostly positive, but depends on context and the users | VERIFIED |
| Koivisto & Hamari 2019 (Int. J. Info. Mgmt) | Review, 819 studies | Leans positive, "the amount of mixed results is remarkable", the authors call the 2014 review modest | VERIFIED |
| Sailer & Homner 2020 (Educ. Psych. Review) | Meta-analysis, learning | Cognitive g = .49 (k=19), motivational g = .36 (k=16), behavioural effect not retrieved. Motivational and behavioural effects were less stable in high-rigor studies. Fiction and mixing competition with collaboration helped behaviour outcomes | VERIFIED (behavioural g: UNVERIFIED) |
| Mollick & Rothbard 2014 "Mandatory Fun" | Field experiment, 200+ salespeople, plus lab | With consent, games raised positive affect. Without it, they lowered it. Consent came from legitimation and a sense of individual agency. Wharton summary: no sales gain overall and some drop for non-consenters | VERIFIED |
| Cardador, Northcraft, Whicker 2017 (Human Resource Mgmt Review) | Theory | Argues effects depend on design and on fit to the work. Details | MEMORY |
| Landers, Bauer, Callan 2017 | Experiment on leaderboards | Leaderboards work through goal setting: they raise performance when they give a clear, specific, hard goal. Details | MEMORY |

Reading: the education meta-analysis is the best effect-size evidence, and it does not transfer straight to adult work. No result I found shows a large, robust gain in knowledge-worker output from points and badges.

### Q2. Rank feedback and leaderboards in field experiments

| Study | Setting | Result | Status |
|---|---|---|---|
| Barankay, "Rank Incentives" | Furniture salespeople, n=1,754, randomized | Removing rank feedback raised sales about 11% (0.1 SD). Effect was in men only. Leaders grow complacent ("already No. 1") | VERIFIED |
| Barankay (crowd-sourcing task) | Online workers | Those told their rank were less likely to return and less productive than a control | VERIFIED (through summary) |
| Ashraf and Bandiera-linked work | Sweater factory | Private rank had no effect. Public rank cut productivity, driven by high-ranked workers | VERIFIED (through summary, exact citation UNVERIFIED) |
| Azmat & Iriberri 2010 (J. Public Econ.) | School, natural experiment | Telling students their grade against the class average raised grades about 5%, across the whole distribution. The gain vanished when the feedback stopped | VERIFIED |
| Blanes i Vidal & Nossol 2011 (Mgmt Sci.) | Firm personnel records | Rank feedback without prizes raised output. Search failed. Details | UNVERIFIED |
| Charness, Masclet, Villeval 2014 (Mgmt Sci., "dark side of competition for status") | Lab, real effort | Learning relative standing raised effort. Some paid to sabotage others or inflate their own rank. Strong group identity cut sabotage | VERIFIED |
| Bandiera, Barankay, Rasul (team incentives and rank) | Fruit farm | Not searched | UNVERIFIED |
| Disengagement at bottom ranks | | The crowd-sourcing result above and the sweater result point that way. No large field study of "bottom of a leaderboard" found | PARTIAL |

When public rank helps: simple repeated tasks, a clear and measurable objective, a close race, a short horizon, and a population that opted in. When it hurts: leaders coast, low ranks quit, outcome is not fully in the person's control, rank is public and permanent, and quality is not measured. Gender and personality moderate it (Barankay: men only).

### Q3. Real company cases

| Case | What happened | Lesson | Status |
|---|---|---|---|
| Wells Fargo 2016 | Aggressive cross-sell goals. USD 185M fine, about 5,300 fired (2011 to Mar 2016). Later estimates about 3.5M improper accounts. Regulator said the pay plan enabled the abuse and the bank did not monitor it | Hard targets plus pay equals fraud | VERIFIED (news) |
| Microsoft stack ranking | Ended Nov 2013. A 2012 Vanity Fair article quoted engineers describing sabotage of colleagues | Forced ranking punishes collaboration | VERIFIED (through secondary source) |
| GE, Adobe, Deloitte | Dropped forced or annual ratings (Adobe 2012, GE 2015, Deloitte 2014 study: 58% of executives called performance management a waste of time) | Ranking people is costly, weak at improving them | VERIFIED (through SHRM summary) |
| Amazon FC Games (2019, expanded 2021) | Mini-games with points and badges on workstations. Amazon said it does not penalize non-players. Workers were mixed: some liked the break, some feared tracking | Reaction depends on trust that it is not surveillance | VERIFIED (news) |
| Disneyland Anaheim hotel laundry (2011) | Monitors ranked workers by name and speed. Workers called it the "electronic whip". Reports of skipped breaks, stress, injuries (union claim) | Named speed rankings read as surveillance | VERIFIED (secondary, LA Times original not read) |
| GitHub streaks (May 2016) | Streak counters removed without announcement, during a profile redesign. Critics said the graph rewards activity on many days. A research paper finds behaviour changed after removal | Streaks reward quantity and cause unhealthy habits. GitHub's own reason: UNVERIFIED | VERIFIED (partly) |
| Google OKRs | Grade 0.0 to 1.0, 0.6 to 0.7 is the "sweet spot" for aspirational goals. Committed goals aim for 1.0. Re:Work says do not use OKRs for reviews. Bock said an earlier OKR-to-pay link led to gaming | Keep goals apart from pay | VERIFIED (through secondary sources) |
| CNIL v Amazon France Logistique (Dec 2023) | EUR 32M fine under GDPR for scanner-based performance monitoring seen as excessively intrusive and putting "disproportionate pressure" on staff | Fine-grained individual speed tracking is a legal risk | VERIFIED. Appeal: Conseil d'Etat 23 Dec 2025 found an error of assessment on one point. Final outcome UNVERIFIED |
| Sales gamification vendors (Ambition, Spinify, SalesScreen, Hoopla, Zoho Motivator, Salesforce) | Vendor case studies claim uplift. I found no independent peer-reviewed test | Treat as vendor claims | UNVERIFIED (not searched) |
| Peer recognition (Bonusly, Kudos) | Not searched. Evidence is vendor-led | | UNVERIFIED |
| Duolingo streaks, Stack Overflow reputation | Non-work references. Not searched | | UNVERIFIED |

### Q4. Fair scoring across different roles

- **Attainment against own target.** Quota attainment is the sales standard. It makes a 90% attainer in a hard territory comparable to a 90% attainer in an easy one only if targets are set fairly. Most fairness risk sits in target setting. [MEMORY]
- **Within-cohort percentile or z-score.** Fair when cohorts are comparable and large enough (rule of thumb: 8 or more, UNVERIFIED). Percentiles force a bottom, which is the stack-ranking problem. Do not show a percentile to the person in a small cohort.
- **Personal best and trend.** Compares you to you. Avoids social-comparison harm. Azmat and Iriberri show relative feedback helps while shown, but that is school evidence.
- **Team scores.** Good for roles with shared outcomes. The Charness result suggests group identity reduces sabotage.
- **Calibration.** Managers review outliers. Ratings calibration is common in HR practice, but its fairness evidence is mixed [MEMORY].
- **Role metric norms** [MEMORY unless noted]:
  - Sales: quota attainment, pipeline coverage, win rate, cycle length.
  - Marketing: pipeline sourced and influenced, campaign results against plan, cost per qualified lead.
  - Engineering: DORA team measures (deployment frequency, lead time, change failure rate, recovery time). SPACE has five dimensions: satisfaction and well-being, performance, activity, communication and collaboration, efficiency and flow. Its authors list "one productivity metric can tell us everything" as a myth, and say context matters. VERIFIED.
  - Support: CSAT, first response and resolution time, reopen rate.
- **Goodhart and Campbell.** When a measure becomes a target it stops measuring well. Work examples: Wells Fargo accounts, GitHub single-commit days (VERIFIED above), lines of code, tickets closed by splitting tickets, deals pulled forward into the quarter.
- **Stopping gaming.** Pair every volume measure with a quality measure (deal kept, PR reverted, ticket reopened). Cap rewards for volume. Audit random samples. Keep rules visible. Make the score unusable for pay, so gaming has nothing to win.

### Q5. Design patterns the evidence supports

| Choice | Evidence-based leaning | Basis |
|---|---|---|
| Opt-in vs mandatory | Opt-in, with a clear why and a way out | Mollick & Rothbard (VERIFIED) |
| Self vs social comparison | Self first. Social comparison optional | Barankay, sweater factory (VERIFIED) |
| Top N vs full ranking vs near you | Full public rank is the riskiest. Top N shows the leader a ceiling and shames no one. A band "near you" is plausible but I found no direct test | UNVERIFIED for "near you" |
| Team boards | Reduce sabotage and bottom-rank shame | Charness (VERIFIED) |
| Time-boxed seasons | Resets leaders' complacency and gives low ranks a fresh start. Supported by theory, no field test found | Reasoning, not test |
| Recognition and badges | Low harm. Effect small | Sailer & Homner (VERIFIED) |
| Formula transparency | Needed for legitimacy and for GDPR. Legitimation drives consent | Mollick & Rothbard (VERIFIED) |
| "How to improve" guidance | Fits goal-setting theory: specific, hard goals, with feedback. The Landers result is MEMORY | |
| Separate from pay and appraisal | Strongly supported by cases | Google, Wells Fargo, Microsoft (VERIFIED) |

Self-determination lens: autonomy (opt-in, own goals), competence (clear feedback, how to improve), relatedness (team boards, recognition). Rankings and pay links undermine autonomy and relatedness. SDT as a framework is MEMORY.

### Q6. Law and works councils (not legal advice)

- **EU AI Act.** Annex III treats AI that monitors and evaluates worker performance and behaviour as high-risk (I recall this as Annex III point 4(b), not re-checked, UNVERIFIED). The Digital Omnibus moved the application date for Annex III systems from 2 Aug 2026 to 2 Dec 2027. One source says the Council approved it on 29 Jun 2026, and a law firm update of July 2026 calls it final. VERIFIED. A plain arithmetic score from rules may fall outside "AI system", an AI-generated score or ranking is more likely inside. Needs counsel.
- **GDPR.** Employee scores are personal data. Needs a lawful basis (consent is weak in employment, so expect legitimate interest), transparency (Arts 12-14), data minimisation, and likely a DPIA [MEMORY]. Art. 22 limits decisions based solely on automated processing with legal or similarly significant effect, so a score must not feed hiring, pay or firing without human review [MEMORY]. The CNIL fine above shows regulators act on performance tracking.
- **Germany.** BetrVG section 87(1) no. 6 gives the works council a co-determination right over technical systems designed to monitor behaviour or performance. The BAG held software counts (1 ABR 6/94, VERIFIED through summary) and individualisable data is the key test (1 ABR 45/11, VERIFIED through summary). I found no ruling about leaderboards specifically. UNVERIFIED. In practice a named ranking would need a works agreement where a council exists.
- **France.** CNIL guidance on employee rankings: not found beyond the Amazon decision. French law also requires informing and consulting the CSE before monitoring tools [MEMORY].
- **India.** No specific rule on employee leaderboards found. The Digital Personal Data Protection Act 2023 applies general duties (notice, purpose limits), though rules phasing is UNVERIFIED. Confirmed only as "nothing specific found", not as "none exists".

### 3. Design rules

**Do**
1. Make it opt-in, and let people leave without cost. (Mollick & Rothbard)
2. Default to a private view of your own score and trend. (Barankay, sweater-factory result)
3. Compare each person to their own role target and past self. (Quota attainment practice, Azmat & Iriberri)
4. Show team boards and recognition on the public surface. (Charness: group identity reduces sabotage)
5. Pair every volume measure with a quality measure. (Wells Fargo, GitHub streaks)
6. Show the full formula and the data behind each point. (Mollick & Rothbard, GDPR Arts 12-14)
7. Show "what moves your score next" for each person. (Goal-setting theory, Landers: MEMORY)
8. Run short seasons with reset and leave old scores behind. (Barankay complacency, reasoning)
9. Keep the score away from pay, ratings and firing, in code and in written policy. (Google, Microsoft, Wells Fargo)
10. Audit for gaming by sampling and by outlier alerts. (Goodhart cases above)
11. Consult employee representatives before launch where law requires. (BetrVG 87(1) no. 6)

**Do not**
1. Do not rank all staff in public by default. (Sweater factory, crowd-sourcing, Disneyland)
2. Do not use one number per engineer. (SPACE)
3. Do not rank different roles on one raw scale. (Role norms)
4. Do not use streaks that punish a missed day. (GitHub 2016)
5. Do not force a bottom percentile. (Microsoft)
6. Do not hide bottom-rank people's data. Do not show them a shame row. (Charness, Disneyland)
7. Do not let an automated score decide hiring, pay or dismissal alone. (GDPR Art 22)
8. Do not track fine-grained speed per person without need. (CNIL v Amazon)
9. Do not claim vendor uplift figures as proof. (No independent evidence found)

### 4. Proposed fair-scoring model for mixed roles (SYNTHESIS, not from a single study)

**Principle:** score each person against their own plan, inside their role, with a quality gate, and compare ranks only within a role cohort, and only if the person opts in.

1. **Role scorecard.** Each role has 3 to 5 measures from the role norms above. Weights are visible. Each measure has an outcome part (for sales: quota attainment) and a quality part (for sales: revenue kept, no refunds). Engineering uses team-level delivery measures plus personal contribution evidence, never a single count.
2. **Attainment, not raw volume.** For each measure, attainment = actual / own target, capped (for example at 120%) so volume cannot dominate. Targets come from the existing goals and measures, and are set by the person and manager.
3. **Quality gate.** The score is multiplied or capped by a quality factor. A fall in quality cuts the score faster than volume adds to it.
4. **Score = weighted attainment (0 to 100).** The headline is the person's own score and trend against their last season. This is the default view.
5. **Cohort view (opt-in).** Within a role cohort of enough size, show the band (top third, middle, rest) or "people near you", not the full list. Below the minimum cohort size, show nothing. No cross-role ranking.
6. **Team and cross-role view.** Cross-role comparison happens only at team level: team goal attainment. This is fair because every team uses its own targets.
7. **Seasons.** Quarterly seasons. Each starts fresh. Badges record effort and quality (for example, "closed the loop on all reviews"), not rank.
8. **Explain and improve.** Each score card lists the top two gaps and the evidence rows behind them, with the likely next step.
9. **Wall.** A flag in the data model marks the score "not for appraisal". Pay, rating and firing screens never read it. Add an automated test for that (test fence). An audit log shows who viewed whose score.
10. **Governance.** Written purpose, DPIA, works council or employee-representative consultation by market, review of the formula each season, and a review for gaming. An appeal path lets a person contest wrong evidence.
11. **Open points for the founder.** Is ranking needed at all, or is self-score plus team board enough? The evidence says self-score plus team board carries the least risk. Pilot with one willing team before wide launch, and measure engagement of the bottom third explicitly.

### 5. Sources

Peer-reviewed or first-hand
- Sailer & Homner 2020: https://link.springer.com/article/10.1007/s10648-019-09498-w
- Hamari, Koivisto, Sarsa 2014: https://research.aalto.fi/fi/publications/does-gamification-work-a-literature-review-of-empirical-studies-o/
- Koivisto & Hamari 2019 (summary page): https://webpages.tuni.fi/gamification/2019/06/24/the-rise-of-motivational-information-systems-a-review-of-gamification-research/
- Mollick & Rothbard, Mandatory Fun: https://papers.ssrn.com/abstract=2277103
- Barankay, Rank Incentives: https://repository.upenn.edu/bepp_papers/75
- Barankay interview: https://knowledge.wharton.upenn.edu/article/why-its-sometimes-better-not-to-tell-employees-where-they-stand/
- Azmat & Iriberri 2010: https://econpapers.repec.org/RePEc:eee:pubeco:v:94:y:2010:i:7-8:p:435-452
- Charness, Masclet, Villeval (IZA DP 5034): https://docs.iza.org/dp5034.pdf
- SPACE of developer productivity (ACM Queue / CACM): https://queue.acm.org/detail.cfm?id=3454124
- GitHub streaks and gamification study (arXiv): https://arxiv.org/abs/2006.02371
- BAG 1 ABR 6/94: https://www.anwalt24.de/urteile/bag/1994-07-26/1-abr-6_94
- BAG 1 ABR 45/11: https://www.bundesarbeitsgericht.de/?p=1148298

Press, law-firm and secondary
- Wells Fargo fine (NPR): https://www.npr.org/2016/09/08/493157959/wells-fargo-ordered-to-pay-185-million-fine-over-unauthorized-accounts
- Microsoft ends stack ranking (Computerworld): https://www.computerworld.com/article/1390584/microsoft-abandons-employee-ranking-system.html
- Annual review trend (SHRM): https://www.shrm.org/topics-tools/news/employee-relations/annual-performance-review-dead
- Amazon warehouse games (Engadget): https://www.engadget.com/2019/05/22/amazon-warehouse-games/
- Disneyland "electronic whip" (Consumerist): https://consumerist.com/2011/10/21/disneyland-hotel-workers-face-electronic-whip/
- GitHub blog on the 2016 profile change: https://github.blog/2016-05-19-more-contributions-on-your-profile/
- Google OKR grading summary (WhatMatters): https://www.whatmatters.com/okrs-explained/grading-okrs
- CNIL v Amazon France Logistique: https://cnil.fr/en/employee-monitoring-cnil-fined-amazon-france-logistique-eu32-million
- AI Act delay (Cloud Security Alliance note): https://labs.cloudsecurityalliance.org/research/csa-research-note-eu-ai-act-omnibus-vii-deadline-delay-20260/
- AI Act omnibus finalised (Orrick): https://www.orrick.com/ja-JP/Insights/2026/07/EU-AI-Act-Update-Digital-Omnibus-Finalizes-8-Compliance-Changes

Not checked this session: Cardador et al. 2017, Landers et al. 2017, Blanes i Vidal & Nossol 2011, Bandiera et al., vendor evidence, Bonusly and Kudos, GDPR Art 22 text, Annex III wording, India DPDP details.
