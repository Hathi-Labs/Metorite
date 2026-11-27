# v5-guided: research notes

The form under test is a person-led first meeting, with an AI beside them that listens, takes notes and does the setup.

## What I looked at

1. **Fireflies LiveAssist.** A side panel shows a live transcript with speaker names and AI notes during the call. Viewers of a shared panel must log in.
   https://gotranscript.com/public/fireflies-liveassist-real-time-ai-help-in-meetings
2. **Granola and Otter, compared.** Granola lets the person write the notes and the AI improves them. It shows no live transcript. Otter shows the full transcript live, and its bot joins the call. Reviewers say the bot that joins makes some people careful.
   https://fabric.so/comparison/otter-vs-granola
   https://zackproser.com/blog/granola-vs-otter-ai
3. **Rocketlane on the forward deployed engineer.** The FDE works inside the customer's setup and makes the product run there. The AI takes the work after the meeting.
   https://rocketlane.com/blogs/introducing-ai-fills-your-ai-teammate-for-all-your-post-meeting-work

## What works and what fails

- Works: notes that appear while people talk. Each person sees that the work happens now.
- Works (Granola): the human leads, and the AI supports. People trust the human.
- Fails (Otter): a full transcript is noise, and a silent bot feels like surveillance.
- Fails: notes that nobody can correct. One wrong fact poisons the rest.

## The decisions the research changed

1. **Facts, not a transcript.** The notes strip shows short facts only, and each one names the words it came from.
2. **Rajesh can correct every note.** Each note has an edit and a remove control. Before the research, the strip was read-only.
3. **Aroh announces itself.** Its first line in the meeting says what it captures and that it never sends.
