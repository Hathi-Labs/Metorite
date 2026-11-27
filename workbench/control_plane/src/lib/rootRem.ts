/**
 * The root font size in px: the density the member chose, applied. Compact and
 * comfortable change it, so a sum of rem heights must not assume 16.
 *
 * 16 on the server and wherever it cannot be read. `StatusMenu` and the
 * windowed list read this one function.
 */
export function rootRemPx(
  doc: Pick<Document, "documentElement"> | undefined = globalThis.document,
  style: (el: Element) => Pick<CSSStyleDeclaration, "fontSize"> = (el) => getComputedStyle(el),
): number {
  if (!doc?.documentElement) return 16;
  const px = parseFloat(style(doc.documentElement).fontSize);
  return px > 0 ? px : 16;
}
