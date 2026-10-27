// Test helper: build the tree of `xml.ts` by hand, since vitest runs without a DOM.
import type { XEl } from "./xml";

export function el(name: string, attrs: Record<string, string> = {}, ...kids: Array<XEl | string>): XEl {
  return { name, attrs, kids };
}

/** `<a:p><a:r><a:t>text</a:t></a:r></a:p>` */
export function para(text: string, lvl?: number): XEl {
  return el("p", {}, ...(lvl ? [el("pPr", { lvl: String(lvl) })] : []), el("r", {}, el("t", {}, text)));
}

/** A shape; `ph` is the placeholder type, or `null` for a plain text box. `""` is a placeholder with no type. */
export function sp(ph: string | null, ...paras: XEl[]): XEl {
  const nv = ph === null ? [] : [el("nvSpPr", {}, el("nvPr", {}, el("ph", ph ? { type: ph } : {})))];
  return el("sp", {}, ...nv, el("txBody", {}, ...paras));
}
