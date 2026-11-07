/**
 * Task 1007: inside a card with a dismiss "×", the fold header keeps a right
 * gutter, so a cut summary ends in a visible ellipsis before the "×". A card
 * with no dismiss overlay does not change. Mutation: drop the `gutter` class
 * from the header -> red.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CardDismissGutterContext, CardFoldProvider, CollapsibleCardHeader } from "./Collapsible";

const fold = { rooted: false, toggle: false, open: true, label: "Noted" };

function draw(gutter: boolean): string {
  return renderToStaticMarkup(
    <CardDismissGutterContext.Provider value={gutter}>
      <CardFoldProvider value={fold}>
        <CollapsibleCardHeader />
      </CardFoldProvider>
    </CardDismissGutterContext.Provider>,
  );
}

describe("the fold header beside a dismiss ×", () => {
  it("keeps a right gutter under a dismissable card", () => {
    expect(draw(true)).toMatch(/\bpr-6\b/);
  });
  it("is unchanged with no dismiss overlay", () => {
    expect(draw(false)).not.toMatch(/\bpr-6\b/);
  });
});
