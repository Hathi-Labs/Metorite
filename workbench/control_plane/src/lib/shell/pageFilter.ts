/**
 * The page's own list filter, from the shell's side (`navigation_shell.md`
 * §6.1 and §6.7).
 *
 * A page marks its filter with `data-page-filter="<the list's name>"`. The
 * mark sits on the input, or on a closed button that opens it (add
 * `data-page-filter-opener`), as My Tasks keeps its box an icon until asked.
 *
 * Two shell acts reach the filter, and both live HERE, once, for every page:
 *   • `/` focuses it (§6.1).
 *   • "Show all in …" fills it with the words typed into the bar (§6.7 rule 3).
 *
 * ⚠️ Filling is generic on purpose. The first version left it to each app,
 * and only Email listened, so the row did nothing on My Tasks and on Chat
 * (review, 2026-10-08). The shell now sets the value the way a keystroke does,
 * so every React input takes it. A page that must do more (Email lifts
 * `from:` into pills) listens for `FILL_PAGE_FILTER` and calls
 * `preventDefault()`, and the shell then leaves the box alone.
 */
import { FILL_PAGE_FILTER } from "./registry";

const visible = (el: Element | null): el is HTMLElement =>
  !!el && (el as HTMLElement).offsetParent !== null;

/** The page's filter mark (the input, or its opener), when one is on screen. */
export function pageFilterTarget(): HTMLElement | null {
  for (const el of Array.from(document.querySelectorAll<HTMLElement>("[data-page-filter]"))) {
    if (visible(el)) return el;
  }
  return null;
}

/** The list's name, for "Show all in <name>". */
export function pageFilterName(): string | null {
  return pageFilterTarget()?.getAttribute("data-page-filter") || null;
}

const nextFrame = () => new Promise<void>((r) => requestAnimationFrame(() => r()));

/** The filter's input, opening it first if the page keeps it closed. */
async function openInput(): Promise<HTMLInputElement | null> {
  const target = pageFilterTarget();
  if (!target) return null;
  if (target instanceof HTMLInputElement) return target;
  if (!target.hasAttribute("data-page-filter-opener")) return null;
  target.click();
  for (let i = 0; i < 5; i++) {
    await nextFrame();
    const input = pageFilterTarget();
    if (input instanceof HTMLInputElement) return input;
  }
  return null;
}

/** `/`: focus the page's filter. False when the page has none. */
export async function focusPageFilter(): Promise<boolean> {
  const input = await openInput();
  input?.focus();
  return !!input;
}

/** Set an input's value as a keystroke would, so React's onChange runs. */
function typeInto(input: HTMLInputElement, words: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, words);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

/** "Show all in …": put the words into the page's filter. */
export async function fillPageFilter(words: string): Promise<void> {
  const event = new CustomEvent(FILL_PAGE_FILTER, { detail: { query: words }, cancelable: true });
  // A page that handles the words itself cancels the event.
  if (!window.dispatchEvent(event)) return;
  const input = await openInput();
  if (!input) return;
  typeInto(input, words);
  input.focus();
}
