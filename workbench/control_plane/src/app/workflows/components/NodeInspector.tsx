"use client";

/**
 * The inspector pane — typed config fields for the selected node (RFC §5.3).
 * Fields that accept upstream data take `{{node.field}}` references; the
 * available roots are shown as hint chips (trigger, vars, upstream node ids).
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useCallback, useMemo, useState } from "react";
import { FALLBACK_ICON, NODE_ICON, NODE_KIND_LABEL } from "../lib/nodeVisuals";
import { NODE_CATEGORY_STYLE, agentLabel, categoryForType } from "../lib/types";
import type {
  Catalog,
  GraphIssue,
  NodeType,
  ToolArg,
  TriggerKind,
  WorkflowGraphNode,
} from "../lib/types";

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
        {label}
      </span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-input bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring";

function JsonField({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (v: Record<string, unknown>) => void;
}) {
  return (
    <textarea
      defaultValue={JSON.stringify(value ?? {}, null, 2)}
      onBlur={(e) => {
        try {
          const parsed = JSON.parse(e.target.value || "{}");
          if (parsed && typeof parsed === "object") onChange(parsed);
        } catch {
          /* keep the previous value on bad JSON; the field shows the draft */
        }
      }}
      rows={5}
      spellCheck={false}
      className={`${inputCls} font-mono text-[11px]`}
    />
  );
}

/**
 * One typed field per declared tool argument (spec F3 / RFC §2.2 — Sim's
 * `subBlocks` pattern: the integration catalog carries the parameter contract,
 * so the form is data, not per-tool UI code).
 *
 * Values stay in the same `config.args` object the raw JSON editor writes, so
 * switching between the two views is lossless and the run-model is unchanged.
 * Every field accepts `{{refs}}`, including typed ones — a reference resolves
 * at run time and is exempt from type checking on both sides.
 */
function ToolArgsForm({
  args,
  value,
  onChange,
}: {
  args: ToolArg[];
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const set = (name: string, raw: string, type: ToolArg["type"]) => {
    const next = { ...value };
    if (raw.trim() === "") {
      delete next[name];
    } else if (type === "object" || type === "array") {
      // Structured fields hold JSON — unless the maker wrote a reference,
      // which is a string until the run resolves it.
      if (raw.includes("{{")) {
        next[name] = raw;
      } else {
        try {
          next[name] = JSON.parse(raw);
        } catch {
          next[name] = raw; // keep the draft; validation names it
        }
      }
    } else if (type === "number" && !raw.includes("{{")) {
      const n = Number(raw);
      next[name] = Number.isFinite(n) ? n : raw;
    } else {
      next[name] = raw;
    }
    onChange(next);
  };

  const asText = (v: unknown) =>
    v === undefined || v === null
      ? ""
      : typeof v === "string"
        ? v
        : JSON.stringify(v);

  return (
    <div className="space-y-2">
      {args.map((arg) => (
        <div key={arg.name}>
          <div className="flex items-baseline gap-1">
            <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
              {arg.name}
            </span>
            {arg.required ? (
              <span className="text-[10px] text-destructive" title="Required">
                *
              </span>
            ) : (
              <span className="text-[9px] text-muted-foreground/60">optional</span>
            )}
            <span className="ml-auto text-[9px] text-muted-foreground/60 font-mono">
              {arg.type}
            </span>
          </div>
          {arg.type === "boolean" ? (
            <select
              value={String(value[arg.name] ?? "")}
              onChange={(e) => {
                const next = { ...value };
                if (e.target.value === "") delete next[arg.name];
                else next[arg.name] = e.target.value === "true";
                onChange(next);
              }}
              className={inputCls}
            >
              <option value="">—</option>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : (
            <input
              value={asText(value[arg.name])}
              onChange={(e) => set(arg.name, e.target.value, arg.type)}
              placeholder={
                arg.type === "object"
                  ? '{"key": "value"} or {{node.output}}'
                  : arg.type === "array"
                    ? '["a", "b"] or {{node.list}}'
                    : "value or {{node.field}}"
              }
              spellCheck={false}
              className={`${inputCls} ${arg.type === "object" || arg.type === "array" ? "font-mono text-[11px]" : ""}`}
            />
          )}
          {arg.description && (
            <p className="text-[10px] text-muted-foreground mt-0.5">
              {arg.description}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

const WAIT_UNITS: { id: string; label: string; secs: number }[] = [
  { id: "seconds", label: "seconds", secs: 1 },
  { id: "minutes", label: "minutes", secs: 60 },
  { id: "hours", label: "hours", secs: 3600 },
  { id: "days", label: "days", secs: 86400 },
];

/** Duration editor over a single `seconds` config value: the unit is display
 * only (the largest that divides evenly), so the graph stays one number. */
function WaitDuration({
  seconds,
  onChange,
}: {
  seconds: unknown;
  onChange: (v: number) => void;
}) {
  const total = Number(seconds);
  const safe = Number.isFinite(total) && total > 0 ? total : 0;
  const unit =
    [...WAIT_UNITS].reverse().find((u) => safe >= u.secs && safe % u.secs === 0) ??
    WAIT_UNITS[0];
  const amount = safe ? safe / unit.secs : "";

  return (
    <div className="flex gap-1.5">
      <input
        type="number"
        min={1}
        value={amount}
        onChange={(e) => {
          const n = Number(e.target.value);
          onChange(Number.isFinite(n) && n > 0 ? n * unit.secs : 0);
        }}
        className={`${inputCls} w-24`}
      />
      <select
        value={unit.id}
        onChange={(e) => {
          const next = WAIT_UNITS.find((u) => u.id === e.target.value);
          if (next && amount) onChange(Number(amount) * next.secs);
        }}
        className={inputCls}
      >
        {WAIT_UNITS.map((u) => (
          <option key={u.id} value={u.id}>
            {u.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/** The words of a node that names an agent that the catalog no longer offers. */
export function retiredAgentNote(offer: string): string {
  return `This step uses an assistant that Metorite retired. Replace it with ${offer}.`;
}

/** A value that the inspector shows and does not let the member change. */
function ReadOnly({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
        {label}
      </span>
      <div className="mt-1 text-xs text-foreground whitespace-pre-wrap break-words">
        {children}
      </div>
    </div>
  );
}

/**
 * The fields of the AI step (WFC-2, D223 proposed).
 *
 * The catalog offers one agent, Ask Mira, so the step has nothing to choose:
 * it shows its name and what it does. A node that names an agent that the
 * catalog does not offer comes from a graph of before. It still runs, so the
 * inspector shows it read only, with a note that says what to do.
 *
 * No model field: the platform picks the model (D90). A `model` that an old
 * graph stores stays in the config, and the run still reads it.
 */
function AgentFields({
  agent,
  message,
  catalog,
  onAgent,
  onMessage,
}: {
  agent: string;
  message: string;
  catalog: Catalog | null;
  onAgent: (name: string) => void;
  onMessage: (text: string) => void;
}) {
  const offered = catalog?.agents ?? [];
  const picked = offered.find((a) => a.name === agent) ?? null;
  const offer = offered[0] ?? null;
  // Before the catalog loads, no node can read as retired.
  const retired = agent !== "" && catalog !== null && picked === null;

  if (retired) {
    return (
      <>
        <ReadOnly label="AI step">
          <span className="font-mono">{agent}</span>
        </ReadOnly>
        <p
          role="note"
          className="rounded-lg border border-warning/30 bg-warning/10 px-2.5 py-2 text-[11px] text-foreground"
        >
          {retiredAgentNote(offer ? agentLabel(offer) : "Ask Mira")}
        </p>
        <ReadOnly label="Message">
          {message || <span className="text-muted-foreground">No message.</span>}
        </ReadOnly>
      </>
    );
  }

  return (
    <>
      <div>
        <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
          AI step
        </span>
        <div className="mt-1">
          {picked ? (
            <>
              <div className="text-xs font-medium text-foreground">{agentLabel(picked)}</div>
              <p className="mt-0.5 text-[10px] text-muted-foreground">{picked.description}</p>
            </>
          ) : offer ? (
            <Button size="sm" variant="secondary" onClick={() => onAgent(offer.name)}>
              Use {agentLabel(offer)}
            </Button>
          ) : (
            <p className="text-[10px] text-muted-foreground">
              {catalog === null
                ? agent || "Loading the AI step…"
                : "The AI step did not load. Reload the page to try again."}
            </p>
          )}
        </div>
      </div>
      <Field label="Message">
        <textarea
          value={message}
          onChange={(e) => onMessage(e.target.value)}
          rows={5}
          placeholder="Sort this email as urgent or not: {{trigger.body}}"
          className={inputCls}
        />
      </Field>
    </>
  );
}

export default function NodeInspector({
  node,
  catalog,
  upstreamIds,
  issues,
  triggerKinds,
  onConfig,
  onLabel,
  onDelete,
}: {
  node: WorkflowGraphNode | null;
  catalog: Catalog | null;
  upstreamIds: string[];
  issues: GraphIssue[];
  triggerKinds: { kind: TriggerKind; hint: string }[];
  onConfig: (patch: Record<string, unknown>) => void;
  onLabel: (label: string) => void;
  onDelete: () => void;
}) {
  const set = useCallback(
    (key: string, value: unknown) => onConfig({ [key]: value }),
    [onConfig],
  );
  // Escape hatch for arguments the form cannot express (a whole object pasted
  // from a previous run, an argument added to the action since this version
  // was published). Per-node so leaving it open on one node does not follow
  // you to the next.
  const [rawArgsFor, setRawArgsFor] = useState<string | null>(null);
  const selectedTool = useMemo(() => {
    const action = node
      ? String((node.data.config as Record<string, unknown>).action ?? "")
      : "";
    return (catalog?.tools ?? []).find((t) => t.action === action) ?? null;
  }, [catalog, node]);

  if (!node) {
    return (
      <div className="w-64 xl:w-72 shrink-0 border-l border-border p-4">
        <p className="text-xs text-muted-foreground">
          Select a node to configure it. Fields accept{" "}
          <code className="text-[10px] bg-secondary px-1 rounded">
            {"{{trigger.field}}"}
          </code>{" "}
          and{" "}
          <code className="text-[10px] bg-secondary px-1 rounded">
            {"{{node_id.field}}"}
          </code>{" "}
          references to upstream data.
        </p>
      </div>
    );
  }

  const cfg = node.data.config as Record<string, unknown>;
  const type = node.type as NodeType;
  const rawArgs = rawArgsFor === node.id;
  const nodeIssues = issues.filter((i) => i.node_id === node.id);
  const headStyle =
    NODE_CATEGORY_STYLE[categoryForType(type)] ?? NODE_CATEGORY_STYLE.logic;
  const HeadIcon = NODE_ICON[type] ?? FALLBACK_ICON;

  return (
    <div className="w-64 xl:w-72 shrink-0 border-l border-border overflow-y-auto scrollbar-thin">
      {/* Header mirrors the card it belongs to: same plate, same kind eyebrow */}
      <div className="p-3 border-b border-border flex items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0">
          <span
            className={`w-7 h-7 shrink-0 rounded-lg border grid place-items-center ${headStyle.tile}`}
          >
            <HeadIcon className="w-3.5 h-3.5" />
          </span>
          <div className="min-w-0">
            <div
              className={`font-mono text-[8.5px] uppercase tracking-[0.12em] ${headStyle.text}`}
            >
              {NODE_KIND_LABEL[type] ?? type}
            </div>
            <div className="text-xs font-semibold text-foreground truncate">
              {node.data.label || type}
            </div>
            <div className="font-mono text-[9.5px] text-muted-foreground truncate">
              {node.id}
            </div>
          </div>
        </div>
        {type !== "trigger" && (
          <button
            onClick={onDelete}
            title="Delete node"
            className="p-1.5 rounded-lg text-muted-foreground hover:text-destructive hover:bg-secondary tech-transition"
          >
            <Icon name="Trash2" className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {nodeIssues.length > 0 && (
        <div className="p-3 border-b border-border space-y-1">
          {nodeIssues.map((issue, i) => (
            <p key={i} className="text-[10px] text-warning">
              ⚠ {issue.message}
            </p>
          ))}
        </div>
      )}

      <div className="p-3 space-y-3">
        <Field label="Label">
          <input
            value={node.data.label}
            onChange={(e) => onLabel(e.target.value)}
            className={inputCls}
          />
        </Field>

        {type === "trigger" && (
          <>
            <Field label="Trigger kinds">
              <p className="text-[10px] text-muted-foreground">
                Configure triggers (webhook URL, schedule, …) from the
                topbar&apos;s Triggers panel. The trigger&apos;s payload is
                available downstream as <code>{"{{trigger.*}}"}</code>.
              </p>
            </Field>
            <div className="space-y-1">
              {triggerKinds.map((t) => (
                <div key={t.kind} className="text-[10px] text-muted-foreground">
                  <span className="font-medium text-foreground">{t.kind}</span>{" "}
                  — {t.hint}
                </div>
              ))}
            </div>
          </>
        )}

        {type === "agent" && (
          <AgentFields
            agent={String(cfg.agent ?? "")}
            message={String(cfg.message ?? "")}
            catalog={catalog}
            onAgent={(name) => set("agent", name)}
            onMessage={(text) => set("message", text)}
          />
        )}

        {type === "tool" && (
          <>
            <Field label="Action">
              <select
                value={String(cfg.action ?? "")}
                onChange={(e) => set("action", e.target.value)}
                className={inputCls}
              >
                <option value="">Select an action…</option>
                {(catalog?.tools ?? []).map((t) => (
                  <option key={t.action} value={t.action}>
                    {t.label}
                    {t.destructive ? " (write)" : ""}
                  </option>
                ))}
              </select>
            </Field>
            {selectedTool?.args?.length && !rawArgs ? (
              <>
                <ToolArgsForm
                  args={selectedTool.args}
                  value={(cfg.args ?? {}) as Record<string, unknown>}
                  onChange={(v) => set("args", v)}
                />
                <button
                  onClick={() => setRawArgsFor(node.id)}
                  className="text-[10px] text-muted-foreground hover:text-foreground underline"
                >
                  Edit as JSON
                </button>
              </>
            ) : (
              <>
                <Field label="Arguments (JSON, {{refs}} allowed)">
                  <JsonField
                    key={`${node.id}:args`}
                    value={cfg.args}
                    onChange={(v) => set("args", v)}
                  />
                </Field>
                {/* Only offer the way back when there is a form to go back to:
                    an action with no declared arguments has nothing to render. */}
                {selectedTool?.args?.length ? (
                  <button
                    onClick={() => setRawArgsFor(null)}
                    className="text-[10px] text-muted-foreground hover:text-foreground underline"
                  >
                    Back to fields
                  </button>
                ) : null}
              </>
            )}
          </>
        )}

        {type === "module" && (
          <>
            <Field label="Module">
              <select
                value={String(cfg.module_id ?? "")}
                onChange={(e) => set("module_id", e.target.value)}
                className={inputCls}
              >
                <option value="">Select a module…</option>
                {(catalog?.modules ?? [])
                  .filter((m) => m.status === "ready")
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="Inputs (JSON, {{refs}} allowed)">
              <JsonField
                key={`${node.id}:inputs`}
                value={cfg.inputs}
                onChange={(v) => set("inputs", v)}
              />
            </Field>
          </>
        )}

        {type === "condition" && (
          <>
            <Field label="Left value">
              <input
                value={String(cfg.left ?? "")}
                onChange={(e) => set("left", e.target.value)}
                placeholder="{{classify.result}}"
                className={inputCls}
              />
            </Field>
            <Field label="Operator">
              <select
                value={String(cfg.op ?? "equals")}
                onChange={(e) => set("op", e.target.value)}
                className={inputCls}
              >
                {(catalog?.condition_ops ?? ["equals"]).map((op) => (
                  <option key={op} value={op}>
                    {op}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Right value">
              <input
                value={String(cfg.right ?? "")}
                onChange={(e) => set("right", e.target.value)}
                placeholder="lead"
                className={inputCls}
              />
            </Field>
          </>
        )}

        {type === "set" && (
          <Field label="Assignments (name → value, {{refs}} allowed)">
            <JsonField
              key={`${node.id}:assignments`}
              value={cfg.assignments}
              onChange={(v) => set("assignments", v)}
            />
          </Field>
        )}

        {type === "approval" && (
          <Field label="Message for the approver">
            <textarea
              value={String(cfg.message ?? "")}
              onChange={(e) => set("message", e.target.value)}
              rows={3}
              placeholder="OK to create the CRM record for {{trigger.company}}?"
              className={inputCls}
            />
            <p className="text-[10px] text-muted-foreground mt-1">
              The run pauses here and appears in the Approvals inbox. Approving
              resumes it; rejecting cancels it.
            </p>
          </Field>
        )}

        {type === "wait" && (
          <Field label="Wait for">
            <WaitDuration
              seconds={cfg.seconds}
              onChange={(v) => set("seconds", v)}
            />
            <p className="text-[10px] text-muted-foreground mt-1">
              Up to a minute runs inline. Anything longer parks the run with a
              deadline — it survives a restart and resumes on schedule.
            </p>
          </Field>
        )}

        {type === "output" && (
          <Field label="Value ({{refs}} allowed)">
            <textarea
              value={String(cfg.value ?? "")}
              onChange={(e) => set("value", e.target.value)}
              rows={3}
              placeholder="{{final_step.result}}"
              className={inputCls}
            />
          </Field>
        )}

        {type !== "trigger" && (
          <div className="pt-1">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide shrink-0">
                Available data
              </span>
              <span className="h-px flex-1 bg-border" />
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {["trigger", "vars", ...upstreamIds].map((root) => (
                <code
                  key={root}
                  title="Paste this into any field above"
                  className="font-mono text-[9.5px] px-1.5 py-0.5 rounded-md border border-primary/25 bg-primary/10 text-primary"
                >
                  {`{{${root}.*}}`}
                </code>
              ))}
            </div>
            <p className="mt-1.5 text-[9.5px] text-muted-foreground">
              Only blocks upstream of this one are listed — anything else has not
              run yet when this block does.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
