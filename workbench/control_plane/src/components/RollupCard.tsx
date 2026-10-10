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
 * (`InStepContext`), a card draws as it always did: no header, no fold. A
 * read in the trail is already folded by its step.
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
import { CollapsibleCard } from "@/components/ui/Collapsible";
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
  registry: RollupRegistry<Node>;
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
  className?: string;
  children: React.ReactNode;
}

export default function RollupCard(props: RollupCardProps) {
  const scope = useContext(RollupContext);
  const inStep = useContext(InStepContext);
  if (!scope || inStep) return <>{props.children}</>;
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
  className,
  children,
}: RollupCardProps & { scope: RollupScope }) {
  const { registry, waiting, onManualToggle } = scope;
  const rootRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

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
    const el = bodyRef.current;
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
  }, []);

  const [manual, setManual] = useState<ManualState | undefined>(() => manualStateOf(id));
  // A remount under a new id (rare) reads that card's own choice.
  useEffect(() => {
    setManual(manualStateOf(id));
  }, [id]);
  const [animate, setAnimate] = useState(false);

  const pending = pendingProp || (!!askTarget && waiting.has(askTarget));
  const long = isLong(height, rows);
  const toggle = showRollupToggle({ pending, long });
  const open = rollupOpen({ pending, manual, newest, long });

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
    <div ref={rootRef} data-rollup-card={id} className={className}>
      <CollapsibleCard
        open={open}
        onOpenChange={onOpenChange}
        toggle={toggle}
        label={title}
        icon={icon}
        count={rows}
        summary={summary}
        animate={animate}
      >
        <div ref={bodyRef} className="min-w-0">
          {children}
        </div>
      </CollapsibleCard>
    </div>
  );
}
