/**
 * Which deployment this workbench is: production or staging (WS-38 S-7,
 * `project-docs/specs/staging_and_daily_release.md` §3).
 *
 * The box's `.env` holds `DEPLOY_ENV`, and the staging bootstrap writes the
 * same line into this app's `.env.local`. Read on the SERVER, at render time.
 * It is not a `NEXT_PUBLIC_` name, because nothing in the browser needs it.
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
