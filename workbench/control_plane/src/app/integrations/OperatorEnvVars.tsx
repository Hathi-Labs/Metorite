/**
 * The read-only list of the guide keys that the operator sets on the server
 * (security fix, 2026-10-05).
 *
 * The gateway's `/integrations/status` moves every guide key that a tenant may
 * not write — a URL, host, domain, port or path, a mailbox the operator's
 * service account impersonates, or a platform key — out of `env_vars` and
 * into `operator_env_vars`. No form offers them, and this block names them.
 *
 * It renders a label and a key name, and never a value: a status row carries
 * no value, and this component reads only `key` and `label` even when a row
 * holds more.
 */

export interface OperatorEnvVarsRow {
  operator_env_vars?: { key: string; label: string }[];
}

export default function OperatorEnvVars({ api }: { api: OperatorEnvVarsRow }) {
  const vars = api.operator_env_vars ?? [];
  if (vars.length === 0) return null;
  return (
    <div data-testid="operator-env-vars" className="rounded-lg border border-border bg-secondary px-3 py-2 text-xs text-muted-foreground">
      <p className="mb-1">The operator sets these on the server. You cannot change them here:</p>
      <ul className="space-y-0.5">
        {vars.map((v) => (
          <li key={v.key}>{v.label} <span className="font-mono text-[9px]">{v.key}</span></li>
        ))}
      </ul>
    </div>
  );
}
