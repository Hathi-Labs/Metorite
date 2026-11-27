# v5-guided: done

## What this version does

The first meeting has three parties: Rajesh (the buyer), Meera from the Aroh team (fictional, a forward deployed engineer) and Aroh (the AI).

1. Aroh asks one question first: "Is someone from the Aroh team with you right now?"
2. **Yes:** Meera joins the room. She asks the human questions. Aroh listens and writes each fact into the notes panel, with the words it came from. Meera hands each connection to Aroh ("Meera → Aroh"). Aroh proposes the connection, runs the mock consent, shows the first insight, and hands back. At the end, Aroh shows the brief and Meera closes.
3. **No:** Aroh runs alone. It offers a call with Meera first (three time slots, or start now). A booked call stays in the notes, and the last line names it.
4. Both branches cover the whole arc: the open question, size, pain, Gmail, Tally or Zoho, WhatsApp (optional), style, and the first brief in the dawn colours.

The notes panel sits on the right at desktop size, and as a strip that scrolls sideways at the top on a phone. Each note has a fix and a remove control.

## The one idea it tests

A person leads the first meeting, and the AI does the setup beside them. Trust comes from Meera. Speed and proof come from Aroh.

## What is weak

- Meera's lines are scripted, so the demo cannot show a real person going off script.
- The hand-offs follow the script. The presenter cannot hand a step over by hand.
- Name and business parsing is simple pattern matching. Odd input falls back to Kaveri Textiles.
- The header label wraps to two lines on a phone in the three-party room.

## How to demo it in 2 minutes

1. Click **Yes, Meera is here**. Point at the room avatars and Meera's "Aroh team" label.
2. Click the first chip for each of the three questions. Point at the notes as they appear. Click the pencil on one note and fix it.
3. **Connect Gmail**, then **Allow**. Show the dawn insight. Point at "Meera → Aroh" and "Aroh → Meera".
4. **Tally Prime**, **Connect Tally**, **It's paired**. Then **Later** for WhatsApp, and **Short**.
5. The brief appears. Then click **Restart** and pick **No, just me** to show the booking card.

You can type free text in any input. Try "I am Anil, Sagar Silks from Bangalore."

## Files

`index.html`, `style.css`, `app.js`, `notes.md`. Captures: `shots/desktop.png`, `shots/mobile.png`, and the brief in `shots/desktop-brief.png` and `shots/mobile-brief.png`.
