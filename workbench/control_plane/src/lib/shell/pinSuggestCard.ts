/**
 * The two answers of the pin card (PKG-19a-2), apart from React so a test
 * can run them. Yes calls `accept` once. No calls `hide` once. Both then
 * close the card. This file writes nothing itself.
 */
export type PinAnswer = "yes" | "no";

export async function answerCard(
  answer: PinAnswer,
  href: string,
  deps: {
    accept: (href: string) => Promise<unknown>;
    hide: (href: string) => void;
    close: () => void;
  },
): Promise<void> {
  if (answer === "no") {
    deps.hide(href);
    deps.close();
    return;
  }
  deps.close();
  await deps.accept(href);
}
