"use client";

/**
 * The mic of the chat composer (`projects_ai_chat.md` §31, VOX-1, VOX-7 and
 * hold to talk, owner 2026-11-21, task 2501).
 *
 * HOLD TO TALK. A press starts a recording at once. Letting go stops it, and
 * the box shows "Transcribing…". A press under 300 ms is a tap: nothing is
 * kept, and a small popover says "Hold to talk" for 2 s. A pointercancel, a
 * lost focus, Escape, or a release off the button discards the clip, and no
 * text is sent. Space and Enter hold the button too, and the key up stops.
 * While it records, a band of three layered sine lines sits in the bar row
 * beside a timer. The draft in the box stays in view, and the box stays
 * mounted. At 120 s the recording stops by itself. The text goes to
 * `onText`, and the composer puts it in the box at the caret, so a second
 * clip appends. A refused mic or a refused clip goes to `onNotice`.
 *
 * It sits beside Attach, inside the composer's form, so every button here is
 * `type="button"` (`lib/chatAttachments.test.ts` fences it, as it fences
 * Attach). The composer reaches `stop` and `cancel` through `ref`, so Enter
 * and Escape in the box act on a recording.
 */
import { useCallback, useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import VoiceWave, { usePrefersReducedMotion } from "@/components/VoiceWave";
import { useVoiceDictation } from "@/hooks/useVoiceDictation";
import { HOLD_TEXT, isHoldKey, isOffButton, POPOVER_MS, releaseOutcome } from "@/lib/holdToTalk";
import { formatClock, MAX_SECONDS, type VoiceAvailability, type VoicePhase } from "@/lib/voiceDictation";
import { secondsLeft } from "@/lib/voiceWave";

export interface VoiceControls {
  stop: () => void;
  cancel: () => void;
}

/** The accessible name of the control in each phase. */
export function voiceLabel(phase: VoicePhase): string {
  switch (phase) {
    case "recording":
      return `${HOLD_TEXT}. Release to send`;
    case "transcribing":
      return "Transcribing";
    case "asking":
      return "Waiting for the microphone";
    default:
      return HOLD_TEXT;
  }
}

export default function VoiceInputButton({
  availability,
  onText,
  onNotice,
  onPhase,
  className = "",
  ref,
}: {
  availability: VoiceAvailability;
  onText: (text: string) => void;
  onNotice: (sentence: string | null) => void;
  /** Called when the phase changes, never on a level tick. */
  onPhase?: (phase: VoicePhase) => void;
  /** The tap size, from the composer (`h-8 w-8`, or `h-11 w-11` on a phone). */
  className?: string;
  ref?: Ref<VoiceControls>;
}) {
  const voice = useVoiceDictation({ onText, onNotice });
  const { phase } = voice.state;
  const recording = phase === "recording";
  const transcribing = phase === "transcribing";
  const waiting = phase === "asking" || transcribing;
  const reduced = usePrefersReducedMotion();
  const ringRef = useRef<HTMLSpanElement>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);

  // The press that is held now: when it began, and the pointer that holds it
  // (null for a key).
  const held = useRef<{ at: number; pointerId: number | null } | null>(null);
  const { stop, cancel } = voice;
  useImperativeHandle(
    ref,
    () => ({
      stop: () => {
        held.current = null;
        void stop();
      },
      cancel: () => {
        held.current = null;
        cancel();
      },
    }),
    [stop, cancel],
  );
  useEffect(() => {
    onPhase?.(phase);
  }, [phase, onPhase]);

  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  // The "Hold to talk" popover: 2 s, or the next click elsewhere.
  const [popover, setPopover] = useState(false);
  useEffect(() => {
    if (!popover) return;
    const timer = setTimeout(() => setPopover(false), POPOVER_MS);
    const away = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setPopover(false);
    };
    document.addEventListener("pointerdown", away);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("pointerdown", away);
    };
  }, [popover]);

  // A mouse press does not focus the button, so Escape reaches the page and
  // not the button. While a press is held, a page-level Escape discards it.
  const holding = recording || phase === "asking";
  useEffect(() => {
    if (!holding) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      held.current = null;
      cancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [holding, cancel]);

  const begin = useCallback(
    (pointerId: number | null) => {
      if (held.current) return;
      if (!availability.ready) {
        onNotice(availability.message);
        return;
      }
      if (phaseRef.current !== "idle") return;
      held.current = { at: Date.now(), pointerId };
      setPopover(false);
      void voice.start();
    },
    [availability, onNotice, voice],
  );

  const end = useCallback(
    (cancelled: boolean) => {
      const press = held.current;
      if (!press) return;
      held.current = null;
      const outcome = releaseOutcome({
        heldMs: Date.now() - press.at,
        cancelled,
        recording: phaseRef.current === "recording",
      });
      if (outcome === "stop") {
        void voice.stop();
        return;
      }
      voice.cancel();
      if (outcome === "tap") setPopover(true);
    },
    [voice],
  );

  const escape = (e: React.KeyboardEvent) => {
    if (e.key === "Escape" && (recording || phase === "asking")) {
      e.preventDefault();
      e.stopPropagation();
      held.current = null;
      voice.cancel();
    }
  };

  const title = recording
    ? "Release to send. Drag off the button or press Esc to discard."
    : !availability.ready && availability.message
      ? availability.message
      : `${HOLD_TEXT}. Talk for up to ${MAX_SECONDS / 60} minutes.`;

  const left = recording ? secondsLeft(voice.elapsed) : null;
  const clock = formatClock(voice.elapsed);

  return (
    // While it records or transcribes, the cluster takes the room that the
    // pickers leave (`basis-0`), so the bar never wraps to a second line.
    <span
      className={waiting || recording ? "flex min-w-fit flex-[3] basis-0 items-center gap-1.5" : "flex shrink-0 items-center gap-1.5"}
      data-chat-voice={phase}
    >
      <span ref={wrapRef} className="relative flex shrink-0">
        {recording && (
          <span
            ref={ringRef}
            aria-hidden="true"
            data-chat-voice-ring=""
            className="pointer-events-none absolute -inset-1 rounded-full border-2 border-primary opacity-30"
          />
        )}
        {popover && (
          <span
            role="status"
            data-chat-voice-popover=""
            className="pointer-events-none absolute bottom-full left-0 z-20 mb-2 whitespace-nowrap rounded-lg bg-tooltip px-2.5 py-1.5 text-xs font-medium text-tooltip-foreground shadow-lg"
          >
            {HOLD_TEXT}
            <span aria-hidden="true" className="absolute -bottom-1 left-3 h-2 w-2 rotate-45 bg-tooltip" />
          </span>
        )}
        <Button
          type="button"
          variant={recording ? "primary" : "ghost"}
          size="none"
          radius="keep"
          disabled={transcribing}
          aria-label={voiceLabel(phase)}
          aria-busy={waiting || undefined}
          title={title}
          // The press is the pointer or the key. A click that has no press
          // before it comes from a screen reader, so it shows the popover.
          onClick={(e) => {
            if (e.detail === 0 && !held.current && phase === "idle") setPopover(true);
          }}
          onPointerDown={(e) => {
            if (e.pointerType === "mouse" && e.button !== 0) return;
            e.currentTarget.setPointerCapture?.(e.pointerId);
            begin(e.pointerId);
          }}
          onPointerUp={(e) => {
            // Only the pointer that began the hold ends it.
            if (held.current?.pointerId !== e.pointerId) return;
            const off = isOffButton(e.clientX, e.clientY, e.currentTarget.getBoundingClientRect(), 4);
            end(off);
          }}
          onPointerCancel={() => end(true)}
          onContextMenu={(e) => e.preventDefault()}
          onKeyDown={(e) => {
            escape(e);
            if (!isHoldKey(e.key)) return;
            // Space and Enter would click the button. A hold is not a click.
            e.preventDefault();
            if (e.repeat) return;
            begin(null);
          }}
          onKeyUp={(e) => {
            if (!isHoldKey(e.key)) return;
            e.preventDefault();
            if (held.current?.pointerId === null) end(false);
          }}
          onBlur={() => end(true)}
          // GL-6: in glass, the mic is a round icon button on the secondary
          // fill, as Attach beside it is. While it records, it is a filled
          // round primary button. The size comes from `className`.
          // Touch: no long-press menu, no text selection, no scroll start.
          style={{ touchAction: "none", WebkitTouchCallout: "none" }}
          className={`touch-none select-none ${
            recording
              ? "rounded-full"
              : "rounded-lg p-2 glass:rounded-full glass:p-0 glass:bg-secondary glass:text-foreground glass:hover:bg-muted"
          } ${className}`}
        >
          {waiting ? (
            <Icon name="Loader2" size={16} className="animate-spin motion-reduce:animate-none" />
          ) : (
            <Icon name="Mic" size={recording ? 16 : 18} />
          )}
        </Button>
      </span>
      {recording && (
        <>
          <span className="ml-2.5 flex shrink-0 items-center gap-1.5 text-[11px]" data-chat-voice-meter="">
            <span
              className={`font-mono tabular-nums ${left !== null ? "font-semibold text-warning" : "text-foreground"}`}
              aria-label={left !== null ? `Recording, ${clock}, ${left} seconds left` : `Recording, ${clock}`}
            >
              {clock}
            </span>
            {left !== null && (
              <span aria-hidden="true" className="hidden whitespace-nowrap text-warning sm:inline" data-chat-voice-left="">
                {left} s left
              </span>
            )}
          </span>
          <VoiceWave levelRef={voice.levelRef} reduced={reduced} ringRef={ringRef} />
        </>
      )}
      {transcribing && (
        <span role="status" className="truncate text-xs text-muted-foreground" data-chat-voice-status="">
          Transcribing…
        </span>
      )}
    </span>
  );
}
