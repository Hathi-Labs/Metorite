/**
 * The push prompt sheet (WS-OB OB-8): the three buttons, Escape, and the
 * ios-tab variant. `PushPromptPanel` holds no hook, so the test calls it and
 * walks the element tree it returns. No DOM is needed.
 */
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { PushPromptPanel, type PushPromptPanelProps } from "./PushPromptSheet";

type El = ReactElement<Record<string, unknown>>;

function walk(node: ReactNode, out: El[] = []): El[] {
  if (Array.isArray(node)) node.forEach((n) => walk(n, out));
  else if (isValidElement(node)) {
    const el = node as El;
    out.push(el);
    walk(el.props.children as ReactNode, out);
  }
  return out;
}

function text(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement(node)) return text((node as El).props.children as ReactNode);
  return "";
}

function setup(over: Partial<PushPromptPanelProps> = {}) {
  const fns = { onTurnOn: vi.fn(), onLater: vi.fn(), onNever: vi.fn(), onClose: vi.fn() };
  const props: PushPromptPanelProps = { kind: "turn-on", phase: "ask", ...fns, ...over };
  const tree = PushPromptPanel(props);
  const buttons = walk(tree).filter((e) => typeof e.type !== "string" && e.props.type === "button");
  const byLabel = (label: string) => buttons.find((b) => text(b.props.children as ReactNode).trim() === label);
  return { fns, tree, buttons, byLabel, html: renderToStaticMarkup(createElement(PushPromptPanel, props)) };
}

describe("PushPromptPanel", () => {
  it("is a labelled region with the spec's title and body", () => {
    const { tree, html } = setup();
    expect(tree.props.role).toBe("region");
    expect(tree.props["aria-label"]).toBe("Get notified on this phone");
    expect(html).toContain("Turn on notifications so reminders, your morning brief and replies that need you reach this phone, even when Metorite is closed.");
  });

  it("Turn on, Remind me later and Don't ask again each call their handler", () => {
    const { fns, byLabel } = setup();
    (byLabel("Turn on")!.props.onClick as () => void)();
    (byLabel("Remind me later")!.props.onClick as () => void)();
    (byLabel("Don't ask again")!.props.onClick as () => void)();
    expect(fns.onTurnOn).toHaveBeenCalledTimes(1);
    expect(fns.onLater).toHaveBeenCalledTimes(1);
    expect(fns.onNever).toHaveBeenCalledTimes(1);
    expect(fns.onClose).not.toHaveBeenCalled();
  });

  it("the close button closes and names itself", () => {
    const { fns, byLabel, buttons } = setup();
    const close = buttons.find((b) => b.props["aria-label"] === "Close")!;
    (close.props.onClick as () => void)();
    expect(fns.onClose).toHaveBeenCalledTimes(1);
    expect(byLabel("Turn on")).toBeDefined();
  });

  it("Escape closes, and another key does not", () => {
    const { fns, tree } = setup();
    const stop = vi.fn();
    (tree.props.onKeyDown as (e: unknown) => void)({ key: "Enter", stopPropagation: stop });
    expect(fns.onClose).not.toHaveBeenCalled();
    (tree.props.onKeyDown as (e: unknown) => void)({ key: "Escape", stopPropagation: stop });
    expect(fns.onClose).toHaveBeenCalledTimes(1);
  });

  it("ios-tab shows the two Home Screen steps with the Share glyph and no Turn on", () => {
    const { byLabel, html, tree } = setup({ kind: "ios-tab" });
    expect(byLabel("Turn on")).toBeUndefined();
    expect(html).toContain("Add to Home Screen");
    expect(walk(tree).some((e) => e.props.name === "Share")).toBe(true);
    expect(html.indexOf("Share")).toBeLessThan(html.indexOf("Add to Home Screen"));
    expect(byLabel("Remind me later")).toBeDefined();
  });

  it("denied shows the phone-settings steps and no Turn on", () => {
    const { byLabel, html } = setup({ kind: "denied" });
    expect(byLabel("Turn on")).toBeUndefined();
    expect(html).toContain("Open your phone&#x27;s Settings.");
    expect(byLabel("Got it")).toBeDefined();
  });

  it("after a success it says Notifications are on and holds no button", () => {
    const { html, buttons } = setup({ phase: "on" });
    expect(html).toContain("Notifications are on");
    expect(buttons).toHaveLength(0);
  });

  it("never names the model or a tool", () => {
    const all = (["turn-on", "ios-tab", "denied"] as const).map((kind) => setup({ kind }).html).join(" ");
    expect(all).not.toMatch(/claude|gpt|openai|anthropic|\btool\b/i);
  });
});
