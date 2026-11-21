/**
 * UnsavedMark — the mark of a buffer that holds edits not saved yet (STU-G3c).
 *
 * The App Workshop page drew a bare warning dot for it. The Studio IDE needs the same mark with
 * words, because a member must see which tab holds the edit. One component draws both:
 * `<UnsavedMark />` is the dot with a title, and `<UnsavedMark label />` adds "Unsaved changes".
 */
export default function UnsavedMark({ label = false }: { label?: boolean }) {
  return (
    <span title="Unsaved changes" data-unsaved-mark className="inline-flex shrink-0 items-center gap-1.5">
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-warning" />
      {label ? <span role="status" className="text-xs font-medium text-warning">Unsaved changes</span> : <span className="sr-only">Unsaved changes</span>}
    </span>
  );
}
