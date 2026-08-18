"use client";

/**
 * The self-serve checkout — SC-4a's LAUNCH slice, the ₹0 / discount path.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a launch done-whens
 * 1, 2, 5a, 6, 7, 8, 9 · SC-4g · `customer_console.md` §6 CP-9 §9.3 / §9.3a.
 *
 * The journey, end to end:
 *
 *   pick package → order created (`POST /billing/orders`) → enter code
 *   (`POST /billing/orders/{id}/redeem`) → 100% redemption fulfils → done,
 *
 * with the result read back through `GET /billing/orders/{id}`.
 *
 * ## What this surface deliberately cannot do
 *
 * **It never hands off to a payment provider, and it never constructs a
 * payment link.** There is no browser→provider hand-off anywhere in the tree
 * (SC-4a finding F-A): `create_order` posts to Razorpay's Orders API, which
 * returns no link, and the customer read strips every provider identifier by
 * design. Checkout's two required values — the publishable `key_id` and the
 * provider `order_id` — are exposed by no route. That work is **SC-4h**, gated
 * behind `work_plan.md` §6(b)'s Razorpay account. So a remaining balance is
 * reported honestly here and nothing pretends it can be paid on this screen.
 *
 * **It never computes a total.** Prices are integer paise from the Console and
 * are formatted, never summed (§9.2). Every figure below the picker is a field
 * of the order the Console priced, re-read from the server.
 *
 * **It never names a price.** Done-when 1 forbids a hard-coded ladder in
 * TypeScript — a price change is a database row (D23/D24's rule), so a surface
 * that transcribed one would be a second source of truth for money. The ladder
 * is whatever `GET /billing/catalog` returned. Fenced by
 * `lib/checkout.test.ts`'s source scan over this file.
 *
 * ## Done-when 5a, which is the rule most easily lost in a refactor
 *
 * The order is **re-read before every act and after every one**. `applyCode`
 * re-reads first, refuses on a terminal order without calling redeem, and
 * re-reads again afterwards **whatever the response was** — a refusal is about
 * the row on screen, and a stale row after a 409 reads as success. Nothing here
 * acts on an order object captured in state before a write.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { Input } from "@/components/ui/Input";
import { categoricalAccent } from "@/lib/categorical";

import {
  type CatalogPlan,
  type Order,
  type Refusal,
  type Selection,
  basketLines,
  formatPaise,
  orderState,
  redeemRefusal,
  terminalCopy,
} from "./lib/checkout";

type Step = "pick" | "code" | "done";

async function readJson(res: Response): Promise<unknown> {
  return res.json().catch(() => null);
}

export default function Checkout() {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<Step>("pick");
  const [plans, setPlans] = useState<CatalogPlan[] | null>(null);
  const [selection, setSelection] = useState<Selection>({});
  const [order, setOrder] = useState<Order | null>(null);
  const [code, setCode] = useState("");
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const codeField = useRef<HTMLInputElement>(null);

  const loadCatalog = useCallback(async () => {
    setError("");
    try {
      const res = await fetch("/api/billing/catalog", { cache: "no-store" });
      const body = (await readJson(res)) as
        | { plans?: CatalogPlan[]; detail?: string }
        | null;
      if (!res.ok) {
        throw new Error(body?.detail ?? `Could not load packages (${res.status})`);
      }
      // Rendered in the order the server sent them: `sort_order` is the
      // catalog's own decision and re-sorting here would be a second one.
      setPlans(body?.plans ?? []);
    } catch (e) {
      setPlans([]);
      setError(e instanceof Error ? e.message : "Could not load packages.");
    }
  }, []);

  useEffect(() => {
    if (!open || plans !== null) return;
    void loadCatalog();
  }, [open, plans, loadCatalog]);

  function reset() {
    setStep("pick");
    setSelection({});
    setOrder(null);
    setCode("");
    setRefusal(null);
    setError("");
  }

  function close() {
    setOpen(false);
    // A completed purchase must not leave a stale "done" panel behind the next
    // time the dialog opens; a half-finished one must not leave an order the
    // customer can no longer see the state of.
    reset();
  }

  const setQuantity = (slug: string, next: number) =>
    setSelection((current) => ({ ...current, [slug]: Math.max(0, next) }));

  const chosen = plans ? basketLines(plans, selection) : [];

  /** done-when 2 — one order, and it changes nothing else. */
  async function createOrder() {
    if (chosen.length === 0 || busy) return;
    setBusy(true);
    setError("");
    setRefusal(null);
    try {
      const res = await fetch("/api/billing/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lines: chosen }),
      });
      const body = await readJson(res);
      if (!res.ok) {
        setRefusal(redeemRefusal(res.status, body));
        return;
      }
      setOrder(body as Order);
      setStep("code");
    } catch {
      setError("Billing is temporarily unavailable.");
    } finally {
      setBusy(false);
    }
  }

  /** The one re-read, used before an act and after it (done-when 5a). */
  async function reread(id: string): Promise<Order | null> {
    const res = await fetch(`/api/billing/orders/${encodeURIComponent(id)}`, {
      cache: "no-store",
    });
    const body = await readJson(res);
    if (!res.ok) {
      setRefusal(redeemRefusal(res.status, body));
      return null;
    }
    const fresh = body as Order;
    setOrder(fresh);
    return fresh;
  }

  async function applyCode() {
    if (!order || !code.trim() || busy) return;
    setBusy(true);
    setError("");
    setRefusal(null);
    try {
      // 1. BEFORE the act. Never the order held in state: a page left open
      //    past `expires_at` is looking at a status the server is about to
      //    change, and acting on it is the stale-order bug 5a exists to name.
      const before = await reread(order.id);
      if (!before) return;
      const state = orderState(before, Date.now());
      if (state.terminal) {
        setRefusal({
          kind: "order_not_open",
          message: terminalCopy(state.reason ?? "expired"),
        });
        return;
      }

      const res = await fetch(
        `/api/billing/orders/${encodeURIComponent(order.id)}/redeem`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: code.trim() }),
        },
      );
      const body = await readJson(res);
      const failure = res.ok ? null : redeemRefusal(res.status, body);

      // 2. AFTER the act — whatever the answer was. A refusal is about the row
      //    on screen, and a stale row after a 409 reads as success.
      const after = await reread(order.id);
      if (failure) {
        setRefusal(failure);
        return;
      }
      if (after && orderState(after, Date.now()).completed) {
        setCode("");
        setStep("done");
      }
    } catch {
      setError("Billing is temporarily unavailable.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button size="lg" icon="Plus" onClick={() => setOpen(true)}>
        Buy a package
      </Button>

      <Modal
        open={open}
        onClose={close}
        title="Buy a package"
        icon="ShoppingCart"
        size="xl"
        description="Choose what your organization needs. You will see the exact amount before anything is charged."
        initialFocus={step === "code" ? codeField : undefined}
        className="max-h-[85vh] flex flex-col"
      >
        <div className="flex-1 overflow-y-auto">
          {error ? (
            <div className="mb-4 flex flex-col items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/10 p-3">
              <p className="text-sm text-destructive">{error}</p>
              <Button variant="secondary" size="sm" onClick={() => void loadCatalog()}>
                Try again
              </Button>
            </div>
          ) : null}

          {step === "pick" ? (
            <PackagePicker
              plans={plans}
              selection={selection}
              onQuantity={setQuantity}
            />
          ) : null}

          {step !== "pick" && order ? (
            <OrderPanel order={order} />
          ) : null}

          {step === "code" && order ? (
            <CodeEntry
              code={code}
              onCode={setCode}
              onApply={() => void applyCode()}
              busy={busy}
              fieldRef={codeField}
            />
          ) : null}

          {step === "done" && order ? <Completed order={order} /> : null}

          {refusal ? (
            <p className="mt-3 rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
              {refusal.message}
            </p>
          ) : null}
        </div>

        <div className="mt-4 flex shrink-0 items-center justify-end gap-2 border-t border-border pt-3">
          {step === "pick" ? (
            <>
              <Button variant="secondary" onClick={close}>
                Cancel
              </Button>
              <Button
                onClick={() => void createOrder()}
                loading={busy}
                disabled={chosen.length === 0}
              >
                Continue
              </Button>
            </>
          ) : (
            <Button variant="secondary" onClick={close}>
              {step === "done" ? "Done" : "Close"}
            </Button>
          )}
        </div>
      </Modal>
    </>
  );
}

// ── Step 1: the ladder ──────────────────────────────────────────────────────

function PackagePicker({
  plans,
  selection,
  onQuantity,
}: {
  plans: CatalogPlan[] | null;
  selection: Selection;
  onQuantity: (slug: string, next: number) => void;
}) {
  if (plans === null) {
    return <p className="text-sm text-muted-foreground">Loading packages…</p>;
  }
  if (plans.length === 0) {
    return (
      <p className="rounded-xl border border-border p-4 text-sm text-muted-foreground">
        There is nothing available to buy right now. Contact us and we will sort
        it out.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {plans.map((plan) => {
        const quantity = selection[plan.slug] ?? 0;
        // A `kind` is a category, not a measurement: its hue is an identity
        // only, so it goes through the shared ramp rather than a tone
        // (AGENTS.md rule 7). The label carries the meaning; the colour never
        // does on its own.
        const accent = categoricalAccent(plan.kind);
        return (
          <div
            key={plan.slug}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border p-3"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <p className="truncate text-sm font-medium text-foreground">
                  {plan.name}
                </p>
                <span
                  className={`rounded-md border px-1.5 py-0.5 text-[10px] ${accent.chip}`}
                >
                  {plan.kind}
                </span>
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {formatPaise(plan.price_paise)} per month
              </p>
            </div>

            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="icon-sm"
                icon="Minus"
                aria-label={`One fewer ${plan.name}`}
                disabled={quantity === 0}
                onClick={() => onQuantity(plan.slug, quantity - 1)}
              />
              <span className="w-6 text-center text-sm text-foreground">
                {quantity}
              </span>
              <Button
                variant="secondary"
                size="icon-sm"
                icon="Plus"
                aria-label={`One more ${plan.name}`}
                onClick={() => onQuantity(plan.slug, quantity + 1)}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Step 2: what the server priced ──────────────────────────────────────────

function OrderPanel({ order }: { order: Order }) {
  const state = orderState(order, Date.now());

  return (
    <section className="rounded-xl border border-border p-3">
      <div className="flex flex-col gap-1 text-sm">
        {(order.lines ?? []).map((line) => (
          <div key={line.plan_slug} className="flex justify-between gap-4">
            <span className="text-muted-foreground">
              {line.plan_slug} × {line.quantity}
            </span>
            <span className="text-foreground">
              {formatPaise(line.unit_price_paise * line.quantity)}
            </span>
          </div>
        ))}

        <div className="mt-2 flex justify-between gap-4 border-t border-border pt-2">
          <span className="text-muted-foreground">Subtotal</span>
          <span className="text-foreground">{formatPaise(order.gross_paise)}</span>
        </div>

        {order.discount ? (
          <div className="flex justify-between gap-4">
            <span className="text-success">
              Code {order.discount.code_prefix}
            </span>
            <span className="text-success">
              −{formatPaise(order.discount.discount_paise)}
            </span>
          </div>
        ) : null}

        <div className="flex justify-between gap-4">
          <span className="text-muted-foreground">
            GST{order.gst_split ? ` (${order.gst_split})` : ""}
          </span>
          <span className="text-foreground">{formatPaise(order.gst_paise)}</span>
        </div>

        <div className="mt-1 flex justify-between gap-4 border-t border-border pt-2 text-base font-semibold">
          <span className="text-foreground">Total</span>
          <span className="text-foreground">{formatPaise(order.total_paise)}</span>
        </div>
      </div>

      {state.terminal && !state.completed ? (
        <p className="mt-3 text-xs text-warning">
          {terminalCopy(state.reason ?? "expired")}
        </p>
      ) : null}
    </section>
  );
}

// ── Step 2b: the code ───────────────────────────────────────────────────────

function CodeEntry({
  code,
  onCode,
  onApply,
  busy,
  fieldRef,
}: {
  code: string;
  onCode: (next: string) => void;
  onApply: () => void;
  busy: boolean;
  fieldRef: React.RefObject<HTMLInputElement | null>;
}) {
  return (
    <div className="mt-4">
      <label
        htmlFor="billing-discount-code"
        className="text-xs text-muted-foreground"
      >
        Have a code?
      </label>
      <div className="mt-1 flex items-center gap-2">
        <Input
          id="billing-discount-code"
          ref={fieldRef}
          inputSize="lg"
          value={code}
          autoComplete="off"
          spellCheck={false}
          placeholder="Enter your code"
          onChange={(e) => onCode(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onApply();
          }}
        />
        <Button onClick={onApply} loading={busy} disabled={!code.trim()}>
          Apply
        </Button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        A code that covers the whole amount completes the purchase here. Card
        payment is not available on this screen yet — if anything is left to
        pay, contact us and we will arrange it.
      </p>
    </div>
  );
}

// ── Step 3: done, and it stops ──────────────────────────────────────────────

function Completed({ order }: { order: Order }) {
  return (
    <div className="mt-4 flex items-start gap-3 rounded-xl border border-success/40 bg-success/10 p-4">
      <Icon name="CheckCircle2" size={18} className="mt-0.5 text-success" />
      <div>
        <p className="text-sm font-medium text-success">
          Your purchase is complete.
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Nothing was charged — the code covered the full amount. Your
          subscription and seats are active.
        </p>
        {order.discount ? (
          <Badge tone="success" className="mt-2">
            Code {order.discount.code_prefix} applied
          </Badge>
        ) : null}
      </div>
    </div>
  );
}
