// ── When the editor writes its Markdown (WS-61 KB-2b fix round) ────────────
//
// Pure: the timer and the write come in from the editor. A key only marks
// the body pending and starts the pause. A flush writes the body at once.
//
// ⚠️ A SILENT flush returns the body and leaves it pending. It stores the
// keys for a new view of the page (`lib/liveDrafts.ts`), and it sends them
// to no page state. If the old view stays on screen, its next flush or the
// pause still sends them. A silent flush that cleared the pending mark let
// a later Save write a draft without those keys (review of the fix round).

export interface BodyWriterOptions {
  /** Write the editor's document as the body a save would write. */
  serialize: () => string | null;
  /** Hand the body to the page. */
  onChange: (body: string) => void;
  /** The first key after a send. */
  onTouched?: () => void;
  /** How long to wait after the last key. */
  delayMs: number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export interface BodyWriter {
  /** A key changed the document. Cheap: no write. */
  touch(): void;
  /** Write now. A silent flush sends nothing and keeps the body pending. */
  flush(opts?: { silent?: boolean }): string | null;
  /** True while keys wait for a send. */
  pending(): boolean;
  /** Stop the pause timer. */
  dispose(): void;
}

export function createBodyWriter(opts: BodyWriterOptions): BodyWriter {
  const setTimer = opts.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms));
  const clearTimer = opts.clearTimer ?? ((h: unknown) => clearTimeout(h as ReturnType<typeof setTimeout>));
  let pending = false;
  let timer: unknown = null;

  const stop = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
  };

  const writer: BodyWriter = {
    touch() {
      if (!pending) opts.onTouched?.();
      pending = true;
      stop();
      timer = setTimer(() => writer.flush(), opts.delayMs);
    },
    flush(flushOpts) {
      if (!pending) return null;
      const body = opts.serialize();
      if (body === null) return null;
      if (flushOpts?.silent) return body;
      stop();
      pending = false;
      opts.onChange(body);
      return body;
    },
    pending: () => pending,
    dispose: stop,
  };
  return writer;
}
