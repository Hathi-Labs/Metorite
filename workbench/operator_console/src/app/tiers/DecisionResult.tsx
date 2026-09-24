// What one "Try a decision" call answered. No hooks and no state, so
// `decide.test.ts` can call it and walk the element tree it returns.

import { costText, meteringWarning, type TryResult } from "@/lib/decide";

export default function DecisionResult({ result }: { result: TryResult }) {
  const warning = meteringWarning(result);
  return (
    <div className="try-result">
      <p className="try-answer">
        <span className="lbl">Answer</span> {result.answer}
      </p>
      <div className="stats">
        <div className="stat">
          <div className="lbl">Latency</div>
          <div className="num small-num">{result.latencyMs} ms</div>
        </div>
        <div className={warning ? "stat caution" : "stat"}>
          <div className="lbl">Input tokens</div>
          <div className="num small-num">{result.inputTokens}</div>
        </div>
        <div className="stat">
          <div className="lbl">Output tokens</div>
          <div className="num small-num">{result.outputTokens}</div>
        </div>
        <div className="stat">
          <div className="lbl">Vendor cost</div>
          <div className="num small-num mono">{costText(result.vendorCostUsd)}</div>
        </div>
      </div>
      {warning && <p className="field-hint warn">{warning}</p>}
      <p className="muted small">
        Served by <span className="mono">{result.model}</span>. One audit row
        records this call. No customer usage row does.
      </p>
    </div>
  );
}
