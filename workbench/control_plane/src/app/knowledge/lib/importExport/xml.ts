// ── XML of an Office file as a plain tree (WS-61 KB-AI-1) ────────────────────
//
// A `.pptx` and an `.xlsx` are zips of XML, and the file came from outside.
// `parseXml` reads the text with `DOMParser` as `application/xml`, in an inert
// document that is never attached to the page, and copies it into plain
// objects. The parsers and their tests work on those objects, so they need no
// DOM.
//
// ⚠️ The browser's XML parser does not expand external entities and does not
// fetch a DTD, so an XXE file reads as plain text there. A `<!DOCTYPE` is
// refused anyway, because no Office file written by an Office program has one.
// A parse error shows as a `<parsererror>` element, and that refuses the file.

import { MAX_XML_DEPTH } from "./limits";

/** One element. The name and the attribute names are local names, with no prefix. */
export interface XEl {
  name: string;
  attrs: Record<string, string>;
  kids: Array<XEl | string>;
}

export class XmlError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "XmlError";
  }
}

/** Parse XML text. Throws `XmlError` for a bad file. Browser only. */
export function parseXml(text: string): XEl {
  if (/<!DOCTYPE/i.test(text)) throw new XmlError("The file holds a DOCTYPE, which Office files do not have.");
  const doc = new DOMParser().parseFromString(text, "application/xml");
  const root = doc.documentElement;
  if (!root || root.localName === "parsererror" || doc.getElementsByTagName("parsererror").length) {
    throw new XmlError("The file holds XML that cannot be read.");
  }
  return fromDom(root);
}

/** Copy a DOM element into a tree, without recursion, so a deep file cannot overflow the stack. */
function fromDom(root: Element): XEl {
  const copy = (el: Element): XEl => {
    const attrs: Record<string, string> = {};
    for (const a of Array.from(el.attributes)) attrs[a.localName] = a.value;
    return { name: el.localName, attrs, kids: [] };
  };
  const top = copy(root);
  const stack: Array<{ dom: Element; out: XEl; depth: number }> = [{ dom: root, out: top, depth: 1 }];
  while (stack.length) {
    const { dom, out, depth } = stack.pop() as { dom: Element; out: XEl; depth: number };
    if (depth > MAX_XML_DEPTH) throw new XmlError("The XML is nested too deep.");
    const pending: Array<{ dom: Element; out: XEl; depth: number }> = [];
    for (const node of Array.from(dom.childNodes)) {
      if (node.nodeType === 1) {
        const child = copy(node as Element);
        out.kids.push(child);
        pending.push({ dom: node as Element, out: child, depth: depth + 1 });
      } else if (node.nodeType === 3 || node.nodeType === 4) {
        out.kids.push(node.nodeValue ?? "");
      }
    }
    stack.push(...pending.reverse());
  }
  return top;
}

/** The child elements of `el`, optionally only those named `name`. */
export function kids(el: XEl, name?: string): XEl[] {
  return el.kids.filter((k): k is XEl => typeof k !== "string" && (name === undefined || k.name === name));
}

/** The first child named `name`. */
export function kid(el: XEl, name: string): XEl | undefined {
  return kids(el, name)[0];
}

/** Every descendant named `name`, in document order. `el` itself is not included. */
export function all(el: XEl, name: string): XEl[] {
  const out: XEl[] = [];
  const walk = (node: XEl) => {
    for (const k of node.kids) {
      if (typeof k === "string") continue;
      if (k.name === name) out.push(k);
      walk(k);
    }
  };
  walk(el);
  return out;
}

/** All text below `el`. */
export function textOf(el: XEl): string {
  let out = "";
  for (const k of el.kids) out += typeof k === "string" ? k : textOf(k);
  return out;
}
