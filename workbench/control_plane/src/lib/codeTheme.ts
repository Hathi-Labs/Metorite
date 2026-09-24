/**
 * The code-block colours for `react-syntax-highlighter` — tokens only
 * (WS-27bm S8 fix round 4).
 *
 * The chat used the library's `vscDarkPlus`, a dark palette of hex values.
 * In light mode its pale comments and strings sat on a light card and could
 * not be read. This style has one look in both modes because every colour is
 * a CSS variable: text and punctuation are `--foreground` and
 * `--muted-foreground`, and each token kind takes a categorical slot
 * (`--cat-*`). The contrast gate (`src/lib/theme/contrast.test.ts`) holds
 * every `--cat-*` slot to AA body text on the card and the page in both
 * modes, which is why the slots are safe as text colours here.
 *
 * `codeTheme.test.ts` fails on any colour that is not a `var(--…)`.
 */
import type { CSSProperties } from "react";

const base: CSSProperties = {
  color: "var(--foreground)",
  background: "transparent",
  textShadow: "none",
  direction: "ltr",
  textAlign: "left",
  whiteSpace: "pre",
  wordSpacing: "normal",
  wordBreak: "normal",
  tabSize: 2,
  hyphens: "none",
};

const muted = { color: "var(--muted-foreground)" };
const slot = (n: number) => ({ color: `var(--cat-${n})` });

export const CODE_THEME: Record<string, CSSProperties> = {
  'code[class*="language-"]': base,
  'pre[class*="language-"]': { ...base, overflow: "auto" },
  comment: { ...muted, fontStyle: "italic" },
  prolog: { ...muted, fontStyle: "italic" },
  doctype: { ...muted, fontStyle: "italic" },
  cdata: { ...muted, fontStyle: "italic" },
  punctuation: muted,
  operator: muted,
  keyword: slot(1),
  atrule: slot(1),
  important: { ...slot(1), fontWeight: "bold" },
  boolean: slot(1),
  string: slot(3),
  char: slot(3),
  "attr-value": slot(3),
  inserted: slot(3),
  regex: slot(3),
  number: slot(2),
  constant: slot(2),
  symbol: slot(2),
  function: slot(4),
  "class-name": slot(4),
  builtin: slot(4),
  tag: slot(5),
  selector: slot(5),
  property: slot(6),
  "attr-name": slot(6),
  deleted: slot(5),
  url: slot(6),
  entity: slot(6),
  variable: { color: "var(--foreground)" },
  bold: { fontWeight: "bold" },
  italic: { fontStyle: "italic" },
};
