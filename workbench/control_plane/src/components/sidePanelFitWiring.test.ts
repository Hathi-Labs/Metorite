/**
 * S8 fix round 4 — the side-panel fit decision reaches the two places that
 * act on it (verifier advisory: nothing proved that they do).
 *
 * 1. `MessageBubble` renders the artifact card with a side-panel handler only
 *    when the panel fits. Without one the card's Open goes to the viewer.
 * 2. The rail's `onArtifact` handler (`artifactHandler`) opens nothing when
 *    the panel does not fit.
 *
 * The bubble is rendered for real (`renderToStaticMarkup`) under each value
 * of `SidePanelFitContext`. The card's Open button names its target in its
 * title, so the markup says which handler it got.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import MessageBubble from "@/components/MessageBubble";
import type { ChatMessage } from "@/hooks/useAgentChat";
import { artifactHandler } from "@/lib/autoOpenArtifact";
import { SidePanelFitContext } from "@/lib/sidePanelFit";

const message = {
  id: "m1",
  role: "assistant",
  content: "I saved the report.",
  timestamp: new Date("2026-09-24T10:00:00Z").getTime(),
  customEvents: [
    { name: "artifact_created", value: { path: "outputs/status.md", size: 1800 } },
  ],
} as unknown as ChatMessage;

function bubble(fits: boolean): string {
  return renderToStaticMarkup(
    createElement(
      SidePanelFitContext.Provider,
      { value: fits },
      createElement(MessageBubble, { message, sessionId: "s1", onFileOpen: () => {} }),
    ),
  );
}

describe("MessageBubble acts on the fit", () => {
  it("offers the side panel when it fits", () => {
    const html = bubble(true);
    expect(html).toContain("Open in side panel");
    expect(html).not.toContain("Open the rendered artifact");
  });

  it("offers the viewer instead when it does not", () => {
    const html = bubble(false);
    expect(html).toContain("Open the rendered artifact");
    expect(html).not.toContain("Open in side panel");
  });
});

describe("the rail's artifact handler acts on the fit", () => {
  function deps() {
    return { openDoc: vi.fn(), setDocLive: vi.fn(), schedule: vi.fn() };
  }

  it("opens a written document when the panel fits", () => {
    const d = deps();
    artifactHandler({ sessionId: "s1", isMobile: false, panelFits: true }, d)({
      path: "outputs/status.md",
    });
    expect(d.openDoc).toHaveBeenCalledTimes(1);
  });

  it("opens nothing when it does not", () => {
    const d = deps();
    artifactHandler({ sessionId: "s1", isMobile: false, panelFits: false }, d)({
      path: "outputs/status.md",
    });
    expect(d.openDoc).not.toHaveBeenCalled();
  });
});
