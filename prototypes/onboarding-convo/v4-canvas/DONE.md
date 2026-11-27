# v4-canvas: done

Open `index.html` from the gallery, or go to `http://127.0.0.1:4790/v4-canvas/`.

## What it does

The page has two panes. The conversation is on the left. On the right, a profile of the business grows as Aroh learns. The profile has six cards: the company, the people, the tools, the money owed, the open decisions, and how you work.

- When you answer, a small pill moves from your message to its place on the canvas. Then the fact glows once. The chat shows a receipt ("Added to your profile: business · city · what you sell"), and the full fact lives on the canvas only.
- Each fact shows its source: "from you", "from Gmail", "from Tally", "from WhatsApp".
- When you click a fact, an editor opens. Save changes the fact to "fixed by you", and the canvas keeps the old value as struck-through text. The chat logs the change, and Aroh acknowledges it. The company name also changes the title and the hub of the tools graph.
- Tools are nodes around a hub. A node lights up and its wire becomes solid when you connect it. "Later" marks it, with no penalty.
- In the payoff, the canvas changes to the dawn colours and shows a ribbon. The brief card in the chat shows three decisions, each with its source.
- On a phone, the canvas is a sheet that peeks from the bottom. The peek shows the number of facts and the newest fact, and it moves a little when a fact lands. Tap the peek to open the sheet.

## The idea it tests

The buyer trusts the system because he watches its knowledge grow, and he can fix it.

## Paths I played (Playwright, 1440×900 and 390×844)

1. Chips, with every connection made, a correction of People (42 to 45), and "With the detail". The brief has three decisions from Tally, WhatsApp and Gmail.
2. Free text only: "I'm Sunil, I run a steel shop in Pune with 12 staff, we sell TMT bars". Aroh asks for the business name, then gets "Later" or "skip" for each connection. The brief falls back to three decisions from the chat. The title reads "Patel Steels", and Sunil is the owner.
3. Phone: chips, Gmail and Zoho connected, WhatsApp later, "Short". Then the sheet opens.

No path showed a console error. The captures are in `shots/`: `desktop.png`, `mobile.png`, `mobile-canvas.png` and `desktop-correct.png`.

## What is weak

- The free-text parse uses regular expressions. It finds a name, a city, a headcount and "what you sell" in common phrasings, and it asks when it finds no business name. An odd sentence can give a clumsy "What you sell" value. The buyer can fix it on the canvas, and that is part of the idea.
- At 1440 wide, the People card is narrow, so a long name wraps to two lines.
- The canvas text says "click" on a phone too.
- The brief always says "Good morning" and uses today's date.
- A correction does not change the brief text that the chat has already shown.

## Demo it in 2 minutes

1. Click the first chip ("We're Kaveri Textiles…"). Point at the pills that fly to the company card.
2. Click "Rajesh. 42 people." Then click **People** on the canvas, type 45 and save. Show the struck-through "was 42" and the reply from Aroh.
3. Click "Both, honestly", then **Connect Gmail** and **Allow**. The Gmail node lights, and three people and three decisions land.
4. Click **Connect Tally Prime** and **Allow**. Show ₹18,40,000 and the bars.
5. Click **Pick groups** and **Watch these groups**, then **With the detail**. The canvas changes to dawn, and the brief shows three decisions with their sources.
6. On a phone, do the same and tap the peek sheet at the end.
