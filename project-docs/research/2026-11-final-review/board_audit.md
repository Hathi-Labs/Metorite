# Board final review 2026-11-10 (read-only)

Sources: board_stats/list_tasks (all counts), get_task on about 150 new slices by 4 audit agents, get_plan on 29 epics, get_project on 27 projects, 1 duplicate-analysis agent. Row files: board/wscx, board/auditor, board/audit-ks-hx-ch, board/auditor-wb-bk.

## 1. Per-project open tasks (backlog+todo+in_progress, 1819 total; 138 epics)
Estimate gaps are NOT countable by filter (needs get_task per task). Tag gaps = open minus tagged count (approximate if a task has two M or P tags).
| Project | open | no M tag | no P tag |
|---|---|---|---|
| MCP & External Agents | 235 | 164 | 152 |
| AI Studio | 221 | 15 | 14 |
| Projects/Tasks | 214 | 86 | 76 |
| CRM | 125 | 6 | 6 |
| My Email | 118 | 26 | 47 |
| Developer Workflow | 99 | 80 | 80 |
| Knowledge | 77 | 0 | 0 |
| Billing & Accounts | 64 | 0 | 21 |
| WhatsApp | 61 | 3 | 6 |
| Proactive Intel | 60 | 0 | 1 |
| Platform Core | 57 | 7 | 9 |
| Calendar | 52 | 0 | 0 |
| Personalization | 49 | 2 | 2 |
| AI Chat | 47 | 13 | 12 |
| Onboarding | 46 | 1 | 1 |
| Agent Tooling | 32 | 3 | 4 |
| Metorite Application (root) | 32 | 18 | 18 |
| Platform & CI/CD (root) | 29 | 5 | 9 |
| Meeting Notetaker | 26 | 1 | 2 |
| CI Pipeline | 23 | 16 | 12 |
| Observability | 23 | 7 | 7 |
| Package Host | 23 | 6 | 22 |
| My Day | 19 | 0 | 0 |
| Artifact | 18 | 1 | 1 |
| QoL | 18 | 0 | 1 |
| Secrets & Security | 16 | 5 | 6 |
| Operator Console | 8 | 2 | 1 |
| Roadmap | 8 | 3 | 8 |
| Build & Containers | 7 | 7 | 7 |
| Env & Delivery | 5 | 1 | 1 |
| Organisation | 4 | 1 | 1 |
| My Profile | 2 | 0 | 0 |
| User Stories | 1 | 1 | 1 |
| TOTAL | 1819 | 480 | 528 |

## 2. Dispatch readiness of new epics (strict = six blocks + estimate + M + P + parent)
Every audit agent: the "owning spec / spec slice it waits on" block is missing as a literal line on almost all slices. Counting a Depends-on line as the spec link: CX 41/41, KS 17/17, HX 8/8, CH 8/8, WB 33/37 checked (2674, 2698, 2666 fail), BK 11/12, CM 9/9, LL 8/8 (no Depends labels). Strict: 0 of ~140.
Not checked: WB 2694, 2697, 2701, 2703, 2705, 2706, 2707, 2709, 2711; BK 2683, 2685, 2688; CM 8 of 17; LL 5 of 13; RC 4 of 9; comments (get_task returns none, so hand-off comments partly unverified except the duplicates agent which used get_task_context).

## 3. get_plan (28 plans; 2546 is a single task, no plan)
No cycles. No critical-path slice lacks an estimate. No finish after 2026-12-15 (latest 2650 on 11-27, 1027 on 11-26, 2732 on 11-24). Ready 0: epic 2758 WS-CH (8 slices held behind HX). 44 of 44 WB slices and most others have no owner (expected).
ready/held: 2624 2/11; 2640 1/0; 2650 4/28; 2675 2/4; 2713 1/6; 2732 2/18; 2757 1/0; 2758 0/8; 2790 1/14; 2547 1/11; 2566 1/8; 321 ~51/15; 354 14/6; 495 21/0; 477 26/1; 669 4/0; 520 29/1; 1648 3/0; 445 7/0; 1946 2/0; 931 4/2; 2255 9/0; 1959 2/0; 783 2/3; 491 101/24; 1273 8/33; 1027 8/11; 1234 9/1.

## 4. Duplicates and overlaps
- TRUE DUPLICATE: 2698 into KS-11 2725 (keep 2725; WB-25 2692 stays as consumer).
- PARTIAL OVERLAP: 2761 vs 2786: both own "create project from won deal". Make 2786 the one write seam; narrow 2761 to estimates/capacity read; add link both ways; align M2/M3.
- CLEAN SPLIT, hand-off missing: KS-14 2728 vs WB-35 2703 (no comment or link; also ignore existing KB-9a gaps, flag KNOWLEDGE_GAPS, tasks 371, 948); CX-7 2744 vs AUTO 807/Studio (no links either end); CX-5b 2741 vs WB-32 2700/WB-6 (no link either end); RC-1 2642 vs SG-3b 2496 (no link); CX-10a/KS-7/WB-7 vs AP-1 Waiting seam 1615/2001 and 2622 vs AP-1b 1616 (no link); HX-3 2765 vs SH-1 946/SH-9 2317; HX-1 vs DLM-5 2151, SH-7 952 (risk of a second nudge path; bodies unread).
- CLEAN SPLIT, ok: 2731 vs KS-2 2716 (trim KS-2 Do list), WB-10 2661 vs BK-11 2687 (stale text in WB-10), CX-2 2735 vs CM-3 2794 (drop migrator bullet from CX-2, overlaps CM-10/CM-16), CH-1 with HX-1/2.

## 5. P0, M0, stale, due, owner gates
- P0 open: 96. M0 open: 40 (includes 12 in Metorite Application root: Customer Zero 519 children #544-554, due 2026-11-17).
- M0 anchor 2232 (due 11-17) In progress, last update 11-08. Zoho cutover CRM-Z10 799 In progress (due 12-15, update 11-07); 803 and 806 Backlog, due 12-15.
- In progress 169: <1d 120, 1-3d 45, 3-7d 2, 7-30d 2. Stale 5+ days: 2 (1140 STU-E, updated 11-01; 1125 GL glass, updated 10-31). 3-7d band also 2.
- Overdue: 1229 LD-8c (due 2026-10-31, in progress, updated today).
- Due by 12-15: 73 open tasks. Due within 14 days (by 11-24): 2232, 1229 overdue, 11-17 group (544-558, 597-599: 20 tasks), 260 PZ-10 (11-24). 
- Owner-gated (tag owner-gate 74, Owner 86, union 143). See final reply and the full per-project list in the owner-gate agent report (CRM 11, Knowledge 13, WhatsApp 14, Calendar 8, Email 9, MCP 4, Proactive 7, Personalization 7, Chat 3, Studio 11, Billing 12, Onboarding 2, Agent Tooling 4, My Day 2, Platform Core 9, Operator Console 4, CI/CD 5, Projects 5, Org 1, Metorite root 6, other 6). Some Owner-tagged tasks look stale (291 tracing, 265 trunk-based).

## 6. Delegation briefs
Missing: Roadmap d42090b0 and the Metorite space ca9a507b (description null). Platform & CI/CD has a short one. Metorite Application has "Start here" (24-row table). My Day has its brief.
