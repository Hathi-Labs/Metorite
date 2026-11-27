# v4-canvas: research notes

Time spent: about 6 minutes, two web searches.

## What I found

- **ChatGPT Canvas and Claude artifacts** put the work in a side panel beside the chat. The chat holds the talk, and the panel holds the result. Source: https://venturebeat.com/ai/openai-launches-chatgpt-canvas-challenging-claude-artifacts
- **Pattern libraries** list two failures of chat plus canvas. The same content shows in both panes with no source of truth, and the user cannot undo a bad edit by the agent. Source: https://aiuxplayground.com/pattern/chat-artifact
- The same libraries say that split view needs a mobile fallback, and that a short answer must not open the panel. Source: https://aiuxplayground.com/glossary/canvas
- **Notion** pairs its welcome survey with a live preview of the product that changes as you answer. Appcues says the preview makes the survey believable. Source: https://www.appcues.com/blog/best-user-onboarding-examples
- **Lovable** does the work on the first prompt and does not ask for a form first. Time to value is about two to three minutes. Source: https://www.candu.ai/blog/lovables-agentic-onboarding
- A Product Hunt review of a similar builder asks for a first preview that is nearly correct. A fast but wrong preview breaks trust. Source: https://www.producthunt.com/products/shuffle-assistant

## Decisions that the research changed

1. **The canvas is the one source of truth.** The chat does not repeat a fact in full. It shows a small receipt ("Added to profile: 42 people"), and the fact lives on the canvas.
2. **Every fact shows its source** ("from you", "from Gmail", "from Tally"), and a click corrects it. This answers the "nearly correct" problem: a wrong guess is cheap to fix.
3. **A correction keeps the old value** as a struck-through line, so the buyer can see what changed. This is the small form of undo.
4. **On a phone the canvas is a peek sheet**, not a squeezed second column. The peek shows the newest fact, so the growth stays visible.
5. **The canvas is visible from the first second** with empty, labelled slots. The buyer sees what Aroh will learn before he types.
