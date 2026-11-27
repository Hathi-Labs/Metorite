# v3-voice: done

## What it does

Aroh calls the buyer. The page opens with an incoming call card. "Answer"
opens a calm call screen:

- A sage orb breathes at rest and pulses on each word when Aroh speaks.
  A label under it gives the state: speaking, listening, reading your inbox.
- A live transcript writes each line of Aroh word by word.
- A rail, "What Aroh heard", gets a note for each fact: Business, City, Sells,
  Team, Stuck at, Email, Overdue, WhatsApp, Style. On a phone the rail is a
  strip of pills under the call bar.
- The buyer answers with a suggested spoken reply (a chip) or types. An answer
  during Aroh's line cuts in (barge-in): the voice stops, the line completes.
- Each connection is "sent to your phone": a card in the transcript, then a
  mock phone with a read-only consent sheet. Then a live count ("Reading
  invoices · 1,286") and a dawn insight card.
- The call ends with the first brief on the dawn sky: three decisions, each
  with its source and two actions.

The browser voice (`speechSynthesis`, an en-IN voice when there is one) reads
Aroh's lines. "Mute Aroh" and the check box on the call card turn it off. The
page never waits on the voice for more than 3.5 seconds. The transcript alone
carries the demo.

## The idea it tests

A five-minute call replaces the setup. The buyer only talks. Aroh writes the
setup down in front of them.

## Branches that I played

- Chips all the way, every source connected. Brief from Tally, Gmail, WhatsApp.
- Typed answers ("I run Ganga Silk Mills in Varanasi, we weave sarees"), then
  "Later" for every source. Notes use the typed words. The brief falls back to
  three decisions "From our call".
- "Not now" on the call card, then "Take the call now".
- "End call" at any point goes straight to a brief.
- Reduced motion: lines appear whole, the orb is still.

No console errors on any branch. Captures: `shots/desktop.png` (1440 by 900)
and `shots/mobile.png` (390 by 844).

## What is weak

- The voice is the browser voice. It sounds robotic on most machines, and the
  word timing is a fixed pace, not synced to the audio. A real build needs a
  real voice.
- The buyer does not speak. Real voice input would test the idea better.
- Only one consent per connection. "Both" books connects Tally and promises
  Zoho later.
- The parser of typed words is a few regular expressions. A lowercase business
  name falls back to "your company".
- On a phone the orb and the transcript compete for height. The rail pills
  scroll sideways, so early notes go out of view.

## Demo in 2 minutes

1. Open `v3-voice/index.html`. Turn on the sound. Click the green button.
2. Click the first chip each time. When the phone sheet opens, click "Allow".
3. Point at the rail as each note lands, and at the two yellow insight cards.
4. For WhatsApp, click "Later" once to show that it costs nothing.
5. Click "Short, detail if I ask". The brief opens on its own.
6. Click an action on decision 1 to show that the brief is live.

"Restart" (top right) resets the call. "End call" skips to the brief.
