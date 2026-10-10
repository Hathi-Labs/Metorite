"use client";

/**
 * RollupCard — the one seam that folds a long chat card (owner request,
 * 2026-10-10). Every card in the transcript flow draws through it: the
 * generative-UI cards (`MessageBubble`), the Projects receipts
 * (`ProjectToolCards`) and the inline approval group (`AgentChat`).
 *
 * `lib/cardRollup.ts` holds the rules and says why. This file only wires
 * them to the page:
 *
 * - the transcript's registry, so a card knows whether it is the newest;
 * - the set of elements that wait (`lib/askPin.ts` `waitingTargets`);
 * - a `ResizeObserver` on the body, so "long" is a measure;
 * - the member's own toggle, kept for the page's life.
 *
 * Outside a transcript (no provider), and inside a step of the working trail
 * (`InStepContext`), a card draws as it always did: no toggle, no fold. A
 * read in the trail is already folded by its step.
 *
 * ## One toggle, one place, inside the card (owner feedback, 2026-10-10)
 *
 * The first cut drew a "Roll up" control ABOVE an open card, at the right,
 * and the folded card's chevron at the LEFT. The pointer travelled across
 * the card to undo what it had just done. Now the card's own title row is
 * the toggle, in both states, with the chevron at its left:
 *
 * - a card that HAS a title row (a receipt, a titled generative-UI card)
 *   passes `header="own"`, and draws {@link RollupHeader} where its title
 *   row was and {@link RollupBody} round the rest. Its title draws once;
 * - a card that has none (a table, a chart) keeps the default, and the
 *   wrapper draws a frame with the header as its first row while it folds.
 */

import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { InStepContext } from "@/components/ToolCardShell";
import {
  CardFoldProvider,
  CollapsibleCard,
  CollapsibleCardBody,
  CollapsibleCardHeader,
} from "@/components/ui/Collapsible";
import {
  isLong,
  manualStateOf,
  rollupOpen,
  setManualState,
  showRollupToggle,
  type ManualState,
  type RollupRegistry,
} from "@/lib/cardRollup";

export interface RollupScope {
  registry: RollupRegistry<Element>;
  /** The `data-chat-ask` targets of the elements that wait on the member. */
  waiting: ReadonlySet<string>;
  /** Told when the member folds or opens a card by hand. The transcript
   *  then keeps its place instead of following the bottom. */
  onManualToggle?: () => void;
}

export const RollupContext = createContext<RollupScope | null>(null);

export interface RollupCardProps {
  /** Stable for the card's life, across a remount and a replay: a tool
   *  call's id, a generative-UI target. */
  id: string;
  /** The header's words. */
  title: string;
  icon?: string;
  /** The rows the body lists: drawn as the count, and more than four makes
   *  the card long without a measure. */
  rows?: number;
  /** One line for the folded card. */
  summary?: string;
  /** The card waits on the member. */
  pending?: boolean;
  /** The card's `data-chat-ask` target. It waits while `lib/askPin.ts` says so. */
  askTarget?: string;
  /**
   * The card draws its own title row as {@link RollupHeader}, and its body
   * inside {@link RollupBody}. Leave it out for a card with no title row.
   */
  header?: "own" | "frame";
  className?: string;
  children: React.ReactNode;
}

/**
 * The card's title row: the one toggle while the card folds, and its plain
 * title row otherwise. Draw it where the title row was.
 */
export const RollupHeader = CollapsibleCardHeader;

/** The part of the card that folds away. Wrap everything under the title. */
export const RollupBody = CollapsibleCardBody;

export default function RollupCard(props: RollupCardProps) {
  const scope = useContext(RollupContext);
  const inStep = useContext(InStepContext);
  if (!scope || inStep) {
    // No fold here. A card with its own title row still draws that row.
    return (
      <CardFoldProvider
        value={{ rooted: false, toggle: false, open: true, label: props.title, icon: props.icon, count: props.rows }}
      >
        {props.children}
      </CardFoldProvider>
    );
  }
  return <RollupCardIn scope={scope} {...props} />;
}

function RollupCardIn({
  scope,
  id,
  title,
  icon,
  rows,
  summary,
  pending: pendingProp = false,
  askTarget,
  header = "frame",
  className,
  children,
}: RollupCardProps & { scope: RollupScope }) {
  const { registry, waiting, onManualToggle } = scope;
  const rootRef = useRef<HTMLDivElement>(null);
  // The body is drawn by the card (`header="own"`) or by the frame, so it
  // reports its element instead of taking a ref from here.
  const [body, setBody] = useState<HTMLDivElement | null>(null);

  // Register before paint, so the newest card is known in the first frame.
  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    registry.register(id, el);
    return () => registry.unregister(id);
  }, [registry, id]);

  // A card counts as the newest until the registry knows better. The server
  // render reads the same registry, which is empty there.
  const isNewest = () => {
    const n = registry.newest();
    return n === null || n === id;
  };
  const newest = useSyncExternalStore(registry.subscribe, isNewest, isNewest);

  // The body's height, measured. 0 is "not known" (a shut body measures 0).
  const [height, setHeight] = useState(0);
  useLayoutEffect(() => {
    const el = body;
    if (!el) return;
    const h = el.offsetHeight;
    if (h > 0) setHeight(h);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const next = entries[entries.length - 1]?.contentRect.height ?? 0;
      if (next > 0) setHeight(Math.round(next));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [body]);

  const [manual, setManual] = useState<ManualState | undefined>(() => manualStateOf(id));
  // A remount under a new id (rare) reads that card's own choice.
  useEffect(() => {
    setManual(manualStateOf(id));
  }, [id]);
  const [animate, setAnimate] = useState(false);
  // The focus is inside: an automatic fold waits (`lib/cardRollup.ts`).
  const [focused, setFocused] = useState(false);

  const pending = pendingProp || (!!askTarget && waiting.has(askTarget));
  const long = isLong(height, rows);
  const toggle = showRollupToggle({ pending, long });
  const open = rollupOpen({ pending, manual, newest, long, focused });

  const onOpenChange = useCallback(
    (next: boolean) => {
      const state: ManualState = next ? "open" : "closed";
      setManualState(id, state);
      setManual(state);
      setAnimate(true);
      onManualToggle?.();
    },
    [id, onManualToggle],
  );

  return (
    <div
      ref={rootRef}
      data-rollup-card={id}
      className={className}
      onFocus={() => setFocused(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false);
      }}
    >
      <CollapsibleCard
        open={open}
        onOpenChange={onOpenChange}
        toggle={toggle}
        label={title}
        icon={icon}
        count={rows}
        summary={summary}
        header={header}
        bodyRef={setBody}
        animate={animate}
      >
        {children}
      </CollapsibleCard>
    </div>
  );
}
