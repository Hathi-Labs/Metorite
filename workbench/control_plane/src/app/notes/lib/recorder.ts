/**
 * MeetingRecorder — browser mic capture → chunked upload to the /notes API.
 *
 * MediaRecorder emits timeslice blobs that are parts of ONE continuous
 * container, so the server appends them in order to reconstruct the file. Each
 * blob is uploaded sequentially with retry+backoff; unacked blobs stay queued
 * in memory (the offline buffer), and stop() flushes the queue before
 * finalizing. Spec: note_taker_app.md §3.3 / §6 (slice 1).
 */

import { completeRecording, startRecording, uploadChunk } from "./api";
import { LiveTranscription, type LiveCaption } from "./live";

export type RecorderState = "idle" | "recording" | "paused" | "finalizing";

export interface RecorderCallbacks {
  onState?: (s: RecorderState) => void;
  onElapsed?: (seconds: number) => void;
  onLevel?: (level0to1: number) => void;
  /** Count of blobs captured but not yet acked by the server. */
  onBacklog?: (pending: number) => void;
  onError?: (message: string) => void;
  /** A live caption arrived (Deepgram streaming) — best-effort, may never fire. */
  onCaption?: (c: LiveCaption) => void;
  /** Live captions aren't available; recording continues via the batch path. */
  onLiveUnavailable?: (reason: string) => void;
  /** The page was hidden (screen off / app switched) or shown again while
   *  recording. Mobile browsers suspend capture in the background, so the UI
   *  uses this to warn the user to keep the app open. */
  onVisibility?: (hidden: boolean) => void;
}

// The mic list lives in the one audio seam (`lib/audioCapture.ts`, VOX-1), so
// Notes and the chat's voice input read one copy. Re-exported, so the Notes
// imports do not change.
export { listMicrophones, type MicDevice } from "@/lib/audioCapture";

/** Pick the best MediaRecorder mime the browser supports (Chromium/FF → webm,
 *  Safari → mp4). */
export function pickMimeType(): string {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  const MR = typeof MediaRecorder !== "undefined" ? MediaRecorder : null;
  for (const c of candidates) {
    if (MR && MR.isTypeSupported(c)) return c;
  }
  return "audio/webm";
}

const CHUNK_MS = 5000;
const MAX_RETRIES = 6;

export interface MeetingRecorderOptions {
  /** Preferred mic (from listMicrophones). Falls back to the system default if
   *  the device is gone by the time we record (unplugged / switched). */
  deviceId?: string | null;
}

export class MeetingRecorder {
  private meetingId: string;
  private cb: RecorderCallbacks;
  private opts: MeetingRecorderOptions;
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private rafId = 0;
  private recordingId = "";
  private mime = "audio/webm";

  // Live captions (Deepgram streaming) — additive; failures never affect the
  // chunked-upload / batch path below.
  private live: LiveTranscription | null = null;
  /** Pending retry after live was declined (copilot off). Cleared on stop. */
  private liveRetry: ReturnType<typeof setTimeout> | null = null;
  private liveNode: ScriptProcessorNode | null = null;
  private liveSource: MediaStreamAudioSourceNode | null = null;
  private liveSink: GainNode | null = null;

  private seq = 0;
  private queue: { seq: number; blob: Blob }[] = [];
  private pumping = false;
  private startedAt = 0;
  private pausedAccum = 0;
  private pausedAt = 0;
  private timerId: ReturnType<typeof setInterval> | null = null;
  private stopping = false;

  // Screen Wake Lock — keeps the phone from auto-locking mid-recording (the
  // browser suspends mic capture when the screen sleeps). Best-effort: the OS
  // auto-releases it when the page hides, so we re-acquire on return.
  private wakeLock: WakeLockSentinel | null = null;
  private visHandler: (() => void) | null = null;

  constructor(
    meetingId: string,
    cb: RecorderCallbacks = {},
    opts: MeetingRecorderOptions = {}
  ) {
    this.meetingId = meetingId;
    this.cb = cb;
    this.opts = opts;
  }

  async start(): Promise<void> {
    const base: MediaTrackConstraints = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    const deviceId = this.opts.deviceId;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        // `exact` so a stale saved device errors instead of silently falling
        // back to the wrong mic — we handle that fallback explicitly below.
        audio: deviceId ? { ...base, deviceId: { exact: deviceId } } : base,
      });
    } catch (e) {
      // Chosen mic vanished (common on Windows when a headset is unplugged) —
      // retry on the system default rather than failing the recording outright.
      const name = e instanceof DOMException ? e.name : "";
      if (deviceId && (name === "OverconstrainedError" || name === "NotFoundError")) {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: base });
      } else {
        throw e;
      }
    }
    this.mime = pickMimeType();
    const { recording_id } = await startRecording(
      this.meetingId,
      "mic",
      this.mime.split(";")[0]
    );
    this.recordingId = recording_id;

    this.setupMeter();
    this.recorder = new MediaRecorder(this.stream, { mimeType: this.mime });
    this.recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) {
        this.queue.push({ seq: this.seq++, blob: e.data });
        this.cb.onBacklog?.(this.queue.length);
        void this.pump();
      }
    };
    this.recorder.onerror = () =>
      this.cb.onError?.("Recording error — the browser stopped the recorder.");
    this.recorder.start(CHUNK_MS);

    this.startedAt = Date.now();
    this.timerId = setInterval(() => this.cb.onElapsed?.(this.elapsed()), 250);
    this.cb.onState?.("recording");

    // Keep the screen awake and watch for backgrounding so a long recording
    // survives as long as the app stays open (spec §5.1: mobile resilience).
    void this.acquireWakeLock();
    this.setupVisibility();

    // Attach live captions without blocking the recording start — if the token
    // or socket isn't available, the batch path is unaffected.
    void this.setupLive();
  }

  /** Ask the OS to keep the screen on while recording. No-op / silent on
   *  browsers without the Wake Lock API (older iOS, Firefox) or if denied. */
  private async acquireWakeLock(): Promise<void> {
    try {
      if (
        typeof navigator !== "undefined" &&
        "wakeLock" in navigator &&
        typeof document !== "undefined" &&
        document.visibilityState === "visible"
      ) {
        this.wakeLock = await navigator.wakeLock.request("screen");
        // The browser drops the sentinel when the page hides; clear our ref so
        // the visibility handler knows to re-request on return.
        this.wakeLock.addEventListener?.("release", () => {
          this.wakeLock = null;
        });
      }
    } catch {
      // Best-effort — recording proceeds without it.
      this.wakeLock = null;
    }
  }

  private releaseWakeLock(): void {
    try {
      void this.wakeLock?.release();
    } catch {
      /* already released */
    }
    this.wakeLock = null;
  }

  /** Watch page visibility: when the app is hidden (screen off / switched away)
   *  mobile browsers suspend capture, so force out the buffered audio first and
   *  tell the UI; re-acquire the wake lock when we come back. */
  private setupVisibility(): void {
    if (typeof document === "undefined") return;
    this.visHandler = () => {
      const hidden = document.visibilityState !== "visible";
      this.cb.onVisibility?.(hidden);
      if (hidden) {
        // We're about to be frozen — flush whatever the encoder has buffered so
        // it uploads before suspension, minimising truncation if capture stops.
        try {
          if (this.recorder?.state === "recording") this.recorder.requestData();
        } catch {
          /* requestData unsupported mid-stream — ignore */
        }
        void this.pump();
        this.wakeLock = null; // auto-released by the browser when hidden
      } else if (this.recorder?.state === "recording") {
        void this.acquireWakeLock();
      }
    };
    document.addEventListener("visibilitychange", this.visHandler);
  }

  /** Try live again shortly. Only ever scheduled when live was *declined*, so
   *  this costs nothing while the copilot stays off — one small request per
   *  interval, versus a per-minute ASR bill. */
  private scheduleLiveRetry(): void {
    if (this.stopping || this.liveRetry !== null) return;
    this.liveRetry = setTimeout(() => {
      this.liveRetry = null;
      if (!this.stopping && !this.live) void this.setupLive();
    }, 30_000);
  }

  /** Best-effort live captions: tap the audio graph and stream PCM to the
   *  configured STT provider (AssemblyAI, or Deepgram as fallback). */
  private async setupLive(): Promise<void> {
    if (!this.audioCtx || !this.stream || this.stopping) return;
    const live = new LiveTranscription({
      onCaption: (c) => this.cb.onCaption?.(c),
      onUnavailable: (r) => this.cb.onLiveUnavailable?.(r),
    });
    const ok = await live.start(this.meetingId);
    if (!ok || this.stopping || !this.audioCtx || !this.stream) {
      live.stop();
      // Refused because nothing is reading the stream (copilot off) — retry
      // periodically so switching the copilot ON mid-meeting starts captions
      // rather than requiring a restart. Recording is unaffected either way.
      this.scheduleLiveRetry();
      return;
    }
    this.live = live;
    try {
      const ctx = this.audioCtx;
      const source = ctx.createMediaStreamSource(this.stream);
      const node = ctx.createScriptProcessor(4096, 1, 1);
      const sink = ctx.createGain();
      sink.gain.value = 0; // never play the mic back through the speakers
      node.onaudioprocess = (e) => {
        // Only stream while actually recording (not paused).
        if (this.recorder?.state !== "recording") return;
        this.live?.send(e.inputBuffer.getChannelData(0), ctx.sampleRate);
      };
      source.connect(node);
      node.connect(sink);
      sink.connect(ctx.destination);
      this.liveSource = source;
      this.liveNode = node;
      this.liveSink = sink;
    } catch (e) {
      this.cb.onLiveUnavailable?.(String(e instanceof Error ? e.message : e));
      live.stop();
      this.live = null;
    }
  }

  pause(): void {
    if (this.recorder?.state === "recording") {
      this.recorder.pause();
      this.pausedAt = Date.now();
      // Let the screen sleep while paused — nothing is being captured.
      this.releaseWakeLock();
      this.cb.onState?.("paused");
    }
  }

  resume(): void {
    if (this.recorder?.state === "paused") {
      this.pausedAccum += Date.now() - this.pausedAt;
      this.pausedAt = 0;
      this.recorder.resume();
      void this.acquireWakeLock();
      this.cb.onState?.("recording");
    }
  }

  /** Stop capture, flush every queued chunk, then finalize + kick transcription. */
  async stop(): Promise<void> {
    if (this.stopping) return;
    this.stopping = true;
    if (this.liveRetry !== null) {
      clearTimeout(this.liveRetry);
      this.liveRetry = null;
    }
    this.cb.onState?.("finalizing");
    const durationS = this.elapsed();

    // Wait for MediaRecorder's final ondataavailable before draining.
    await new Promise<void>((resolve) => {
      if (!this.recorder || this.recorder.state === "inactive") return resolve();
      this.recorder.onstop = () => resolve();
      this.recorder.stop();
    });
    this.teardownCapture();

    await this.pump(); // drain remaining chunks (with retry)
    if (this.queue.length > 0) {
      this.cb.onError?.(
        `${this.queue.length} audio chunk(s) failed to upload — the recording may be truncated.`
      );
    }
    await completeRecording(this.meetingId, this.recordingId, durationS);
    this.cb.onState?.("idle");
  }

  /** Abandon: stop capture without finalizing (user cancelled). */
  cancel(): void {
    this.stopping = true;
    if (this.liveRetry !== null) {
      clearTimeout(this.liveRetry);
      this.liveRetry = null;
    }
    try {
      if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    } catch {
      /* already stopped */
    }
    this.teardownCapture();
    this.queue = [];
  }

  private elapsed(): number {
    if (!this.startedAt) return 0;
    const paused =
      this.pausedAccum + (this.pausedAt ? Date.now() - this.pausedAt : 0);
    return Math.max(0, (Date.now() - this.startedAt - paused) / 1000);
  }

  private async pump(): Promise<void> {
    if (this.pumping) return;
    this.pumping = true;
    try {
      while (this.queue.length > 0) {
        const item = this.queue[0];
        const ok = await this.uploadWithRetry(item.seq, item.blob);
        if (!ok) break; // keep it queued; a later pump() retries
        this.queue.shift();
        this.cb.onBacklog?.(this.queue.length);
      }
    } finally {
      this.pumping = false;
    }
  }

  private async uploadWithRetry(seq: number, blob: Blob): Promise<boolean> {
    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      try {
        await uploadChunk(this.meetingId, this.recordingId, seq, blob);
        return true;
      } catch {
        // Exponential backoff (250ms → 8s), capped — rides out wifi blips.
        const wait = Math.min(250 * 2 ** attempt, 8000);
        await new Promise((r) => setTimeout(r, wait));
      }
    }
    return false;
  }

  private setupMeter(): void {
    try {
      const AC =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext;
      this.audioCtx = new AC();
      const src = this.audioCtx.createMediaStreamSource(this.stream!);
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 512;
      src.connect(this.analyser);
      const buf = new Uint8Array(this.analyser.frequencyBinCount);
      const tick = () => {
        if (!this.analyser) return;
        this.analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) {
          const c = (v - 128) / 128;
          sum += c * c;
        }
        this.cb.onLevel?.(Math.min(1, Math.sqrt(sum / buf.length) * 3));
        this.rafId = requestAnimationFrame(tick);
      };
      tick();
    } catch {
      /* metering is best-effort */
    }
  }

  private teardownCapture(): void {
    if (this.timerId) clearInterval(this.timerId);
    this.timerId = null;
    if (this.rafId) cancelAnimationFrame(this.rafId);
    this.rafId = 0;
    this.analyser = null;
    // Stop keeping the screen awake and unhook the visibility watcher.
    if (this.visHandler && typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this.visHandler);
    }
    this.visHandler = null;
    this.releaseWakeLock();
    // Tear down the live path before the context closes.
    try {
      if (this.liveNode) this.liveNode.onaudioprocess = null;
      this.liveNode?.disconnect();
      this.liveSource?.disconnect();
      this.liveSink?.disconnect();
    } catch {
      /* nodes already detached */
    }
    this.liveNode = null;
    this.liveSource = null;
    this.liveSink = null;
    this.live?.stop();
    this.live = null;
    void this.audioCtx?.close().catch(() => {});
    this.audioCtx = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }
}
