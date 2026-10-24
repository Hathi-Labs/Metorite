/**
 * The six view names of `/tasks` and of Projects · My work come from one
 * source (WS-64 OP-2 fix round 2), and My work hides the list's own heading.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { MY_WORK_VIEWS } from "@/app/projects/lib/myWork";

import { VIEW_TITLES } from "./viewTitles";

const read = (rel: string) => fs.readFileSync(path.join(__dirname, "..", rel), "utf8");

describe("VIEW_TITLES", () => {
  it("holds the /tasks wording", () => {
    expect(VIEW_TITLES).toEqual({
      inbox: "Inbox",
      today: "Today",
      next: "My Next Actions",
      waiting: "Waiting For",
      someday: "Someday / Maybe",
      done: "Done",
    });
  });

  it("names every My work entry, in the same case", () => {
    for (const v of MY_WORK_VIEWS) {
      expect(v.label).toBe(VIEW_TITLES[v.view as keyof typeof VIEW_TITLES]);
    }
  });

  it("is read by the list title and the /tasks sidebar", () => {
    expect(read("components/ItemList.tsx")).toMatch(/title: VIEW_TITLES\.waiting/);
    expect(read("components/ListsSidebar.tsx")).toMatch(/label: VIEW_TITLES\.waiting/);
  });
});

describe("embedded lists", () => {
  it("ItemList and InboxView take an `embedded` prop that hides their heading", () => {
    expect(read("components/ItemList.tsx")).toMatch(/embedded = false/);
    expect(read("components/ItemList.tsx")).toMatch(/\{!embedded && meta\.title\}/);
    expect(read("components/ItemList.tsx")).toMatch(/\{!embedded && <Icon/);
    expect(read("components/InboxView.tsx")).toMatch(/embedded = false/);
    expect(read("components/InboxView.tsx")).toMatch(/embedded \? " hidden"/);
  });

  it("My work mounts both embedded, and /tasks mounts neither", () => {
    const host = fs.readFileSync(
      path.join(__dirname, "../../projects/components/MyWorkHost.tsx"),
      "utf8",
    );
    expect(host).toMatch(/<InboxView embedded \/>/);
    expect(host).toMatch(/<ItemList embedded \/>/);
    const page = read("../page.tsx");
    expect(page).not.toMatch(/embedded/);
  });
});
