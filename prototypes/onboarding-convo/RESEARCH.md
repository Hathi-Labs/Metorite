# Research: onboarding through conversation, with very little friction

Task 4107, lane ishaan-onb-research, 2026-11-27. Time box: 15 minutes of web
search. **[I]** marks an inference. A row without a mark comes from the linked
source. Most sources are third-party teardowns, so the exact order of the steps
can be out of date.

## The table

| Product | The first thing it asks | When it asks for a connection or a permission | How it shows the first value | What is known to fail |
|---|---|---|---|---|
| **Boardy** (AI networking) | Your phone number. Then the AI calls you and asks what you work on. [TechCrunch](https://techcrunch.com/2024/10/24/ai-networking-startup-boardy-raises-3m-pre-seed) | It has no app, no login and no dashboard. It uses phone, email and LinkedIn. [Consumer Startups](https://consumerstartups.substack.com/p/boardy-your-ai-co-founder) | An introduction by email. Both sides opt in first. [Salesforge](https://www.salesforge.ai/blog/boardy-ai-review) | Value is slow: about 17 hours to a match. Short, vague answers give weak matches. [Review](https://justbeingresourceful.com/2026/11/04/boardy-ai-review-free-ai-intros-to-investors-and-customers/). A "personal" email that critiqued a photo caused a public apology. [BetaKit](https://betakit.com/your-mouth-commands-attention-boardy-ceo-apologizes-after-emphatic-negative-response-to-trump-themed-marketing-miss/) |
| **Superhuman** (email) | It books a 1:1 call with a person before you use the product. [Superhuman blog](https://blog.superhuman.com/the-fastest-way-to-inbox-zero-a-single-coaching-session/) | The expert connects the inbox during the call. [I] | Inbox zero in the first session. [First Round](https://review.firstround.com/superhuman-onboarding-playbook/) | The human step does not scale. Superhuman built a self-serve path after it learned from the calls. [First Round](https://review.firstround.com/inside-superhumans-onboarding-strategy-from-human-led-to-self-serve/), [The Baton](https://thebaton.substack.com/p/traps-of-superhuman-onboarding-) |
| **Linear** (issues) | Light or dark theme, then the ⌘K command menu. No role question. [uxdesign.cc](https://uxdesign.cc/the-onboarding-linear-built-without-any-ab-testing-b035572ced72), [Candu](https://www.candu.ai/blog/linear-onboarding-teardown) | GitHub comes late, as an optional step with an explainer. [Speakerdeck](https://speakerdeck.com/fmerian/linear-user-onboarding-magic-feeling) | You do the core act (make an issue) inside the tour. [Supademo](https://supademo.com/content/plg-onboarding-gallery/linear) | A minimal flow assumes an expert user. A new team gets no help with its workflow. [I] |
| **Notion** (workspace) | "How do you want to use Notion?" Work, personal or school. [Appcues](https://goodux.appcues.com/blog/notions-lightweight-onboarding), [Candu](https://candu.ai/blog/how-notion-crafts-a-personalized-onboarding-experience-6-lessons-to-guide-new-users) | After the first page exists. [OnboardMe](https://onboardme.substack.com/p/how-notion-solved-the-blank-page-product-strategy-deepdive) | Five templates that match the answer. The page is never blank. [Candu](https://candu.ai/blog/how-notion-crafts-a-personalized-onboarding-experience-6-lessons-to-guide-new-users) | Full templates overwhelm new users. [Notion RFC template](https://www.notion.com/it/templates/product-design-rfc) |
| **Duolingo** (learning) | Language and a daily goal, then "7 quick questions". [Appcues](https://goodux.appcues.com/blog/duolingo-user-onboarding) | The account comes AFTER the first lesson, to "save your progress". [Taplytics](https://taplytics.com/blog/duolingo-ab-test-onboarding) | A real lesson before signup. | The reported lift (about 20% DAU) is second-hand, and we did not verify it. [Taplytics](https://taplytics.com/blog/duolingo-ab-test-onboarding) |
| **Granola** (meeting notes) | Sign in with Google. [Granola docs](https://docs.granola.ai/help-center/getting-started/setting-up-granola-for-the-first-time.md) | Calendar read at once, then system audio. It says why: to start and stop at the right time. [Granola docs](https://www.granola.ai/docs/docs/101/gettingstarted/calendarintegrations) | A sample meeting waits for you, and real notes arrive when your next meeting ends. [Granola docs](https://docs.granola.ai/help-center/getting-started) | Outlook users meet an admin approval wall. It can pick the wrong calendar. [Granola docs](https://www.granola.ai/docs/docs/101/gettingstarted/calendarintegrations) |
| **Lovable** (AI app builder) | One prompt on the home page: "what do you want to build?" [Lovable docs](https://docs.lovable.dev/introduction/getting-started.md) | Signup comes when you submit, and the prompt survives it. Role and team size come after. [Supademo](https://supademo.com/user-flow-examples/lovable) | A working app in about 2 to 3 minutes. [Supademo](https://supademo.com/content/plg-onboarding-gallery/lovable) | A free cap of a few prompts a day stops the first session early. [EuroSaaS Edge](https://eurosaasedge.substack.com/p/a-lovable-onboarding) |
| **Cleo** (money chat) | The bank connection, before anything else. [Penny Hoarder](https://www.thepennyhoarder.com/save-money/cleo-app-review/) | At once, and it is mandatory, through Plaid, read-only. [FinanceBuzz](https://insurance.financebuzz.com/cleo-review) | A chat with the AI coach about your money. [Penny Hoarder](https://www.thepennyhoarder.com/save-money/cleo-app-review/) | No manual path. The chat step is the slowest part. Onboarding copy became a compliance problem (FTC, 2025). [Sacra](https://sacra.com/chat/h/2b18d8ec-2eaf-417e-bd23-f362872bce1e) |
| **Pi** (voice companion) | Sign in, then pick a voice. [AI Apps](https://www.aiapps.com/blog/say-hello-to-pi-your-new-ai-friend-everyones-talking-about) | No connection. The microphone only. [I] | The conversation itself, hands-free with "Call Pi". [Product Hunt](https://www.producthunt.com/p/pi-by-inflection-ai/call-pi) | No task value. It is warm but does nothing for a business. [TechRadar](https://www.techradar.com/computing/artificial-intelligence/pi-is-a-friendlier-cosier-alternative-to-chatgpt-whether-thats-a-good-thing-depends-on-you) [I] |
| **Typeform** (one-question flows) | One question per screen. [Typeform](https://help.typeform.com/hc/en-us/articles/360029615911) | Not applicable. | A progress feel, one answer at a time. | 47% to 59% completion, self-reported. No controlled test shows that the form alone causes it. [Typeform](https://typeform.com/help/whats-the-average-completion-rate-of-a-typeform), [Fillout](https://fillout.com/blog/one-question-at-a-time-form) |
| **Tally + WhatsApp, Khatabook** (India SMB) | A phone number and OTP. [I] | A WhatsApp Business link needs documents. Tally advises the GST certificate for Meta approval. [Tally help](https://help.tallysolutions.com/tally-prime/welcome-to-tally-prime/onboarding-to-whatsapp-through-interakt/) | Invoices and payment links sent in WhatsApp, where the buyer already is. [Tally guide](https://tallysolutions.com/business-guides/whatsapp-invoicing-payments-india-2026/) | Khatabook moves fast through 13 local languages, and its education keeps users. [CleverTap](https://clevertap.com/blog/how-khatabook-uses-education-and-games-to-fuel-growth/). Paperwork for the WhatsApp channel slows setup. [I] |

## Patterns that work

1. **One open question first, and no form.** Boardy asks what you work on.
   Lovable asks what you want to build. The answer starts the work at once.
2. **The account comes after the value.** Duolingo gives a lesson first.
   Lovable keeps the prompt through signup.
3. **Ask for a permission at the moment it pays, and say why.** Granola says
   it reads the calendar so it can start and stop on time.
4. **Never show a blank page.** Notion fills the page from one answer. Granola
   puts a sample meeting in the list.
5. **Live in the channel the user already trusts.** Boardy uses phone and
   email. Tally sends the bill in WhatsApp.
6. **A person for the first meeting, then self-serve.** Superhuman learned
   from the calls, and then built the product path. [First Round](https://review.firstround.com/inside-superhumans-onboarding-strategy-from-human-led-to-self-serve/)
7. **Do the core act inside the tour.** Linear makes you make an issue.

## Patterns that fail

1. **A mandatory connection before any value.** Cleo asks for the bank first,
   with no manual path. A careful owner stops there. [I]
2. **Slow first value.** Boardy takes hours to a match. A busy owner forgets. [I]
3. **Vague answers in, weak results out.** Boardy says so. The AI must ask a
   sharp follow-up, not accept "fabric".
4. **A personal voice that oversteps.** The Boardy email incident. Warm, but
   never personal about the user.
5. **An admin wall on a permission.** Granola on Outlook. Show a "later" path.
6. **Too much at once.** Full templates overwhelm Notion users.
7. **A human step that does not scale.** Superhuman. It is good for a first
   customer, and it must have an AI path beside it.

## What each of our six versions takes from this

| Version | It takes | It avoids |
|---|---|---|
| **v1-chat** | Boardy and Lovable: one open question, and the answer starts the work. Granola: each connection says why. | Cleo's mandatory bank step. Every card has "Later". |
| **v2-whatsapp** | Boardy and Tally: stay in the channel the buyer trusts. No app, no login. | The WhatsApp Business paperwork. The demo uses a link sheet, not a channel setup. [I] |
| **v3-voice** | Boardy's first call and Pi's hands-free talk. The transcript carries the facts. | Boardy's slow value. The brief arrives before the call ends, not 17 hours later. |
| **v4-canvas** | Notion's "never blank": the profile fills while you answer. Linear's core act inside the tour (correct a fact). | Notion's overload. The canvas grows one fact at a time. |
| **v5-guided** | Superhuman's 1:1 first meeting, with the AI as the scribe beside the human. | Superhuman's scale limit. Without Meera, Aroh runs alone. |
| **v6-value-first** | Duolingo and Lovable: value before the account. Granola's sample meeting becomes a sample brief. | Cleo's connection-first wall. Each locked line names the source that fills it. |
