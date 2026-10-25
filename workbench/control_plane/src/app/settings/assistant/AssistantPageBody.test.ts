// The body of /settings/assistant draws each state of the switch (WS-63 PZ-25).
//
// Mutation: map "error" to the not-available line, and the error test
// fails. Draw nothing while pending, and the skeleton test fails. Drop the
// onClick of Retry, and the Retry test fails.
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ProfileState } from "@/lib/useAssistantProfileEnabled";

import { AssistantPageBody } from "./AssistantPageBody";

const PANE = createElement("section", { "data-pane": "" }, "the pane");
const draw = (state: ProfileState, onRetry = () => {}) =>
  renderToStaticMarkup(createElement(AssistantPageBody, { state, onRetry }, PANE));

/** Each element of a tree, so a test can find the Retry button and press it. */
function elements(node: ReactNode): ReactElement[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const props = node.props as { children?: ReactNode };
  return [node, ...elements(props.children)];
}

describe("AssistantPageBody", () => {
  it("on: the pane, and no other line", () => {
    const html = draw("on");
    expect(html).toContain("the pane");
    expect(html).not.toContain("not available");
    expect(html).not.toContain("Retry");
  });

  it("off: the not-available line, and no pane", () => {
    const html = draw("off");
    expect(html).toContain("This page is not available yet.");
    expect(html).not.toContain("the pane");
  });

  it("pending: a skeleton, never the not-available line nor the pane", () => {
    const html = draw("pending");
    expect(html).toContain('data-testid="assistant-page-loading"');
    expect(html).not.toContain("not available");
    expect(html).not.toContain("the pane");
  });

  it("error: an alert with Retry, never the not-available line nor the pane", () => {
    const html = draw("error");
    expect(html).toContain('role="alert"');
    expect(html).toContain("This page did not load.");
    expect(html).toContain("Retry");
    expect(html).not.toContain("not available");
    expect(html).not.toContain("the pane");
  });

  it("error: Retry calls onRetry", () => {
    const onRetry = vi.fn();
    const tree = AssistantPageBody({ state: "error", onRetry, children: PANE });
    const retry = elements(tree).find((e) => (e.props as { children?: unknown }).children === "Retry");
    expect(retry).toBeDefined();
    (retry!.props as { onClick: () => void }).onClick();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
