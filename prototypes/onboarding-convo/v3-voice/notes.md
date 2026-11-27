# v3-voice: research notes

Time spent: about 6 minutes. Form under test: an onboarding phone call.

## What I found

- **Voice onboarding and activation calls exist, mostly as outbound calls.**
  JustCall, Bolna and OneAI sell agents that call a new or stalled user and
  walk them through setup. They confirm steps and hand edge cases to a person.
  The claims are vendor claims.
  https://justcall.io/product/ai-voice-agent/customer-onboarding/ ·
  https://www.bolna.ai/agents/onboarding-agent · https://oneai.com/activation
- **What works: a visible state.** A builder of a voice tutor found that a
  rolling live transcript and a clear "the agent speaks now" signal made users
  feel heard. Trust comes from visibility.
  https://dev.to/sholajegede/why-building-with-voice-is-a-ux-design-challenge-not-just-a-tech-one-3lh5
- **What fails: an agent that talks over you.** Picovoice says a user who
  cannot stop the agent feels out of control. Barge-in must be instant.
  https://picovoice.ai/guide/voice-agents/voice-ux-latency-turn-taking/
- **What fails: filler that hides a wait.** An acknowledgement helps only when
  it reports real progress ("I'm reading your inbox"), not a vague "one moment".
  https://dev.to/smallestai/voice-assistants-are-designed-in-the-silence-4f5m

## Decisions that the research changed

1. **Barge-in.** A click on a reply, or a typed answer, stops Aroh's voice and
   finishes its line on screen at once. Before the research, Aroh finished
   each line first.
2. **A state label beside the orb:** "Aroh is speaking", "Listening",
   "Reading your inbox". The orb alone did not say enough.
3. **Waits name the work.** While a mock connection runs, Aroh says what it
   reads, with a live count, and not a spinner.
