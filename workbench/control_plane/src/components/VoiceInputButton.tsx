"use client";

/**
 * The mic of the chat composer (`projects_ai_chat.md` §31, VOX-1).
 *
 * A press starts a recording. A second press, or Enter, stops it, and the box
 * shows "Transcribing…". Escape cancels it and keeps nothing. While it
 * records, it shows a timer and a level meter. At 120 s it stops by itself.
 * The text goes to `onText`, and the composer puts it in the box at the
 * caret. A refused mic or a refused clip goes to `onNotice` as one sentence.
 *
 * It sits beside Attach, inside the composer's form, so every button here is
 * `type="button"` (`lib/chatAttachments.test.ts` fences it, as it fences
 * Attach). The composer reaches `stop` and `cancel` through `ref`, so Enter
 * and Escape in the box act on a recording.
 */
import { useEffect, useImperativeHandle, type Ref } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useVoiceDictation } from "@/hooks/useVoiceDictation";
import { formatClock, MAX_SECONDS, type VoiceAvailability, type VoicePhase } from "@/lib/voiceDictation";

export interface VoiceControls {
  stop: () => void;
  cancel: () => void;
}

/** The accessible name of the control in each phase. */
export function voiceLabel(phase: VoicePhase): string {
  switch (phase) {
    case "recording":
      return "Stop recording and transcribe";
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
  const { phase, level } = voice.state;
  const recording = phase === "recording";
  const waiting = phase === "asking" || phase === "transcribing";

  const { stop, cancel } = voice;
  useImperativeHandle(ref, () => ({ stop: () => void stop(), cancel }), [stop, cancel]);
  useEffect(() => {
    onPhase?.(phase);
  }, [phase, onPhase]);

  const press = () => {
    if (!availability.ready) {
      onNotice(availability.message);
      return;
    }
    if (recording) void voice.stop();
    else if (phase === "idle") void voice.start();
  };

  const title = recording
    ? "Stop and transcribe (Enter). Esc cancels."
    : !availability.ready && availability.message
      ? availability.message
      : `Dictate a message. Talk for up to ${MAX_SECONDS / 60} minutes.`;

  return (
    <span className="flex shrink-0 items-center gap-1.5" data-chat-voice={phase}>
      <Button
        type="button"
        variant="ghost"
        size="none"
        radius="keep"
        selected={recording}
        disabled={phase === "transcribing"}
        aria-label={voiceLabel(phase)}
        aria-busy={waiting || undefined}
        title={title}
        onClick={press}
        onKeyDown={(e) => {
          if (e.key === "Escape" && (recording || phase === "asking")) {
            e.preventDefault();
            e.stopPropagation();
            voice.cancel();
          }
        }}
        // GL-6: in glass, the mic is a round icon button on the secondary
        // fill, as Attach beside it is. The size comes from `className`.
        className={`rounded-lg p-2 glass:rounded-full glass:p-0 ${
          recording ? "glass:bg-primary/12 glass:text-primary" : "glass:bg-secondary glass:text-foreground glass:hover:bg-muted"
        } ${className}`}
      >
        {waiting ? (
          <Icon name="Loader2" size={16} className="animate-spin" />
        ) : recording ? (
          <Icon name="Square" size={12} strokeWidth={2.5} fill="currentColor" />
        ) : (
          <Icon name="Mic" size={18} />
        )}
      </Button>
      {recording && (
        <span className="ml-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground" data-chat-voice-meter="">
          <span className="font-mono tabular-nums text-foreground" aria-label={`Recording, ${formatClock(voice.elapsed)}`}>
            {formatClock(voice.elapsed)}
          </span>
          <span aria-hidden="true" className="relative h-1.5 w-10 overflow-hidden rounded-full bg-muted">
            <span
              className="absolute inset-0 origin-left rounded-full bg-primary"
              style={{ transform: `scaleX(${Math.max(0.04, Math.min(1, level))})` }}
            />
          </span>
        </span>
      )}
      {phase === "transcribing" && (
        <span role="status" className="text-[11px] text-muted-foreground" data-chat-voice-status="">
          Transcribing…
        </span>
      )}
    </span>
  );
}
