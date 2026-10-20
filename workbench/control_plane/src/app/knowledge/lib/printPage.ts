// ── Print one page (WS-61 KB-2d, `knowledge_base.md` §21.7) ────────────────
//
// The browser prints the whole window, and the window holds the shell, the
// rail and the panels. So Print copies the title and the body that the
// editor drew into a hidden frame, with the app's style sheets, and prints
// that frame. Paper is light, so the frame reads in light mode.
//
// ⚠️ It copies nodes with `importNode`, and it never writes HTML as text.
// The body is the DOM that the editor already drew, and no string reaches
// an HTML parser here (`htmlSinks.test.ts`).

/** The mark of the print frame, so that a second print replaces the first. */
export const PRINT_FRAME = "data-kb-print-frame";

/** The longest wait for the style sheets of the frame to load, in ms. */
const STYLE_WAIT_MS = 1500;

function loaded(link: HTMLLinkElement): Promise<void> {
  return new Promise((done) => {
    if (link.sheet) return done();
    link.addEventListener("load", () => done(), { once: true });
    link.addEventListener("error", () => done(), { once: true });
  });
}

/** Print `body` under the heading `title`. Resolves once the print starts. */
export async function printPage(title: string, body: HTMLElement): Promise<void> {
  document.querySelectorAll(`iframe[${PRINT_FRAME}]`).forEach((old) => old.remove());
  const frame = document.createElement("iframe");
  frame.setAttribute(PRINT_FRAME, "");
  frame.setAttribute("aria-hidden", "true");
  frame.tabIndex = -1;
  frame.style.position = "fixed";
  frame.style.width = "0";
  frame.style.height = "0";
  frame.style.border = "0";
  frame.style.right = "0";
  frame.style.bottom = "0";
  document.body.appendChild(frame);
  const doc = frame.contentDocument;
  const win = frame.contentWindow;
  if (!doc || !win) {
    frame.remove();
    return;
  }

  doc.title = title;
  // The look of the app, in light mode.
  doc.documentElement.className = document.documentElement.className.replace(/\bdark\b/g, "");
  doc.documentElement.classList.add("light");
  const inline = document.documentElement.getAttribute("style");
  if (inline) doc.documentElement.setAttribute("style", inline);
  const waits: Promise<void>[] = [];
  document.querySelectorAll<HTMLElement>('link[rel="stylesheet"], style').forEach((node) => {
    const copy = doc.importNode(node, true);
    doc.head.appendChild(copy);
    if (copy.tagName === "LINK") waits.push(loaded(copy as HTMLLinkElement));
  });

  const sheet = doc.createElement("main");
  sheet.className = "mx-auto max-w-3xl bg-background p-6 text-foreground";
  const heading = doc.createElement("h1");
  heading.className = "mb-4 text-2xl font-semibold";
  heading.textContent = title;
  sheet.appendChild(heading);
  sheet.appendChild(doc.importNode(body, true));
  doc.body.className = "bg-background";
  doc.body.appendChild(sheet);

  await Promise.race([Promise.all(waits), new Promise((done) => setTimeout(done, STYLE_WAIT_MS))]);
  win.focus();
  win.print();
}
