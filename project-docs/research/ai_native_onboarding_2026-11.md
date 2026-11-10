# Ask one question, then act on real data

**Bottom line.** No product in the evidence runs a documented AI interview that sets up a company, so Metorite would lead a thin field. The best 2025-2026 products do three things. They ask for one or two things and infer the rest. They put output where the person already works. They gate team-wide data behind an admin step, then let each member opt in.

The only measured test of AI-led onboarding comes from Amplitude. An empty chat box lost to clickable starting points. One job-title question that routed to role-tailored clicks doubled new-user conversion ([Amplitude](https://www.amplitude.com/blog/ai-chat-box-tests.md)). That fits the D150 design: one welcome question, then a Get started card.

Build the AI-led layer inside that card. Give the admin a draft company profile from public material and a connection step with visible progress. Give the joiner a different path that starts from what the organization already holds. Treat every number below as a weak prior. Almost all of it is vendor-reported, and nothing was measured on a B2B work operating system.

## What the evidence is worth

The evidence has three grades, and the recommendations lean on them differently. **Measured evidence** is rare. It is Amplitude's own A/B tests (no sample sizes given), a 2017 Google study of Android permission prompts, and a16z retention curves for AI products.

**Vendor claims** are the bulk: Chameleon and Userpilot benchmarks, Fyxer's "2 minutes" setup, Viktor's "10 to 15 minutes", and Perspective AI's "3.2x activation lift". The Perspective figure comes from firms that had already A/B tested, so selection bias inflates it. Treat it as marketing ([Perspective AI](https://getperspective.ai/blog/ai-customer-onboarding-67-percent-adoption-2026-activation-benchmark)). **Inference** is everything I derive from patterns, and I mark it as such.

Three benchmark priors survive scrutiny as rough shapes. Average B2B activation is about **37.5%**, and checklist completion averages **19.2%** (median 10.1%) in Userpilot's data ([Userpilot](https://userpilot.com/blog/saas-onboarding-benchmarks/)). In Chameleon's 2023 data, tours that users launch themselves complete at **64%**, against **33.5%** for all tours. Only about one user in ten opens a checklist launcher ([Chameleon 2024](https://www.chameleon.io/benchmark-report-24)).

The lesson is not to chase these numbers. Checklist completion is a poor activation metric, and pull beats push. Both findings come from vendors that sell onboarding tools. Definitions of "activation" differ by company, so the figures are not comparable targets. No Pendo, OpenView or Lenny benchmark was retrieved, and no independent 2025-2026 funnel sample exists in the notes.

The AI-specific evidence is thinner still. Amplitude found that new users given feature prompts or an empty chat box did worse than users shown familiar, clickable common paths. A centered chat-first Home raised engagement 55%. A chat-first chart empty state lost to control, while an in-workflow "Build this chart with AI" entry point won ([Amplitude](https://www.amplitude.com/blog/ai-chat-box-tests.md)).

I infer that "chat-first" is a hypothesis to test per surface, not a rule. The one measured AI-onboarding lesson is a single cheap question that routes to concrete clicks. Nobody has published a second independent test.

## The best products ask little and infer the rest

The strongest first-run pattern in the notes is Attio's mail sync. The member connects an account. Attio then creates person records only for contacts the team has emailed or met, and company records per non-public domain. It shows a progress tracker, then an "In sync" state and a confirmation email. It shares subject lines and metadata with the team by default and keeps bodies private ([Attio help](https://attio.com/help/reference/email-and-calendar/email-and-calendar-syncing)).

Fyxer works the same way from the member's side. It renders output as labels and drafts inside the member's own Gmail. It asks only for the connections plus a few category and meeting choices, and it admits that early suggestions may be off ([Fyxer docs](https://docs.fyxer.com/get-started/checklist)). The tool writes the context the person would otherwise type. Claude Code's `/init` does the same for a repository, though only third-party guides describe it ([DEV Community](https://dev.to/rajeshroyal/init-let-claude-write-its-own-onboarding-docs-1hp3)).

Waiting is the weak point. Glean tells admins that crawl and index take 2 to 3 days for a small organization and 10 to 14 days for a large one. It also says that users should not get access until its ML phase ends, because early results are "significantly degraded" ([Glean](https://docs.glean.com/get-started/review/crawling-and-learning)). Rovo admins report connectors that show zero indexed objects and fail without logs ([Atlassian Community](https://community.atlassian.com/forums/Rovo-articles/Why-Do-Rovo-Connectors-Show-0-Indexed-Objects-Champions-Slack/ba-p/3211241)).

Viktor takes the opposite approach for light setups. It connects one tool and runs one task whose source the admin can scan in under a minute ([Viktor](https://viktor.com/blog/how-to-set-up-your-ai-employee-in-slack)). That is a vendor claim. My inference is that one pattern is unfilled: a fast first output from a small source while slow sources index. The small source can be the calendar and the last 30 days of mail. No document I found describes it.

The notes found **no product that interviews the admin or reads the company website as a documented setup step**. Dust can crawl public websites as a data source, and Notion agents use a hand-written instructions page ([Dust](https://docs.dust.tt/docs/user-documentation/admins/quickstart.md), [Notion 3.0](https://www.notion.com/blog/introducing-notion-3-0)).

Microsoft's guided onboarding for Copilot is the one AI-led exception. Its Learning Agent starts a 5 to 10 minute flow. A Starter journey asks questions, and a Premium journey uses work and role context. It sits in Microsoft's October 2026 Message Center, in preview, with no outcome data ([MC1486291](https://mc.merill.net/message/MC1486291)).

So an AI-led company setup is open ground. It may also be open because wizards work well enough. I cannot tell which.

## The creator path: a draft profile, a safe connect, a first output

The recommendation is to extend the D150 Get started card with an assistant that does the typing. Stripe supports the shape. A new account works at once in test mode, and hardening sits in a persistent eight-item checklist where inviting teammates is step seven ([Stripe](https://docs.stripe.com/get-started/account/checklist)). Stripe stores tick state in the browser cache. Metorite ticks from real server state per organization and member, which D150 already requires and which fixes that weakness.

**Step one, company context.** The welcome question routes the admin. The first card step then offers a draft company profile built from the admin's own domain and website. The admin edits or confirms it. Treat the draft as a proposal with sources, never as a silent write. This follows D110: the profile changes only on the admin's words or a yes.

The evidence backs source display. Inferred facts should carry their source, and stored memory errors are a known complaint ([OpenAI Community](https://community.openai.com/t/memory-context-corruption-contradiction-in-chatgpt-gpt-4-5/1144222)). That complaint comes from forum posts, so it is anecdotal. The chat fills the profile form and the admin presses save, which keeps chat and UI at full parity.

**Step two, connect the admin's own mail and calendar.** Copy Attio's scoped defaults: people the admin has contacted, metadata before bodies. Show a progress tracker and send a "done" notice. Then produce a first output from calendar and recent mail, such as a prepared day on My Day. Anything the assistant writes on the admin's own work acts at once with Undo. Anything that touches other people asks first.

**Step three, org-level choices.** Every team product in the notes uses two gates. The owner enables a connector for the organization, then each member authenticates their own account ([Claude connectors](https://support.claude.com/en/articles/10166901-use-connectors-to-extend-claude-s-capabilities), [OpenAI admin quickstart](https://help.openai.com/en/articles/20001264-chatgpt-enterprise-admin-quickstart)). Metorite maps onto that directly. Personal data such as mail and calendar stays per-user OAuth. Document stores, if added later, may justify domain-wide scopes, the choice Glean and Sana make for ACL mirroring ([Glean Drive](https://docs.glean.com/connectors/native/gdrive/new-setup), [Sana](https://support.sana.ai/en/articles/248426-google-drive-centralized-integration-guide)).

**Step four, invite and import.** The assistant drafts the invite list from the domain and the admin presses the button, as the member-write rule requires. An import from an old tool is an admin action with user mapping by email, as Linear does ([Linear](https://linear.app/docs/jira-to-linear)). Keep import off the forced path. Linear treats it as a settings action, and I found no product that forces it.

## The joiner path: start from what the organization already holds

This is where the evidence is thinnest, and I say so plainly. **No fetched source documents a joiner first screen that shows what the team is working on.** What exists is structural. Linear publishes separate admin and joiner checklists, and the joiner list runs: sign in, install the apps, set personal settings, join a team ([Linear](https://linear.app/enablement/guides/set-up)). Notion and Canva offer to join an existing workspace or team from the person's email domain ([Notion](https://www.notion.com/help/workspace-settings), [Canva](https://www.canva.com/help/join-teams/)). Absence from help pages does not prove absence in the app, so this may be open ground or may be undocumented practice.

My design inference follows from the structure. At sign-up, a person whose domain already has a tenant sees "join" before "create". That prevents duplicate organizations, a problem Disguise names as the reason for its domain capture ([Disguise](https://help.disguise.one/cloud/cloud-dashboard/domain-capture)). Default domain joiners to least privilege, because Notion's Free plan makes them owners ([Notion](https://www.notion.com/help/workspace-settings)). Skip the company interview, since the admin already did it.

The joiner's welcome question differs, and it reads the organization. It reads the projects they sit in, the tasks assigned to them, the person who invited them, and the company profile the admin confirmed. The Get started card for a joiner has three or four steps, and each does its act in place. The steps are: connect my mail and calendar, review my assigned tasks, set my notifications, and ask the assistant one thing about my work.

Member memory follows the strongest precedent in the notes. On Claude Team and Enterprise, memory is off until each member turns it on, and owners cannot read member memories ([Claude memory](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context)). That matches D110. The assistant writes to the member's profile only on the member's words or yes, and the admin cannot read it.

## Connection limits shape the plan more than design does

Google's rules are the hard constraint. Gmail scopes including `gmail.modify` are restricted. Restricted apps need verification and an annual CASA security assessment, and verification can take several weeks ([Google](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification), [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)). Unverified apps cap at 100 users, and test users are permanent ([Google Health docs](https://developers.google.com/health/app-verification?hl=en)). A CASA estimate of 2 to 6 weeks and USD 500 to 4,500 comes from a Nango search summary ([Nango](https://nango.dev/docs/api-integrations/google-shared/google-security-review)). I did not confirm it on Google's own pages.

Metorite's Testing mode therefore has a direct design cost. Refresh tokens last 7 days, so Connect will break weekly for every Gmail user until CASA clears. Build a visible "reconnect" state with one click, never a silent failure, and count it in the connect-rate metric. Cap pilots below 100 Gmail users. I did not confirm Calendar scope tiers. The notes carry my unverified recollection that they are sensitive, not restricted, so check Google's scope list before you split Calendar from Gmail.

For Microsoft, unverified publishers show an "Unverified" label, and tenants can require admin approval. Microsoft states that customers cannot consent themselves for some external-tenant apps ([Microsoft Learn](https://learn.microsoft.com/en-us/entra/identity-platform/application-consent-experience)). Build the "ask your admin" path as a ready-made consent link the member sends. Log each failure by cause: declined, admin-blocked, abandoned at the warning. Pursue publisher verification early.

No source gives Gmail or Outlook consent drop-off data, and none tests scope wording or read-then-write upgrades. The only rigorous permission study is mobile: 84% of Android runtime requests were accepted in 157 users over six weeks ([Google Research](https://research.google/pubs/exploring-decision-making-with-androids-runtime-permission-dialogs-using-in-context-surveys/)). It transfers loosely at best. Measure this step yourself.

Privacy adds a second limit. A connecting admin cannot consent for colleagues and outside contacts in their mail. India's DPDP notice and consent duties start 13 May 2027 ([AZB Partners](https://www.azbpartners.com/bank/update-indias-digital-personal-data-protection-framework-comes-into-effect/)). GDPR treats employee consent as seldom freely given ([Help Net Security](https://www.helpnetsecurity.com/2020/06/29/analyzing-employee-emails-gdpr/)). The controller and processor roles for Metorite are unsourced, so get legal advice.

Narrow default scope and a stated "what I did with your data" line are the guards the better products use. Any output sent to a non-member, like a recap or nudge, needs a preview and an off switch. Otter's class-action allegations over recording non-users show the cost of skipping that ([UC Today](https://www.uctoday.com/security-compliance-risk/otter-ai-on-trial-and-the-ai-notetaker-industry-with-it/)). These are allegations, not findings.

## Easing into daily use needs a countable action and a scheduled return

Fyxer pairs a scheduled daily briefing with three habit steps: clear priorities, send lightly edited drafts, correct labels ([Fyxer docs](https://docs.fyxer.com/get-started/checklist)). Superhuman defined its aha as 50 clearing actions, per its former growth head ([Pocus](https://www.pocus.com/blog/superhumans-path-to-plg-virality-delight-and-humans-at-the-helm)). Neither source gives retention numbers.

Inference: Metorite needs one countable action and one scheduled return. My Day already supplies the return. Add a daily brief with clickable items at a time the member picks. Amplitude's evidence favors clicks over an empty box.

Invites should arise as a by-product of useful output, such as a task assigned or a summary shared. An "invite team" form should not be the source. That pattern is documented for Superhuman's signature and for Granola's shared notes ([Pocus](https://www.pocus.com/blog/superhumans-path-to-plg-virality-delight-and-humans-at-the-helm), [AlternativeTo](https://alternativeto.net/news/2025/5/granola-launches-new-collaborative-shared-folders-ai-citations-and-advanced-model-support)). Outside recipients carry the privacy cost above.

I propose these metrics as my own, not as reported benchmarks. Activation is one accepted AI output on the member's own data in session one, plus a second on another day within 7 days. Add connect rate by cause and the share of outputs accepted, edited or rejected. For teams, a second member active within 14 days is the test. Guardrails are Undo rate, wrong-fact corrections and memory deletions.

For retention, a16z recommends month-3 retention and the M12/M3 ratio for AI products ([a16z](https://a16z.com/ai-retention-benchmarks/)). Do not use Amplitude's message-count growth as success. Message volume can rise while value does not.

## Conclusion

The evidence says the winning move is narrower than "AI-led onboarding". One question that routes to clickable, data-grounded steps has a measured result behind it. Open chat does not. The unusual part of the Metorite plan has no precedent in the notes. That part is an assistant that drafts the company profile from the admin's domain, and a joiner path built on what the organization already holds. It is a bet, so ship it behind a flag and measure it against the plain D150 card.

Two risks outrank the design work. The Google Testing-mode limits (100 users, 7-day tokens, no CASA) will cap and interrupt Gmail connection however good the flow is. A Microsoft tenant that needs admin consent will block the member unless the "ask your admin" link works. Start publisher verification and CASA now, since both sit on the critical path. Fill the evidence gaps with a hands-on capture of the first runs of Slack, Notion, Linear, Attio, Fyxer and Microsoft's Learning Agent. Pair that with an owner-run A/B on the first question and the draft profile.
