// The go-live rail — six steps between an empty console and a served
// customer, on the page an operator lands on.
//
// ⚠️ **A SERVER component.** Every judgement is pure (`lib/golive.ts`) and
// every fact comes from the page's own catalog read, so there is no state
// here and nothing for a client bundle to carry. It also means the rail can
// never disagree with the pages it links to — same read, same seams.

import Link from "next/link";

import type { AiCatalog } from "@/lib/contract";
import { goLiveSteps, railSummary, stepTone } from "@/lib/golive";
import { chipClass } from "@/lib/tone";

const WORD = {
  done: "done",
  partial: "nearly",
  todo: "to do",
  info: "yours",
} as const;

export default function GoLiveRail({ catalog }: { catalog: AiCatalog }) {
  const steps = goLiveSteps(catalog);
  const summary = railSummary(steps);

  if (summary) {
    return (
      <section className="rail-done">
        <span className={chipClass("ok")}>ready</span>
        <p>{summary}</p>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Going live, in order</h2>
        <p>
          The whole system on one line each: keys arm vendors, models join the
          catalog, tiers point at models, prices make calls bill, a customer
          gets a key and credits, two switches turn it on.
        </p>
      </div>
      <ol className="rail">
        {steps.map((s) => (
          <li key={s.key} className={`rail-step ${s.state}`}>
            <span className="stepno">{s.n}</span>
            <div className="railmain">
              <div className="railhead">
                <strong>{s.title}</strong>
                <span className={chipClass(stepTone(s.state))}>
                  {WORD[s.state]}
                </span>
              </div>
              <p className="muted small">{s.detail}</p>
            </div>
            <Link className="linklike" href={s.href}>
              {s.linkText}
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}
