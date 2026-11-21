import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

import { ORG_TABS, orgTabFromSearch } from "./orgTab";

describe("orgTabFromSearch", () => {
  it("reads a tab that the page draws", () => {
    for (const tab of ORG_TABS) expect(orgTabFromSearch(`?tab=${tab}`)).toBe(tab);
    expect(orgTabFromSearch("?x=1&tab=Members")).toBe("members");
  });

  it("gives nothing for no tab, an unknown tab or an odd value", () => {
    expect(orgTabFromSearch("")).toBeNull();
    expect(orgTabFromSearch("?tab=")).toBeNull();
    expect(orgTabFromSearch("?tab=billing")).toBeNull();
    expect(orgTabFromSearch("?tab=members%00")).toBeNull();
    expect(orgTabFromSearch("?tab[]=members")).toBeNull();
  });

  it("the ids are the ids of the tab strip", () => {
    // Mutation: rename a tab id in OrganizationAdmin -> red.
    const source = readFileSync(`${__dirname}/../OrganizationAdmin.tsx`, "utf8");
    for (const tab of ORG_TABS) expect(source).toContain(`id: "${tab}"`);
  });

  it("the page reads the tab again each time the query changes", () => {
    // A page that the chat moves with router.push stays mounted. Mutation: an
    // effect with no `search` dependency, or `window.location.search` -> red.
    const source = readFileSync(`${__dirname}/../OrganizationAdmin.tsx`, "utf8");
    expect(source).toContain('const search = useSearchParams()?.toString() ?? "";');
    expect(source).toMatch(/orgTabFromSearch\(search\);[\s\S]{0,80}\}, \[search\]\);/);
    expect(source).not.toContain("window.location.search");
  });

  it("the route keeps a Suspense boundary for the query read", () => {
    const page = readFileSync(`${__dirname}/../page.tsx`, "utf8");
    expect(page).toContain("<Suspense");
  });
});
