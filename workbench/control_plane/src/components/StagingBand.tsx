import { deployEnv } from "@/lib/deploy/deployEnv";

/**
 * A 4px warning stripe across the top of every page on the STAGING box
 * (WS-38 S-7, `staging_and_daily_release.md` §3). Without it a person can act
 * on staging and believe the act reached production.
 *
 * It renders NOTHING on production. It is an overlay that takes no space and
 * no clicks: the shell fills 100dvh, so a band in the flow would push every
 * page past the screen. The words are for a screen reader. The tab title
 * (`deployTitle`) carries them for everyone else.
 */
export default function StagingBand() {
  if (deployEnv() !== "staging") return null;
  return (
    <div
      role="note"
      data-staging-band=""
      className="pointer-events-none fixed inset-x-0 top-0 z-[90] h-1 bg-warning"
    >
      <span className="sr-only">Staging deployment. Changes here do not reach production.</span>
    </div>
  );
}
