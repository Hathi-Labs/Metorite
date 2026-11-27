# v2-whatsapp: research notes

Time spent: about 6 minutes, 2026-11-27.

## What I looked at

1. **Tally Prime, the WhatsApp onboarding demo.** Tally puts its own product tour in a
   WhatsApp thread for Indian SMB owners. The buyer meets the product in the channel
   that he already uses every day. Source: https://tallysolutions.com/tally-prime-demo/whatsapp-onboarding-demo/
2. **Meta, the WhatsApp Flows best practices.** Flows are forms that open as a sheet
   inside the thread. Meta says: one task on each screen, finish in about 5 minutes,
   and add a login screen only when you must. Source: https://developers.facebook.com/documentation/business-messaging/whatsapp/flows/guides/bestpractices
3. **8x8 and Gallabox, Flow guides.** Keep a flow to 3 to 5 screens. Ask only for the
   data that the next step needs. Long flows lose people. Sources:
   https://developer.8x8.com/connect/docs/whatsapp/whatsapp-flows-best-practices and
   https://docs.gallabox.com/whatsapp-flows/best-practices-for-whatsapp-forms
4. **"No-download onboarding removes one funnel step, not every friction."** An app
   with no download is a test of one idea, not a full experience. The value must still
   arrive fast. Source: https://ecommercefastlane.com/no-download-onboarding-friction/

## What works and what fails

- Works: a reply button under a message. The owner taps it and does not type.
- Works: a sheet inside the thread. The owner keeps the thread in view behind it.
- Fails: a link that opens a browser and a login page. The owner leaves the channel
  and often does not come back.
- Fails: a long form. Each extra screen loses people.

## The decision that the research changed

I first planned each connection as a link that opens a web page. **I changed it.**
Each connection is now a link message that opens a **sheet inside the thread**, with
**one task per sheet** and **no login screen** (the account is already known). The
WhatsApp groups sheet suggests four groups for the owner, so he taps once and does not
pick eleven boxes. Every sheet has "Not now", and Aroh treats it as a full answer.
