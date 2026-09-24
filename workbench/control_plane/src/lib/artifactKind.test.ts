import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  HTML_EXTS,
  MARKDOWN_EXTS,
  canDownloadPdf,
  classifyArtifact,
  isArtifactPath,
  isRenderable,
  pdfNameFor,
  workspaceFileUrl,
} from "./artifactKind";

describe("classifyArtifact", () => {
  // The mobile bug this module exists to prevent: ArtifactViewerModal is the
  // ONLY artifact viewer on a phone (the side panel is desktop-only), and it had
  // its own copy of this logic where html/jsx/tsx fell through to CODE_EXTS. A
  // full-page artifact rendered on desktop and showed as source on mobile.
  it("classifies .html as a rendered artifact, not code", () => {
    expect(classifyArtifact("report.html", "outputs/report.html")).toBe("html");
    expect(classifyArtifact("report.htm", "outputs/report.htm")).toBe("html");
  });

  it("classifies .jsx/.tsx under outputs/ as a React artifact", () => {
    expect(classifyArtifact("d.jsx", "outputs/d.jsx")).toBe("react");
    expect(classifyArtifact("d.tsx", "outputs/d.tsx")).toBe("react");
    expect(classifyArtifact("d.jsx", "/outputs/d.jsx")).toBe("react");
  });

  it("leaves .jsx/.tsx outside outputs/ as source code", () => {
    // An agent reading its own source must never have the viewer build and run it.
    expect(classifyArtifact("Foo.tsx", "src/components/Foo.tsx")).toBe("code");
    expect(classifyArtifact("page.jsx", "app/page.jsx")).toBe("code");
  });

  it("prefers a real document type over the code-extension list", () => {
    // pdf/docx are checked before CODE_EXTS for the same reason html is.
    expect(classifyArtifact("a.pdf", "outputs/a.pdf")).toBe("pdf");
    expect(classifyArtifact("a.docx", "outputs/a.docx")).toBe("docx");
    expect(classifyArtifact("a", "outputs/a", "application/pdf")).toBe("pdf");
  });

  it("classifies the ordinary kinds unchanged", () => {
    expect(classifyArtifact("n.md", "outputs/n.md")).toBe("markdown");
    expect(classifyArtifact("r.py", "outputs/r.py")).toBe("code");
    expect(classifyArtifact("c.png", "outputs/c.png")).toBe("image");
    expect(classifyArtifact("d.csv", "outputs/d.csv")).toBe("code");
    expect(classifyArtifact("l.log", "outputs/l.log")).toBe("text");
    expect(classifyArtifact("b.bin", "outputs/b.bin")).toBe("binary");
  });

  it("is case-insensitive about extensions", () => {
    expect(classifyArtifact("R.HTML", "outputs/R.HTML")).toBe("html");
    expect(classifyArtifact("D.JSX", "outputs/D.JSX")).toBe("react");
  });
});

describe("isArtifactPath", () => {
  it("accepts outputs/ with or without a leading slash", () => {
    expect(isArtifactPath("outputs/a.jsx")).toBe(true);
    expect(isArtifactPath("/outputs/a.jsx")).toBe(true);
  });
  it("rejects anything else, including a nested outputs/", () => {
    expect(isArtifactPath("src/outputs/a.jsx")).toBe(false);
    expect(isArtifactPath("inputs/a.jsx")).toBe(false);
  });
});

describe("isRenderable", () => {
  it("is true for exactly the kinds with a rendered view", () => {
    expect(isRenderable("html")).toBe(true);
    expect(isRenderable("react")).toBe(true);
    for (const k of ["markdown", "code", "image", "pdf", "docx", "text", "binary"] as const) {
      expect(isRenderable(k)).toBe(false);
    }
  });
});

// WS-27bm S8 — the PDF link. The gateway converts md/markdown/mdx/html/htm and
// answers any other file with a 415, so the viewers offer the link for the
// same two kinds and no other.
describe("canDownloadPdf", () => {
  it("is true for Markdown and HTML only", () => {
    expect(canDownloadPdf("markdown")).toBe(true);
    expect(canDownloadPdf("html")).toBe(true);
    for (const k of ["react", "code", "image", "pdf", "docx", "text", "binary"] as const) {
      expect(canDownloadPdf(k)).toBe(false);
    }
  });
});

describe("workspaceFileUrl", () => {
  it("builds the raw link and the PDF link to one file", () => {
    expect(workspaceFileUrl("s1", "outputs/a b.md")).toBe(
      "/api/agent/workspace/s1/file?path=outputs%2Fa%20b.md",
    );
    expect(workspaceFileUrl("s1", "outputs/a b.md", { pdf: true })).toBe(
      "/api/agent/workspace/s1/file?path=outputs%2Fa%20b.md&format=pdf",
    );
  });

  it("names the PDF after the file", () => {
    expect(pdfNameFor("status.md")).toBe("status.pdf");
    expect(pdfNameFor("page.v2.html")).toBe("page.v2.pdf");
    expect(pdfNameFor("README")).toBe("README.pdf");
  });
});

describe("the PDF extensions agree with the gateway", () => {
  // Fix round 1: `.markdown` was text here and a PDF source there, so the
  // viewers hid a link the gateway would have served.
  const py = readFileSync(
    fileURLToPath(
      new URL("../../../../apps/services/gateway/gateway/pdf_render.py", import.meta.url),
    ),
    "utf8",
  );
  const block = py.slice(py.indexOf("SOURCE_KINDS"), py.indexOf("}", py.indexOf("SOURCE_KINDS")));
  const gateway = [...block.matchAll(/"\.(\w+)":\s*"(\w+)"/g)].map((m) => [m[1], m[2]]);

  it("reads the gateway's list", () => {
    expect(gateway.length).toBeGreaterThanOrEqual(5);
  });

  it("offers the PDF link for exactly the gateway's extensions", () => {
    const here = [
      ...MARKDOWN_EXTS.map((e) => [e, "markdown"]),
      ...HTML_EXTS.map((e) => [e, "html"]),
    ];
    expect([...here].sort()).toEqual([...gateway].sort());
    for (const [ext] of gateway) {
      const kind = classifyArtifact(`f.${ext}`, `outputs/f.${ext}`);
      expect(canDownloadPdf(kind), ext).toBe(true);
    }
  });
});
