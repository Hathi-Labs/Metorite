// The vendor registry — what the providers page offers, and what it promises.
//
// 🔴 **The slug rule is fenced in Python, not here.** Only
// `tests/unit/test_operator_console_vendor_slugs.py` can ask litellm whether a
// slug is real, and that is the check that catches the next `google`. What
// this file pins is everything a JavaScript reader can verify: that a card is
// completable, that a vendor lands in the section somebody would look in, and
// that the two slugs which were actually wrong stay gone.

import { describe, expect, it } from "vitest";

import {
  KNOWN_PROVIDERS,
  PROVIDER_GUIDES,
  SECTIONS,
  guideFor,
  isRoutedToday,
  sectionOf,
  vendorLabel,
} from "./providerGuides";

describe("every card can actually be finished", () => {
  it.each(KNOWN_PROVIDERS)("%s has a way in and a way to follow", (slug) => {
    const g = PROVIDER_GUIDES[slug];
    // A card with no setup link sends the operator to a search engine, which
    // is where they were before this page existed.
    expect(g.setupUrl).toMatch(/^https:\/\//);
    expect(g.steps.length).toBeGreaterThan(0);
    expect(g.label.length).toBeGreaterThan(0);
    expect(g.description.length).toBeGreaterThan(0);
    // ⚠️ `serves` drives the section AND the "nothing calls this yet" note.
    // An empty one silently files the vendor under "not written up".
    expect(g.serves.length).toBeGreaterThan(0);
  });

  it("🔴 offers no slug the Console would refuse on install", () => {
    // Mirrors `check_provider`'s regex. Offering a vendor that 400s on save is
    // the worst possible card: it looks like the product is broken.
    for (const slug of KNOWN_PROVIDERS) {
      expect(slug).toMatch(/^[a-z0-9][a-z0-9_.-]{1,39}$/);
    }
  });

  it("🔴 keeps the two slugs that were WRONG out", () => {
    // `google` and `together` read as correct to anybody who has not checked
    // litellm's provider list, and both were written by somebody being
    // careful. They cost an install, a declaration, a binding and a 503.
    expect(KNOWN_PROVIDERS).not.toContain("google");
    expect(KNOWN_PROVIDERS).not.toContain("together");
    expect(KNOWN_PROVIDERS).toContain("gemini");
    expect(KNOWN_PROVIDERS).toContain("together_ai");
  });
});

describe("which shelf a vendor sits on", () => {
  it("files a vendor that does both under chat", () => {
    // OpenAI transcribes. Nobody looks for OpenAI under Speech to text, and a
    // vendor appearing twice would double every count on the filter chips.
    expect(PROVIDER_GUIDES.openai.serves).toContain("transcribe");
    expect(sectionOf("openai")).toBe("chat");
    expect(sectionOf("groq")).toBe("chat");
  });

  it("files a transcribe-only vendor where somebody would look", () => {
    expect(sectionOf("assemblyai")).toBe("listen");
    expect(sectionOf("elevenlabs")).toBe("voice");
  });

  it("puts a vendor we have never written up in its own section", () => {
    expect(sectionOf("some-vendor-we-bought-yesterday")).toBe("other");
  });

  it("has a section defined for every answer sectionOf can give", () => {
    // ⚠️ A section with no heading renders as a silently missing group of
    // cards — the vendor is on the page and invisible.
    const defined = new Set(SECTIONS.map((s) => s.key));
    for (const slug of [...KNOWN_PROVIDERS, "unheard-of"]) {
      expect(defined).toContain(sectionOf(slug));
    }
  });
});

describe("what the Router can actually call", () => {
  it("🔴 says a transcription vendor is not routed yet", () => {
    // `main.py` declares exactly one `/v1/` route. An AssemblyAI key installs,
    // encrypts and is then called by nothing — and an operator who does not
    // know that will look for the fault in the key.
    expect(isRoutedToday("assemblyai")).toBe(false);
    expect(isRoutedToday("elevenlabs")).toBe(false);
  });

  it("says a chat vendor is", () => {
    expect(isRoutedToday("anthropic")).toBe(true);
    // Groq transcribes as well, and the chat half is live, so the card must
    // not carry a warning that is only true of half of it.
    expect(isRoutedToday("groq")).toBe(true);
  });

  it("⚠️ assumes an UNKNOWN vendor is fine", () => {
    // We do not know what it serves. A warning we cannot justify teaches
    // people to skip warnings, including the ones that are true.
    expect(isRoutedToday("some-vendor-we-bought-yesterday")).toBe(true);
  });
});

describe("names", () => {
  it("shows the vendor's own name, not the litellm id", () => {
    expect(vendorLabel("together_ai")).toBe("Together AI");
    expect(vendorLabel("gemini")).toBe("Google Gemini");
  });

  it("shows the slug back for a vendor we do not know", () => {
    // Better than "Unknown", which loses the one fact the reader needs to
    // match this card against the model ids they are about to declare.
    expect(vendorLabel("acme-llm")).toBe("acme-llm");
  });

  it("matches case-insensitively, because the form does not shout", () => {
    expect(guideFor("  Anthropic ")?.label).toBe("Anthropic");
  });
});
