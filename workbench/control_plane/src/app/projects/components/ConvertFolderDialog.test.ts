/**
 * WS-64 OP-10 (D-OP-16) — "Convert to project".
 *
 * Two halves. The words are pure (`convertCopy`) and tested as values. The
 * dialog and the page are source tests, because `vitest.config.ts` runs in
 * `node` with no DOM: they pin the house primitives, the refusal in place,
 * and the one call the page makes.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { CONVERT_LIST_LIMIT, convertCopy } from "../lib/projectConvert";

const SRC = fileURLToPath(new URL("../../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");

const dialog = read("app/projects/components/ConvertFolderDialog.tsx");
const page = read("app/projects/page.tsx");
const api = read("app/projects/lib/api.ts");

describe("convertCopy", () => {
  it("says the children become subprojects", () => {
    const copy = convertCopy("Clients", ["Acme", "Globex", "Initech"]);
    expect(copy.description).toBe(
      "Clients becomes a project. The projects inside it become its subprojects."
    );
    expect(copy.children).toBe(
      "These 3 projects become subprojects of Clients: Acme, Globex, Initech."
    );
    expect(copy.note).toContain("cannot turn back into a folder");
  });
  it("names one child, and an empty folder", () => {
    expect(convertCopy("Clients", ["Acme"]).children).toBe(
      "Acme becomes a subproject of Clients."
    );
    expect(convertCopy("Clients", []).children).toBe("Clients holds no projects yet.");
  });
  it("lists a long folder up to the limit and counts the rest", () => {
    const names = Array.from({ length: CONVERT_LIST_LIMIT + 3 }, (_, i) => `P${i}`);
    const text = convertCopy("Big", names).children;
    expect(text).toContain(`These ${names.length} projects`);
    expect(text).toContain(`P${CONVERT_LIST_LIMIT - 1} and 3 more.`);
    expect(text).not.toContain(`P${CONVERT_LIST_LIMIT},`);
  });
});

describe("ConvertFolderDialog", () => {
  it("is drawn from the house Modal and Button", () => {
    expect(dialog).toContain('import Modal from "@/components/ui/Modal"');
    expect(dialog).toContain('import Button from "@/components/ui/Button"');
    expect(dialog).not.toMatch(/<button\b/);
  });
  it("shows the copy and the server's refusal in place", () => {
    expect(dialog).toContain("convertCopy(project.name, childNames)");
    expect(dialog).toContain('role="alert"');
    expect(dialog).toContain("Convert to project");
  });
});

describe("the page and the client", () => {
  it("posts the one direction to the convert route", () => {
    expect(api).toMatch(/convertFolder: \(projectId: string\) =>\s+call<ProjectRow>\(`nodes\/\$\{projectId\}\/convert`/);
    expect(api).toContain('JSON.stringify({ to: "project" })');
  });
  it("opens the dialog from the menu and converts only on confirm", () => {
    expect(page).toContain("setConvertingNode(project);");
    expect(page).toContain("projectsApi.convertFolder(node.id)");
    expect(page).toContain("onConfirm={(project) => void convertNode(project)}");
    // A refusal stays in the dialog, so the member reads the names.
    expect(page).toContain("setConvertError(String((err as Error).message));");
  });
});
