// The download of a zip that expired (package_artifacts, review 2, 2026-10-11).
//
// A zip lives on disk only. After a disk wipe the gateway answers 410 with
// PACKAGE_EXPIRED, and the card must say so instead of a failed download.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  DOWNLOAD_FAILED,
  PACKAGE_EXPIRED,
  archiveNotice,
  fetchArchive,
} from "./archiveDownload";

function answer(status: number, body = "x"): typeof fetch {
  return (async () => new Response(body, { status })) as typeof fetch;
}

function source(rel: string): string {
  const text = readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

describe("fetchArchive", () => {
  it("hands back the zip on 200", async () => {
    const got = await fetchArchive("/z", answer(200, "PK"));
    expect(got.kind).toBe("ok");
    expect(archiveNotice(got)).toBeNull();
  });

  it("says the package expired on 410", async () => {
    const got = await fetchArchive("/z", answer(410));
    expect(got).toEqual({ kind: "expired" });
    expect(archiveNotice(got)).toBe(PACKAGE_EXPIRED);
  });

  it("says the download failed on a plain 404 or a network error", async () => {
    expect(archiveNotice(await fetchArchive("/z", answer(404)))).toBe(DOWNLOAD_FAILED);
    const broken = (async () => { throw new TypeError("offline"); }) as typeof fetch;
    expect(archiveNotice(await fetchArchive("/z", broken))).toBe(DOWNLOAD_FAILED);
  });
});

describe("the text and the wiring", () => {
  it("is the gateway's PACKAGE_EXPIRED word for word", () => {
    const py = readFileSync(
      fileURLToPath(new URL(
        "../../../../apps/services/gateway/gateway/routes/workspace.py", import.meta.url,
      )),
      "utf-8",
    );
    expect(py).toContain(`"${PACKAGE_EXPIRED}"`);
  });

  it("the card fetches a zip through fetchArchive and shows the notice", () => {
    const src = source("../components/ArtifactCard.tsx");
    expect(src).toContain("onClick={archive ? downloadArchive :");
    expect(src).toContain("await fetchArchive(fileUrl)");
    expect(src).toContain("setNotice(archiveNotice(result))");
    expect(src).toMatch(/\{notice && \(\s*<p role="status"/);
  });
});
