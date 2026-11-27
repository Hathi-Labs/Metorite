# v1-chat: research notes

Time spent: about 6 minutes. The form under test is pure chat.

## What works

- Most AI tools ask three to five short questions, and then show value at
  once. The quick win matters more than the full profile (UserGuiding).
- A mix of quick replies and free text works best. Engagement falls when
  buttons make up most of the replies (Dashbot data, VentureBeat).
- A 2024 study found that people preferred buttons to free text, because a
  free-text bot that misreads input feels less competent (IJHCI).
- Nielsen Norman Group: less chat, more answer. Give the result, not talk
  about the result.

## What fails

- A chat face on a rigid flow. Users see the script at once (chameleon.io).
- Quick replies that vanish when a new message arrives, or that make no
  sense away from the message they sit under (UX Collective).
- A hidden exit. When "skip" or "talk to a person" is hard to find, users
  leave (Clutch).

## Decisions that the research changed

1. **The chips sit only under the newest Aroh question, and two or three at
   most.** Old chips disappear, so the thread never shows stale buttons.
2. **Each insight is a compact data message (a number, three rows), not a
   paragraph.** Less chat, more answer.
3. **Free text never fails.** If the words do not match, Aroh says what it
   took them to mean and goes on. It never asks "I did not understand".
4. **"Later" has the same weight as "Connect"** on every card.

## Sources

- https://userguiding.com/blog/how-top-ai-tools-onboard-new-users
- https://venturebeat.com/business/too-many-buttons-spoil-the-bot/
- https://informahealthcare.com/doi/full/10.1080/10447318.2024.2351707
- https://nngroup.com/articles/less-chat-more-answer
- https://www.chameleon.io/blog/what-most-teams-get-wrong-about-onboarding-in-2025
- https://uxdesign.cc/honey-i-built-a-chatbot-but-no-one-will-click-on-my-buttons-b713290c0029
- https://clutch.co/resources/fix-your-chatbot-ux
