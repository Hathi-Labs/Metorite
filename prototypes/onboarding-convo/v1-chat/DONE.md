# v1-chat: done

## What it does

One full-screen conversation, and nothing else. The page opens on a dawn sky
with one line from Aroh and one input. Each setup step is a turn in the same
thread:

1. Aroh asks about the business. It reads the name, the business, the city
   and what you sell from your free text, and reflects them back.
2. Three short follow-ups: people, buyers, where things get stuck.
3. Gmail arrives as a card. The consent opens inside the same card ("Aroh
   will" and "Aroh will never"). A read counter runs, and the first insight
   arrives as a dawn message.
4. The books card offers Tally Prime or Zoho. Each has its own consent. The
   insight shows ₹18,40,000 overdue, with three buyer rows.
5. WhatsApp is a card with eleven groups. Aroh ticks three work groups, and leaves
   the two private groups unticked. "Later" has the same weight as "Connect".
6. One style question: short, or with the detail.
7. Aroh shows its work in four steps, and the sky reaches full dawn. The
   first brief unfolds in the thread: three decisions, each with its source
   and two actions. A click on an action resolves it in place.

The sky brightens a little after each connection. That is the only sign of
progress. The page has no progress bar.

## The one idea it tests

The chat alone can carry the whole setup: consent, connection, insight and
the payoff, with no second surface.

## Branches played through

- Connect everything (chips and card buttons), at 1440 by 900 and 390 by 844.
- Skip everything. The brief says it is a sample, and its foot says that
  nothing of yours was read.
- Free text only, with a different owner ("I am Anita, I own Lotus Prints in
  Jaipur…"). Typed words such as "ok connect", "nahi" and "keep it short"
  move each step.

No branch has a console error.

## What is weak

- Each card click also echoes a reply bubble below the card ("Connect Gmail",
  "Allow"). It keeps the thread honest, but it adds two bubbles per
  connection.
- The script holds one set of brief data, for Kaveri Textiles. Another business name still
  gets Nandini Apparels and the dye order.
- On a phone, a long thread scrolls the earlier cards away. The owner cannot
  see all the connections at once. This is the cost of the pure chat form.
- The free-text reader is a set of regexes, not a model. Unusual phrasing
  falls back to safe defaults.

## Demo in 2 minutes

1. Open `v1-chat/index.html`. Point at the single line and the single input.
2. Click the suggestion chip. Click `42`, `About 300`, `Both`.
3. Click `Connect Gmail`, then `Allow, read only`. Wait for the insight.
4. Click `Tally Prime`, then `Allow`. Show the overdue rows.
5. Click `Connect 3 groups`. Click `With the detail`.
6. Watch the brief unfold. Click `Hold until paid` on the first decision.
7. For the "Later" story, click `Restart` (top right) and click `Later` at
   each card. The brief still arrives.
