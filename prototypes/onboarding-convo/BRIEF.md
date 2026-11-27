# Aroh onboarding demos: the shared brief

Task 4107. Branch `onb-convo-demos`. Directory `prototypes/onboarding-convo/`.

## The idea

Aroh is a conversation with your business. So the onboarding is that
conversation from the first second. No wizard, no checklist, no "click here".
Each setup step is a turn in the conversation. Reference: the Boardy onboarding.

The look leads to dawn: light, sage green, pastel yellow for a reward. The
buyer is an Indian business owner who lives in WhatsApp, Tally and email. Green
means trust to this person. Dark mode is not in these demos.

## The buyer in each demo (fictional)

- **Person:** Rajesh Mehta, owner. Direct, busy, types short answers.
- **Business:** Kaveri Textiles, Surat. Makes and sells fabric to garment
  makers. 42 people. About 300 active buyers.
- **Tools:** Gmail, Tally Prime, Zoho CRM, eleven WhatsApp groups.
- **Pain:** payments come late, and decisions wait in WhatsApp groups.

The assistant is **Aroh**. It speaks in the first person. Short lines. Warm,
plain, never cute. It says why it asks for each thing. It uses rupees and
Indian number groups (₹4,80,000).

## The arc that every version covers

Each version tells the same story, so people can compare the form and not the
content.

1. **One open question first.** "Tell me about your business." No form.
2. **Aroh reflects back** what it heard, then asks two or three follow-ups:
   what you sell, how many people, where decisions get stuck.
3. **Connect email, with the reason.** A mock consent step, then a first
   insight at once. Example: "I read 214 threads from the last 30 days. Three
   wait for your decision."
4. **Connect the books** (Tally or Zoho). Insight: "₹18,40,000 is overdue from
   7 buyers. Two of them also have open orders."
5. **WhatsApp groups.** Optional. "Later" is a full answer and has no penalty.
6. **One question about style or ambition.** Example: "When something needs
   you, do you want it short or with the detail?"
7. **The payoff: your first brief.** Three decisions that need the owner today,
   each with its source. This is the dawn moment. Use the dawn colours here.

## Rules for each demo

- **Static files only.** One `index.html` in your directory, with your own CSS
  and JS files beside it if you want. Vanilla HTML, CSS and JS. No build step,
  no framework, no package install, no backend, no model call.
- **Link `../shared/tokens.css`** and use its tokens for every colour, font,
  radius and shadow. Do not edit that file. Add no second palette.
- **Scripted but alive.** Write the conversation as data. Show typing
  indicators with real pauses. Accept free text in every input, and use the
  typed words where you can (the business name, the person's name). Give quick
  reply chips too, so a presenter can click through in two minutes.
- **No dead end.** Every branch reaches the first brief. "Skip" and "Later"
  always work.
- **Mock every connection.** A consent sheet that looks plausible, then a
  "connected" state. Never open a real OAuth page. Never ask for a real secret.
- **A restart control** and a small "Demo" tag, both quiet, in a corner.
- **A link back to `../index.html`** (the gallery).
- **Works at 1440 by 900 and at 390 by 844.** The owner demos on a laptop and
  on a phone.
- **Respect `prefers-reduced-motion`.** Visible keyboard focus. Text contrast
  of 4.5 to 1 or better.
- **Craft counts.** This is shown to a customer's owner and to the founder.
  Typography, spacing and motion must feel like a finished consumer product.
  The display font is for a few large lines only.
- **Use fictional people and companies only.** No real customer name. Do not
  copy Boardy's text, name or look.

## Research inside your lane

Before you build, spend at most 8 minutes on web research for your form. Find
two or three real products that onboard this way, and note what works and what
fails. Write it in `notes.md` in your directory: 10 to 20 lines, with the
source links. Then build. The research must change at least one decision, and
`notes.md` says which.

## How you work

- Work only inside your own directory under
  `/home/dev/wt-onb-convo-demos/prototypes/onboarding-convo/`.
- **Do not run git commands that write.** No add, no commit, no push. The
  orchestrator commits all versions at the end.
- **Do not touch the Metorite board.** The orchestrator holds task 4107.
- **Keep the box calm.** The CPU is shared by 14 lanes. Run no test suite, no
  `npm install`, no Docker, no dev stack.
- **Check your own page.** A preview server runs at
  `http://127.0.0.1:4790/` and serves the `onboarding-convo` directory. Take
  screenshots at both sizes with Playwright if `npx playwright --version`
  works in `~/Metorite/workbench/control_plane`. Run it from there, one browser
  at a time. Look at the captures and fix what you see. Save the final captures
  as `shots/desktop.png` and `shots/mobile.png` in your directory. Play the
  whole conversation once through each branch.
- **Deadline: 35 minutes from your start.** A complete, polished flow matters
  more than extra branches.
- **When you finish,** write `DONE.md` in your directory: what the version
  does, the one idea it tests, what is weak, and how to demo it in 2 minutes.
