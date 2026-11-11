/**
 * The one browser seam for sound from the microphone.
 *
 * Notes and the chat's voice input both read it. Notes re-exports
 * `listMicrophones` from `app/notes/lib/recorder.ts`, so its imports do not
 * change. No app imports this from another app.
 *
 * The chat's voice input (`projects_ai_chat.md` §31, VOX-1) records a 16 kHz
 * mono WAV clip with the Web Audio API. It does not use MediaRecorder: the
 * Console measures the minutes of a WAV clip with the Python standard library
 * (VOX-2), and MediaRecorder gives WebM or MP4. Two minutes of this WAV are
 * about 3.8 MB.
 *
 * The pure parts (`levelOf`, `downsampleToInt16`, `encodeWav`) are tested in
 * `audioCapture.test.ts`. `startPcmCapture` needs a browser.
 */

export interface MicDevice {
  deviceId: string;
  label: string;
}

/** Enumerate the browser's audio-input devices (Google-Meet-style mic picker).
 *  Device labels are only populated once the user has granted mic permission at
 *  least once this session; before that they come back blank (we show a generic
 *  name and reveal real labels after the first getUserMedia). */
export async function listMicrophones(): Promise<MicDevice[]> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.enumerateDevices) {
    return [];
  }
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices
      .filter((d) => d.kind === "audioinput")
      .map((d) => ({ deviceId: d.deviceId, label: d.label }));
  } catch {
    return [];
  }
}

/** The sample rate of the chat's clip. Speech models read 16 kHz. */
export const CLIP_RATE = 16000;

/**
 * The level meter: the loudness of one block of samples, from 0 to 1.
 *
 * The root mean square, times three, so normal speech fills most of the bar.
 * It is the same scale as the Notes meter.
 */
export function levelOf(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.min(1, Math.sqrt(sum / samples.length) * 3);
}

function clamp16(v: number): number {
  const s = Math.max(-1, Math.min(1, v));
  return s < 0 ? s * 0x8000 : s * 0x7fff;
}

/** Float32 samples at `inRate` to 16-bit PCM at `outRate`. Each output sample
 *  is the mean of its source window, a cheap anti-alias. */
export function downsampleToInt16(input: Float32Array, inRate: number, outRate: number): Int16Array {
  if (inRate <= outRate) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) out[i] = clamp16(input[i]);
    return out;
  }
  const ratio = inRate / outRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = clamp16(sum / Math.max(1, end - start));
  }
  return out;
}

/** One array from the blocks the capture collected. */
export function joinBlocks(blocks: Float32Array[]): Float32Array {
  let length = 0;
  for (const b of blocks) length += b.length;
  const out = new Float32Array(length);
  let at = 0;
  for (const b of blocks) {
    out.set(b, at);
    at += b.length;
  }
  return out;
}

/**
 * A mono 16-bit PCM WAV file: the 44-byte RIFF header, then the samples,
 * little-endian. Python's `wave` module reads exactly this shape.
 */
export function encodeWav(pcm: Int16Array, sampleRate: number): Uint8Array {
  const dataBytes = pcm.length * 2;
  const buf = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buf);
  const ascii = (at: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(at + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true); // the size of the fmt chunk
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // bytes each second
  view.setUint16(32, 2, true); // bytes each frame
  view.setUint16(34, 16, true); // bits each sample
  ascii(36, "data");
  view.setUint32(40, dataBytes, true);
  for (let i = 0; i < pcm.length; i++) view.setInt16(44 + i * 2, pcm[i], true);
  return new Uint8Array(buf);
}

/** A capture that runs. `stop` gives the WAV clip, and `cancel` keeps nothing. */
export interface PcmCapture {
  stop(): Blob;
  cancel(): void;
}

/**
 * Start a capture from the microphone. It rejects with the browser's own
 * error (a `DOMException` such as `NotAllowedError`) when the mic is refused.
 *
 * A ScriptProcessor node copies each block of samples. The node feeds a
 * silent gain, so the mic never plays back through the speakers. The context
 * runs at the device's own rate, and `stop` takes the clip down to 16 kHz.
 */
export async function startPcmCapture(opts: {
  deviceId?: string | null;
  onLevel?: (level: number) => void;
}): Promise<PcmCapture> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
    throw new DOMException("No microphone API", "NotSupportedError");
  }
  const base: MediaTrackConstraints = {
    channelCount: 1,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: opts.deviceId ? { ...base, deviceId: { exact: opts.deviceId } } : base,
  });
  const AC =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  let ctx: AudioContext;
  try {
    ctx = new AC();
  } catch (err) {
    stream.getTracks().forEach((t) => t.stop());
    throw err;
  }
  let source: MediaStreamAudioSourceNode;
  let node: ScriptProcessorNode;
  let sink: GainNode;
  try {
    source = ctx.createMediaStreamSource(stream);
    node = ctx.createScriptProcessor(4096, 1, 1);
    sink = ctx.createGain();
    sink.gain.value = 0;
  } catch (err) {
    // A setup that throws must not leave the mic open.
    stream.getTracks().forEach((t) => t.stop());
    void ctx.close().catch(() => {});
    throw err;
  }
  const blocks: Float32Array[] = [];
  let open = true;
  node.onaudioprocess = (e) => {
    if (!open) return;
    const block = new Float32Array(e.inputBuffer.getChannelData(0));
    blocks.push(block);
    opts.onLevel?.(levelOf(block));
  };
  source.connect(node);
  node.connect(sink);
  sink.connect(ctx.destination);
  if (ctx.state === "suspended") await ctx.resume().catch(() => {});

  const teardown = () => {
    open = false;
    node.onaudioprocess = null;
    try {
      source.disconnect();
      node.disconnect();
      sink.disconnect();
    } catch {
      /* nodes already detached */
    }
    void ctx.close().catch(() => {});
    stream.getTracks().forEach((t) => t.stop());
  };

  return {
    stop() {
      const rate = ctx.sampleRate;
      teardown();
      // A context at or below 16 kHz keeps its own rate, and the header says so.
      const outRate = Math.min(rate, CLIP_RATE);
      const pcm = downsampleToInt16(joinBlocks(blocks), rate, outRate);
      blocks.length = 0;
      const bytes = encodeWav(pcm, outRate);
      return new Blob([bytes.buffer as ArrayBuffer], { type: "audio/wav" });
    },
    cancel() {
      teardown();
      blocks.length = 0;
    },
  };
}
