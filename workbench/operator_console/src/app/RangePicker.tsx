// The period picker on a money page — WS-50 slice 7.
//
// 🔴 **Links and a plain GET form, no client state.** A preset is a link and
// a custom range is a form that submits to the same page. The server reads the
// URL and fetches that period, so what the page shows and what the picker
// says can never disagree, and the page works with scripts off.

import { PRESETS, presetHref, type UsageRange } from "@/lib/range";

export default function RangePicker({
  path,
  range,
  keep = {},
}: {
  path: string;
  range: UsageRange;
  /** Other URL parameters to carry, such as the customer page's tab. */
  keep?: Record<string, string>;
}) {
  return (
    <div className="rangebar">
      <nav className="segmented" aria-label="Period">
        {PRESETS.map((p) => (
          <a
            key={p.key}
            href={presetHref(path, p.key, keep)}
            aria-current={range.key === p.key ? "page" : undefined}
          >
            {p.label}
          </a>
        ))}
      </nav>
      <form method="get" action={path} className="rangeform" aria-label="Custom period">
        {Object.entries(keep).map(([k, v]) => (
          <input key={k} type="hidden" name={k} value={v} />
        ))}
        <input type="hidden" name="range" value="custom" />
        <label>
          From <input type="date" name="from" defaultValue={range.from ?? ""} required />
        </label>
        <label>
          to <input type="date" name="to" defaultValue={range.to ?? ""} />
        </label>
        <button type="submit" className="secondary" title="Show this period">
          Show
        </button>
      </form>
      {range.error && <p className="field-hint warn">{range.error} Showing the last 30 days.</p>}
    </div>
  );
}
