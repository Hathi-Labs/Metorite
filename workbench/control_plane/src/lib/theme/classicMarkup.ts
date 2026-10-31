/**
 * Rendered markup as the classic look sees it (D143, `glass_look.md`).
 *
 * A `glass:` class applies only under `<html data-look="glass">`, so it is
 * no change to a classic page. A golden test of rendered HTML passes its
 * markup through this before it compares, and keeps the golden it took
 * before the glass look existed. The test then also proves that the classic
 * markup did not change, and no later glass slice has to retake it.
 *
 * Inside each `class="…"` it removes every token whose variants include
 * `glass`, with the one space before it, so the classic spacing comes back
 * byte for byte: a hash taken before glass still matches. A `class`
 * attribute left empty goes.
 *
 * `collapseSpaces` also squeezes each run of spaces to one. Use it, on both
 * sides, where a class string is assembled from parts that may be empty, so
 * a glass part that is empty in one place leaves a double space. GL-Z
 * deletes this module with the classic look.
 */
export function classicMarkup(html: string, { collapseSpaces = false } = {}): string {
  return html
    .replace(/class="([^"]*)"/g, (_, cls: string) => {
      let kept = cls
        .split(" ")
        .filter((t) => !(t && t.split(":").slice(0, -1).includes("glass")))
        .join(" ");
      if (collapseSpaces) kept = kept.replace(/\s+/g, " ").trim();
      return `class="${kept}"`;
    })
    .replace(/ class=""/g, "");
}
