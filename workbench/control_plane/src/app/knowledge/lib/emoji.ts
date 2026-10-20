// ── Emoji search for the editor's `:` insert (WS-61 KB-2i, §21.7) ──────────
//
// Pure. A small list built in, so the `:` insert works before the emoji
// data of KB-2f lands. KB-2f ships the full data at this same path, with
// this same `searchEmoji` signature, and its file replaces this one.
//
// A match takes the start of a name first, then the start of a word in it,
// then any part of it. An empty query gives the first emoji of the list,
// the ones a page uses most.

export interface EmojiHit {
  char: string;
  name: string;
}

const EMOJI: readonly EmojiHit[] = [
  { char: "👍", name: "thumbs up" },
  { char: "✅", name: "check mark button" },
  { char: "⚠️", name: "warning" },
  { char: "❗", name: "exclamation mark" },
  { char: "❓", name: "question mark" },
  { char: "💡", name: "light bulb idea" },
  { char: "📌", name: "pushpin" },
  { char: "🔥", name: "fire" },
  { char: "🎉", name: "party popper tada" },
  { char: "🚀", name: "rocket" },
  { char: "⭐", name: "star" },
  { char: "❤️", name: "red heart" },
  { char: "😀", name: "grinning face" },
  { char: "😄", name: "smile grinning face with smiling eyes" },
  { char: "🙂", name: "slightly smiling face" },
  { char: "😉", name: "winking face" },
  { char: "😂", name: "face with tears of joy" },
  { char: "😍", name: "smiling face with heart eyes" },
  { char: "🤔", name: "thinking face" },
  { char: "😐", name: "neutral face" },
  { char: "😢", name: "crying face" },
  { char: "😮", name: "face with open mouth" },
  { char: "🙏", name: "folded hands thanks" },
  { char: "👏", name: "clapping hands" },
  { char: "👋", name: "waving hand" },
  { char: "👎", name: "thumbs down" },
  { char: "👀", name: "eyes" },
  { char: "💪", name: "flexed biceps" },
  { char: "✋", name: "raised hand" },
  { char: "❌", name: "cross mark" },
  { char: "⛔", name: "no entry" },
  { char: "🚫", name: "prohibited" },
  { char: "🔒", name: "locked" },
  { char: "🔑", name: "key" },
  { char: "📅", name: "calendar" },
  { char: "⏰", name: "alarm clock" },
  { char: "⏳", name: "hourglass" },
  { char: "📈", name: "chart increasing" },
  { char: "📉", name: "chart decreasing" },
  { char: "📊", name: "bar chart" },
  { char: "📝", name: "memo note" },
  { char: "📄", name: "page facing up document" },
  { char: "📁", name: "file folder" },
  { char: "📎", name: "paperclip" },
  { char: "🔗", name: "link" },
  { char: "📧", name: "e-mail" },
  { char: "📞", name: "telephone receiver phone" },
  { char: "💬", name: "speech balloon" },
  { char: "📣", name: "megaphone" },
  { char: "🔔", name: "bell" },
  { char: "🛠️", name: "hammer and wrench tools" },
  { char: "⚙️", name: "gear settings" },
  { char: "🧪", name: "test tube" },
  { char: "🐛", name: "bug" },
  { char: "🏁", name: "chequered flag finish" },
  { char: "🎯", name: "bullseye target" },
  { char: "🏆", name: "trophy" },
  { char: "💰", name: "money bag" },
  { char: "💳", name: "credit card" },
  { char: "🛒", name: "shopping cart" },
  { char: "📦", name: "package box" },
  { char: "🚚", name: "delivery truck" },
  { char: "🏭", name: "factory" },
  { char: "🏢", name: "office building" },
  { char: "🌍", name: "globe earth" },
  { char: "☀️", name: "sun" },
  { char: "🌙", name: "crescent moon" },
  { char: "☕", name: "hot beverage coffee" },
  { char: "🍕", name: "pizza" },
  { char: "🎂", name: "birthday cake" },
  { char: "🎁", name: "wrapped gift" },
  { char: "✨", name: "sparkles" },
  { char: "💯", name: "hundred points" },
  { char: "➡️", name: "right arrow" },
  { char: "⬅️", name: "left arrow" },
  { char: "⬆️", name: "up arrow" },
  { char: "⬇️", name: "down arrow" },
  { char: "🔴", name: "red circle" },
  { char: "🟡", name: "yellow circle" },
  { char: "🟢", name: "green circle" },
  { char: "🔵", name: "blue circle" },
  { char: "ℹ️", name: "information" },
];

/** How well `query` names an emoji: 0 is no match. */
function score(name: string, q: string): number {
  if (name.startsWith(q)) return 3;
  if (name.split(/[\s-]+/).some((w) => w.startsWith(q))) return 2;
  return name.includes(q) ? 1 : 0;
}

/**
 * The emoji whose name matches `query`, best first, and in list order on a
 * tie. Spaces, dashes and underscores in the query count as one space, so
 * `:thumbs_up` finds "thumbs up".
 */
export function searchEmoji(query: string, limit = 8): EmojiHit[] {
  const q = query.trim().toLowerCase().replace(/[\s_-]+/g, " ");
  if (!q) return EMOJI.slice(0, limit);
  return EMOJI.map((e, index) => ({ e, index, s: score(e.name, q) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || a.index - b.index)
    .slice(0, limit)
    .map((x) => x.e);
}
