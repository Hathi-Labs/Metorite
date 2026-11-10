// A zip in the chat (package_artifacts, 2026-10-11).
//
// The agent tool `package_artifacts` zips the files of a chat and shows the
// zip through the same artifact card as every other file. These cases hold
// what the card must do with it:
//
// - the card shows the archive icon and a Download link to the one address
//   (`workspaceFileUrl`), with the file name as the download name;
// - the card offers no viewer, because a zip has no preview;
// - the viewer modal shows a download for a zip and never fetches its bytes
//   (the old "binary" view read the whole file to show 256 bytes of hex).
//
// vitest runs in the node environment here, so the card renders to static
// markup and the modal's wiring is held by a source scan.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { classifyArtifact, isArchive, workspaceFileUrl } from "@/lib/artifactKind";
import { isKnownIcon } from "@/lib/icons";

import ArtifactCard from "./ArtifactCard";

const SESSION = "11111111-2222-3333-4444-555555555555";

function card(name: string, path: string, mimeType?: string): string {
  return renderToStaticMarkup(
    createElement(ArtifactCard, {
      artifact: { path, name, size: 2048, mimeType },
      sessionId: SESSION,
      onOpen: () => {},
    }),
  );
}

function source(rel: string): string {
  const text = readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

describe("isArchive", () => {
  it("knows a zip by its extension or its type", () => {
    expect(isArchive("files.zip")).toBe(true);
    expect(isArchive("FILES.ZIP")).toBe(true);
    expect(isArchive("bundle", "application/zip")).toBe(true);
    expect(isArchive("bundle", "application/x-zip-compressed")).toBe(true);
  });

  it("leaves every other file alone", () => {
    expect(isArchive("report.md")).toBe(false);
    expect(isArchive("sheet.xlsx")).toBe(false);
    expect(isArchive("zip.md")).toBe(false);
    expect(isArchive("data.bin", "application/octet-stream")).toBe(false);
  });

  it("keeps a zip in the binary kind, so no viewer tries to render it", () => {
    expect(classifyArtifact("files.zip", "outputs/files.zip", "application/zip")).toBe("binary");
  });
});

describe("the artifact card of a zip", () => {
  const path = "outputs/0f0e-1a2b3c4d/files.zip";
  const html = card("files.zip", path, "application/zip");

  it("downloads the zip from the one workspace address", () => {
    // A Download button names `download` (2026-10-16), so the proxy never
    // sends it the rendered page of a Markdown file.
    const href = workspaceFileUrl(SESSION, path, { download: true }).replace(/&/g, "&amp;");
    expect(html).toContain(`href="${href}"`);
    expect(html).toContain('download="files.zip"');
    expect(html).toContain('aria-label="Download files.zip"');
  });

  it("shows the archive icon", () => {
    expect(isKnownIcon("FileArchive")).toBe(true);
    expect(html).toMatch(/lucide-file-archive|lucide-archive/);
  });

  it("offers no viewer and no PDF, because a zip has no preview", () => {
    expect(html).not.toContain("Open in viewer");
    expect(html).not.toContain("Download PDF");
    expect(html).not.toContain('role="button"');
  });

  it("still offers the viewer for an ordinary binary file", () => {
    expect(card("data.bin", "outputs/data.bin")).toContain("Open in viewer");
  });
});

describe("the viewer modal of a zip", () => {
  const src = source("./ArtifactViewerModal.tsx");

  it("decides on the archive before any fetch of the bytes", () => {
    const guard = src.indexOf("isArchive(entry.name, entry.mime_type)");
    const fetchAt = src.indexOf("await fetch(fileUrl)");
    expect(guard).toBeGreaterThan(-1);
    expect(fetchAt).toBeGreaterThan(-1);
    expect(guard).toBeLessThan(fetchAt);
    expect(src).toMatch(
      /if \(isArchive\(entry\.name, entry\.mime_type\)\) \{\s*setState\(\{ status: "archive" \}\);\s*return;/,
    );
  });

  it("shows a download for a zip, not a hex dump", () => {
    expect(src).toContain('state.status === "archive"');
    expect(src).toContain("A zip file has no preview. Download it to open it.");
  });
});
