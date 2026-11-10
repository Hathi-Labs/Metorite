/**
 * Which deployment this workbench is: production or staging (WS-38 S-7,
 * `project-docs/specs/staging_and_daily_release.md` §3).
 *
 * The box's root `.env` holds `DEPLOY_ENV`, and `scripts/vps_apply.sh` copies
 * it into this app's `.env.local` before each build. Read on the server: a
 * STATIC page takes the value at `next build`, a dynamic page at request time.
 * So a change needs a rebuild, and a restart alone leaves the two kinds of
 * page disagreeing. It is not a `NEXT_PUBLIC_` name, because nothing in the
 * browser needs it.
 *
 * Anything that is not exactly `staging` is production. A production box has
 * no such line, and it must render exactly as it did before.
 */
export type DeployEnv = "production" | "staging";

export function deployEnv(raw: string | undefined = process.env.DEPLOY_ENV): DeployEnv {
  return raw?.trim() === "staging" ? "staging" : "production";
}

/** The tab title. A person with both sites open must tell the tabs apart. */
export function deployTitle(base: string, env: DeployEnv = deployEnv()): string {
  return env === "staging" ? `[Staging] ${base}` : base;
}
