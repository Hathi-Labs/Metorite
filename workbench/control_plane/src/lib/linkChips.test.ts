/**
 * Link chips (KB-4b, `knowledge_base.md` §8.3): a link in an answer chips
 * only when the same message's finished tools printed the same href.
 */
import { describe, expect, it } from "vitest";

import { buildLinkChipIndex, chipForHref } from "@/lib/linkChips";

const PAGE = "/knowledge/pages/p-1";
const TASK = "/projects?task=t-9";
const DEAL = "/crm?tab=deals&deal=d-3";

const SEARCH = [
  `- «Onboarding guide» (page, 2026-10-01) ${PAGE}`,
  "  full_id: page:p-1",
  `- «Fix the login bug» (task, 2026-10-02) ${TASK}`,
  "  full_id: task:t-9",
  `- «Acme renewal» (deal, 2026-10-03) ${DEAL}`,
  "  full_id: deal:d-3",
].join("\n");

const done = (result: string) => ({ result, status: "done" });

describe("buildLinkChipIndex and chipForHref", () => {
  const index = buildLinkChipIndex([done(SEARCH)]);

  it("gives a page, a task and a deal their app, icon and tool title", () => {
    expect(chipForHref(index, PAGE)).toEqual({ app: "Knowledge", icon: "BookOpen", label: "Onboarding guide" });
    // Pinned: a task wears the Projects icon, as its pane does.
    expect(chipForHref(index, TASK)).toEqual({ app: "Projects", icon: "FolderKanban", label: "Fix the login bug" });
    expect(chipForHref(index, DEAL)).toEqual({ app: "CRM", icon: "KanbanSquare", label: "Acme renewal" });
  });

  it("gives null for a link no tool printed", () => {
    expect(chipForHref(index, "/projects?task=other")).toBeNull();
    expect(chipForHref(index, "/crm?tab=deals&deal=d-4")).toBeNull();
  });

  it("gives null for a look-alike with a different query", () => {
    expect(chipForHref(index, "/projects?task=t-9&x=1")).toBeNull();
    expect(chipForHref(index, "/projects")).toBeNull();
  });

  it("reads a running or failed tool as nothing", () => {
    expect(buildLinkChipIndex([{ result: SEARCH, status: "running" }]).size).toBe(0);
    expect(buildLinkChipIndex([{ result: SEARCH, status: "error" }]).size).toBe(0);
    expect(buildLinkChipIndex([{ status: "done" }]).size).toBe(0);
  });

  it("reads an event with no status, and a sub-agent tool", () => {
    expect(chipForHref(buildLinkChipIndex([{ result: SEARCH }]), PAGE)).not.toBeNull();
    const nested = buildLinkChipIndex([{ status: "done", subAgentTools: [done(SEARCH)] }]);
    expect(chipForHref(nested, TASK)?.label).toBe("Fix the login bug");
    expect(buildLinkChipIndex([{ subAgentTools: [{ result: SEARCH, status: "error" }] }]).size).toBe(0);
  });

  it("uses the app name when two titles share one href", () => {
    const wa = buildLinkChipIndex([
      done("- «Chat with Asha» (message, 2026-10-01) /whatsapp\n- «Chat with Ben» (message, 2026-10-02) /whatsapp"),
    ]);
    expect(chipForHref(wa, "/whatsapp")).toEqual({ app: "WhatsApp", icon: "MessageSquare", label: "WhatsApp" });
  });

  it("matches an encoded id", () => {
    const enc = buildLinkChipIndex([done("- «Mail» (email) /email?email=a%2Fb%20c")]);
    expect(chipForHref(enc, "/email?email=a/b c")?.label).toBe("Mail");
    expect(chipForHref(enc, "/email?email=a%2Fb%20c")?.label).toBe("Mail");
  });

  it("refuses //host and an external URL", () => {
    const bad = buildLinkChipIndex([done("- «Evil» (page) //evil.example/x\n- «Out» (page) https://evil.example/x")]);
    expect(bad.size).toBe(0);
    expect(chipForHref(index, "//evil.example/x")).toBeNull();
    expect(chipForHref(index, "https://evil.example/x")).toBeNull();
  });

  it("takes the label from the tool, never the link text", () => {
    expect(chipForHref(index, PAGE, "click here for secrets")?.label).toBe("Onboarding guide");
  });

  it("falls back to the link text, then the app name, when no title parses", () => {
    const bare = buildLinkChipIndex([done(`see ${PAGE}`)]);
    expect(chipForHref(bare, PAGE, "The guide")?.label).toBe("The guide");
    expect(chipForHref(bare, PAGE)?.label).toBe("Knowledge");
  });

  it("gives null for no index, and for a path no pane owns", () => {
    expect(chipForHref(undefined, PAGE)).toBeNull();
    const odd = buildLinkChipIndex([done("- «X» (page) /nowhere/1")]);
    expect(chipForHref(odd, "/nowhere/1")).toBeNull();
  });
});
