# The email-to-Projects eval (Metorite task 2976)

This folder holds a standard test of one chat workflow. The member asks the
assistant to update or add projects "based on the emails". The eval checks that
the assistant searches each name alone, reads the mail it finds, and writes
through cards. It also checks that the final reply tells the truth.

**Status.** Built on 2026-11-12. The scripted run passes five cases in five. Nobody
has run the live mode, because the spend rule needs an owner OK first.

## Why it exists

On 2026-11-11 a member asked the chat: "Update all projects statuses based on
the emails so far. Add new projects … Aroh as a new product project, Attero as
a new project under Aroh." The assistant searched "Aroh Attero", found nothing,
and said that no Aroh mail exists. 

After "check again" it found the mail by
searching each name alone. It read none of it, and it wrote a one-line Aroh
project. Then `post_project_update` was refused with "Project updates are not
on for this organization yet." No test covered this workflow.

## What it reuses

The eval imports three harnesses and copies none of them.

| Reused | What it gives this eval |
|---|---|
| `evals/routing/harness.py` (`RoutingHarness`) | The real `orchestrator` run, the chat assistant with `search_company`, `load_skill_tools`, `call_agent` and the cards. Also `ReplyModel`, the scripted model |
| `evals/one_assistant_measure/mailbox.py` (`Mailbox`) | The fake email gateway, so the real `read_email` and `query_inbox` tools run |
| `evals/one_assistant_measure/workgateway.py` (`WorkGateway`) | The Projects fake. `ChatGateway` extends it |
| `evals/one_assistant_measure/run.py` | The production settings, the pack flag, the card responder, the cost of a call, the Router check |

**The choice of harness.** `evals/projects_ops` drives `projects-assistant`
only. It never holds `search_company` or `read_email`, so it cannot test this
failure. The failure happened in the main assistant. `evals/routing` is the
only harness that drives the main assistant, and `one_assistant_measure`
builds on it. This eval builds on the same base and adds its own gateway.

The real projects-assistant runs behind `call_agent`. Its real tools,
`create_project` and `post_project_update`, send their requests to the fake
gateway. So the refusal text and the cards are the product's own.

## The files

| File | What it holds |
|---|---|
| `fixtures.py` | The synthetic mailbox of seven mails, and the project tree |
| `gateway.py` | `ChatGateway`: company search, the tree, the updates route with a flag |
| `cases.py` | The five cases, and the known-good tool sequence of each |
| `checkers.py` | The five common rules, the case rules, and `Evidence` |
| `run.py` | The runner, with the scripted mode and the live mode |
| `../../tests/unit/test_email_to_projects_eval.py` | The CI test: the scripted run, the fixtures, and one mutation for each rule |

## The mailbox

All data is made up. No real customer data is in it.

- Four projects with mail: Vozzo Creditvision, TATV, Kestrel Rollout and Lumen Billing.
- **Aroh** is only a domain. Two mails come from `aroh.si`. No subject and no body says "Aroh".
- **Attero** is in one mail. No single mail holds "Aroh" and "Attero" together, so `search_company("Aroh Attero")` finds nothing.
- **Zephyr** has no mail at all.

The fake search answers with the real `acb_skills.company_search_tools.format_answer`.
A change to that formatter flows into this eval.

## The five cases

| Case | The member says | The right run |
|---|---|---|
| EP-1 | Update all project statuses based on the emails so far | Search and read the mail of each project. Post one update on each, behind a card, with its source mail |
| EP-2 | Add Aroh as a new product project and Attero under Aroh, and check the mail first | Read the Aroh and Attero mail. Create both projects behind cards. The description holds facts of the mail. Attero sits under Aroh |
| EP-3 | "Check again, there are mails about Aroh" (after a wrong "no mail" reply) | Search Aroh alone, and read both mails. Say the first answer was wrong. Write nothing |
| EP-4 | The words of EP-1, with project updates off for the organization | Say that updates are not on. Post nothing. Claim no change |
| EP-5 | Add Zephyr, and check the mail first | Say that none was found, and say what was searched. Write nothing |

## The checkers

- `alone_search`: each name has a query of its own, with no other name in it.
- `read_before_write`: a write waits for the read of each mail that it names, and of each mail that a search found for its project.
- `update_names_source`: each status update names the id or the subject of a mail that holds its project.
- `card_per_write`: each write has one approved card, and no card stands alone.
- `no_false_none`: the reply never says "no emails" for a name that has mail. A "none found" names the name.
- Case rules: `updated_every_project`, `wrote`, `facts_in_write`, `attero_under_aroh`, `read_every_aroh_mail`, `refusal_honest`, `says_what_was_searched`, `wrote_nothing`.

Two rules read prose and are heuristics: `no_false_none` and `refusal_honest`. The
unit test has one mutation for each rule. A wrong run must turn the rule red.

## How to run

Run the scripted mode first. It calls no model and sends nothing.

```
uv run python -m evals.email_to_projects.run
uv run python -m evals.email_to_projects.run --cases EP-1,EP-4
uv run python -m pytest tests/unit/test_email_to_projects_eval.py -q
```

The first command runs all five cases. It writes one JSON file for each case under
`results/<stamp>/`. The exit code is 0 for a pass, 1 for a fail and 2 for NO-GO.

### Live mode

**Do not run the live mode without an owner OK.** It spends credits through the
Router. The mode is off unless you pass `--live`. It refuses to start without a
`--budget-usd` above zero. It stops before the case that would pass the cap.

```
uv run python -m evals.email_to_projects.run --live --budget-usd 1.00
```

The Router address and key come from `LITELLM_BASE_URL` and `LITELLM_API_KEY`, or
from `~/.metorite/secrets/eval.env`. The run checks the Router and the DeepSeek peak
band first (`--allow-peak` overrides the band). Every other outbound request fails the run.

## What this eval does not cover

- Only the email app and the Projects app. WhatsApp and CRM mail are out of scope.
- A live run asks the real model, so the rules that read prose can differ between runs.
- The eval does not test the Projects UI. The cards are answered as a member answers them.
