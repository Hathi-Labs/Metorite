# Point, do not click: evidence for an assistant that shows how

**Bottom line.** The evidence supports short guidance that the member starts. It does not support a whole-interface tour that starts by itself. Tours that a user starts complete about three times as often as tours that start alone. Completion falls fast after about five steps. The closest product to the owner's ask is Microsoft's Copilot "Highlights", which points at the button and lets the person click.

**The grade of this record.** A research agent ran about 10 searches on 2026-11-02 and read search snippets only. It fetched no page. Treat each fact as a lead to check, not as a source of record. Almost all of it is vendor data.

## Assistants that point or act

- **Copilot Vision "Highlights" (vendor).** The person shares one window and asks "show me how". Copilot highlights where to click. It does not click, type or scroll for the person. A switch in settings turns it off ([Windows blog](https://blogs.windows.com/windowsexperience/2025/06/12/copilot-vision-on-windows-with-highlights-now-available-in-us/), [PCWorld](https://www.pcworld.com/article/2657888/copilot-vision-for-windows-can-walk-you-through-tech-issues-step-by-step.html)).
- **Command AI Copilot, now part of Amplitude (vendor).** It answers from the docs, does actions, and can walk a person through a workflow. Amplitude also sells "annoyance monitoring" for guides. The search found no stated safety limits ([Amplitude](https://amplitude.com/blog/amplitude-acquires-command-ai), [TechCrunch](https://techcrunch.com/2024/10/15/amplitude-buys-command-ai-to-bolster-its-app-engagement-offerings)).
- **Pendo, WalkMe and Whatfix.** The search found only marketing pages, and no safety documentation ([Pendo](https://cfotech.co.uk/story/pendo-unveils-agent-analytics-to-optimise-ai-agents)).
- **Not searched.** Intercom Fin, Gemini, Adobe, Notion AI, Salesforce Agentforce and ServiceNow.

## Tours

- **Chameleon 2025 (vendor, large data set).** A tour that starts on a click completes at 67%. A tour that starts after a delay completes at 31%. Completion falls after about five steps ([Chameleon](https://chameleon.io/benchmark-report)).
- **Chameleon 2024 (vendor).** The average completion is 33.5%, and a tour that the user launches completes at 64% ([Chameleon 2024](https://www.chameleon.io/benchmark-report-24)).
- **Produktly 2026 (independent, 256 tours).** The median is 69% for a tour that the user starts, and 23% for one that starts alone. Tours of one or two steps complete at 73%. Tours of nine steps or more complete at 8% ([Produktly](https://produktly.com/research/saas-onboarding-benchmarks-2026)).
- **NN/g (measured, 70 users, four mobile apps).** People who read a tutorial succeeded at 91% of tasks, and people who skipped it at 94%. Readers rated the tasks harder. NN/g says that coach marks make an app look complex, so keep them short ([NN/g](https://www.nngroup.com/articles/mobile-tutorials/)).
- **The gap.** Completion is not activation. The search found no study that links a first-run tour of a whole interface to activation or retention.

## Sample data

- **Pipedrive (vendor docs).** Sample deals, people, organizations and activities carry a "[Sample]" prefix. Three places remove them. Some language versions say that only an admin can remove them ([Pipedrive](https://support.pipedrive.com/en/article/sample-data)).
- **Stripe (vendor docs).** A sandbox is isolated, and objects do not cross between modes. The older test mode can still change live settings ([Stripe](https://docs.stripe.com/test-mode)).
- **HubSpot, Monday, Asana, ClickUp and Trailhead.** Nothing found.
- **A failure that points at the risk.** A HubSpot user's bulk upload started a workflow that emailed about 300 real contacts ([HubSpot Community](https://community.hubspot.com/t5/Lists-Lead-Scoring-Workflows/Notification-to-let-me-know-if-any-workflows-will-trigger-when/m-p/404224)). It was not sample data. It shows that an automation fires on any new record, so a practice record needs an explicit exclusion.

## Libraries

- **React Joyride (vendor docs).** It follows WAI-ARIA, keeps focus in its dialog, returns focus on close, and works from the keyboard ([React Joyride](https://docs.react-joyride.com/accessibility)).
- **Shepherd.js.** One review says that its dialog lacks `aria-labelledby` and `aria-describedby` ([review](https://sandroroth.com/blog/evaluating-tour-libraries/)).
- **driver.js and Intro.js.** Nothing found on accessibility.
- **Note for Metorite.** A tour dialog that keeps focus does not suit a guide that asks the member to press a button on the page. The guide's ring must leave focus on the page (`specs/assistant_guide.md` §5.3).

## What follows for Metorite

Offer the tour, keep it to five stops, and let the chat answer questions at every stop. Point at the button and let the member press it. Give each practice record a visible mark and an explicit exclusion from automations, mail, notifications and reports. Measure whether a member does the real act after a guide, because no public study shows that a tour moves activation.
