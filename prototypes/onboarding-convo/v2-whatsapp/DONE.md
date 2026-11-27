# v2-whatsapp: done

## What this version does

The onboarding is a messenger thread. Aroh writes first. On a laptop, a phone frame
sits in the centre. The left column names the idea and marks the 7 steps of the arc.
The right column shows "What Aroh knows", and it grows with each answer. On a phone,
the thread fills the screen, and the header carries the Demo tag and Restart.

- Every step is a message with reply buttons under it. Free text works in every step.
  The business name, the city, the owner's name and a time ("7am") come from the
  typed words.
- Each connection is a **link message** (a preview card). The card or the button opens
  a **sheet inside the thread**, with one task on each sheet. The consent is mock and
  read-only, then the sheet shows "Connected", and the card shows a green tick.
- Aroh asks about the WhatsApp groups in a **voice note** (play, waveform, transcript).
  The groups sheet suggests 4 of 11 groups. The other 7 stay folded.
- The payoff is a **PDF document bubble**. The thread warms to the dawn sky. The PDF
  opens a full-screen viewer: "Good morning, Rajesh." and three decisions, each with
  its source and two actions.
- Read ticks: one grey tick, then two, then two sage ticks. The header shows
  "typing…" and "recording audio…".
- No dead end. "Later" and "Not now" always work. The brief picks three decisions from
  what is connected. With nothing connected, it uses the chat answers.

## The one idea it tests

The buyer never leaves the channel he trusts. No app, no browser tab, no login page.

## What is weak

- The brief with nothing connected is thin: three decisions "From our chat".
- The voice note is silent. It animates and shows the transcript, but it plays no audio.
- The "Show my other 7 groups" fold, and the doc viewer, cover the status bar in the
  desktop phone frame.
- A real WhatsApp Business account cannot show a custom sheet like this one. WhatsApp
  Flows come close, but a production build needs a Flows check first.
- Free-text parsing is a few regexes. An odd sentence can give an empty business name,
  and then Aroh says "Got it" without a name.

## Demo it in 2 minutes

1. Open `v2-whatsapp/index.html` at 1440 by 900. Wait for three messages from Aroh.
2. Tap the Kaveri reply, then "Rajesh", "20 to 50", "Both, honestly".
3. Tap "Connect Gmail", then "Allow read-only". Show the 214-threads insight.
4. Tap "Tally Prime", then "Connect Tally Prime", then "Allow read-only". Show
   ₹18,40,000.
5. Tap play on the voice note. Tap "Choose groups", then "Add 4 groups".
6. Tap "With the detail". The thread turns to dawn. Tap the PDF card.
7. Tap "Hold dispatch" on decision 2. Go back, and tap "8:30 works".

To show "no dead end", press Restart and tap Skip and Later at each step. To show free
text, type "We are Ganga Silk Mills in Varanasi" as the first answer.

Files: `index.html`, `app.css`, `app.js`, `notes.md`, `shots/`.
