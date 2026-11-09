"use client";

/**
 * The mic of the chat composer (`projects_ai_chat.md` §31, VOX-1 and VOX-7).
 *
 * A press starts a recording. A second press, or Enter, stops it, and the box
 * shows "Transcribing…". Escape, or the cross, cancels it and keeps nothing.
 * While it records, the mic is a filled round Stop button with a ring that
 * moves with the voice. A band of three layered sine lines sits in the bar
 * row beside a timer. The draft in the box stays in view, and the box stays
 * mounted. At 120 s the recording stops by itself. The text goes to
 * `onText`, and the composer puts it in the box at the caret. A refused mic
 * or a refused clip goes to `onNotice` as one sentence.
 *
 * It sits beside Attach, inside the composer's form, so every button here is
 * `type="button"` (`lib/chatAttachments.test.ts` fences it, as it fences
 * Attach). The composer reaches `stop` and `cancel` through `ref`, so Enter
 * and Escape in the box act on a recording.
 */
import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import VoiceWave, { usePrefersReducedMotion } from "@/components/VoiceWave";
import { useVoiceDictation } from "@/hooks/useVoiceDictation";
import { formatClock, MAX_SECONDS, type VoiceAvailability, type VoicePhase } from "@/lib/voiceDictation";
import { HINT_MS, HINT_TEXT, hintSeen, markHintSeen, secondsLeft } from "@/lib/voiceWave";

export interface VoiceControls {
  stop: () => void;
  cancel: () => void;
}

/** The accessible name of the control in each phase. */
export function voiceLabel(phase: VoicePhase): string {
  switch (phase) {
    case "recording":
      return "Stop recording";
    case "transcribing":
      return "Transcribing";
    case "asking":
      return "Waiting for the microphone";
    default:
      return "Dictate a message";
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

  const { stop, cancel } = voice;
  useImperativeHandle(ref, () => ({ stop: () => void stop(), cancel }), [stop, cancel]);
  useEffect(() => {
    onPhase?.(phase);
  }, [phase, onPhase]);

  // The first recording of this browser shows "Tap ■ to finish" for 4.5 s.
  const [hint, setHint] = useState<"shown" | "fading" | null>(null);
  // The tip sits above the top edge of the composer, so it never covers the
  // box. `lift` is the distance from the button to that edge.
  const wrapRef = useRef<HTMLSpanElement>(null);
  const [lift, setLift] = useState(0);
  useEffect(() => {
    if (!recording || hintSeen()) return;
    markHintSeen();
    const show = setTimeout(() => {
      const wrap = wrapRef.current;
      const box = wrap?.closest("[data-chat-composer]");
      if (wrap && box) setLift(Math.max(0, wrap.getBoundingClientRect().top - box.getBoundingClientRect().top));
      setHint("shown");
    }, 0);
    const fade = setTimeout(() => setHint("fading"), HINT_MS);
    return () => {
      clearTimeout(show);
      clearTimeout(fade);
      setHint(null);
    };
  }, [recording]);

  const press = () => {
    if (!availability.ready) {
      onNotice(availability.message);
      return;
    }
    if (recording) void voice.stop();
    else if (phase === "idle") void voice.start();
  };
  const escape = (e: React.KeyboardEvent) => {
    if (e.key === "Escape" && (recording || phase === "asking")) {
      e.preventDefault();
      e.stopPropagation();
      voice.cancel();
    }
  };

  const title = recording
    ? "Stop recording (Enter). Esc discards."
    : !availability.ready && availability.message
      ? availability.message
      : `Dictate a message. Talk for up to ${MAX_SECONDS / 60} minutes.`;

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
        {recording && hint && (
          <span
            aria-hidden={hint === "fading"}
            data-chat-voice-hint=""
            style={{ marginBottom: lift + 10 }}
            className={`pointer-events-none absolute bottom-full left-0 z-20 flex w-full justify-center transition-opacity duration-500 motion-reduce:transition-none ${
              hint === "shown" ? "opacity-100" : "opacity-0"
            }`}
          >
            <span role="status" className="absolute bottom-1.5 left-0 whitespace-nowrap rounded-lg bg-tooltip px-2.5 py-1.5 text-xs font-medium text-tooltip-foreground shadow-lg">
              {HINT_TEXT}
            </span>
            <span className="h-2 w-2 rotate-45 bg-tooltip" />
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
          onClick={press}
          onKeyDown={escape}
          // GL-6: in glass, the mic is a round icon button on the secondary
          // fill, as Attach beside it is. While it records, it is a filled
          // round primary Stop button. The size comes from `className`.
          className={`${
            recording
              ? "rounded-full"
              : "rounded-lg p-2 glass:rounded-full glass:p-0 glass:bg-secondary glass:text-foreground glass:hover:bg-muted"
          } ${className}`}
        >
          {waiting ? (
            <Icon name="Loader2" size={16} className="animate-spin motion-reduce:animate-none" />
          ) : recording ? (
            <Icon name="Square" size={12} strokeWidth={2.5} fill="currentColor" />
          ) : (
            <Icon name="Mic" size={18} />
          )}
        </Button>
      </span>
      {recording && (
        <>
          <span className="flex shrink-0 items-center gap-1.5 text-[11px]" data-chat-voice-meter="">
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
          <Button
            type="button"
            variant="ghost"
            size="none"
            radius="keep"
            aria-label="Discard recording"
            title="Discard recording (Esc)"
            onClick={() => voice.cancel()}
            onKeyDown={escape}
            className={`shrink-0 rounded-full ${className}`}
          >
            <Icon name="X" size={16} />
          </Button>
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
