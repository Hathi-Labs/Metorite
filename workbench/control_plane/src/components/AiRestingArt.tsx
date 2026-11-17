/**
 * The resting elephant: the picture of the card that says the AI cannot
 * answer right now (AO-3, incident 2026-11-17).
 *
 * The owner asked for a fun, friendly picture, in the spirit of the old
 * "service is down" mascots. HathiLabs is our company, and "hathi" means
 * elephant. So the mascot is a small, sad elephant. It sits with its eyes
 * shut and its trunk down, beside the plug of its own cable, which is out.
 * The art is our own, drawn from a few flat shapes.
 *
 * ⚠️ **Tokens only, no colour written by hand** (`DESIGN_SYSTEM.md` §1).
 * - The elephant is `--muted-foreground`, a grey in both modes.
 * - A seam between two parts is a stroke of `--card`, so the parts read as
 *   separate in light and dark mode alike.
 * - The eye is a stroke of `--background`, cut out of the head.
 * - The plug, the cable and the tear are `--primary`: the two touches that
 *   follow the member's accent.
 *
 * The size comes from the caller in rem, so the picture follows density.
 * The picture is decoration: the card's title and text say everything, so
 * it is hidden from a screen reader.
 *
 * Motion: the elephant breathes, as an idle agent's card does (EA-13h,
 * `DESIGN_SYSTEM.md` §4b, "Working"). `.ai-rest-breathe` in `globals.css`
 * runs only under `prefers-reduced-motion: no-preference`, so a member who
 * asked for less motion sees it still.
 *
 * Fence: `components/aiRestingCard.test.ts`.
 */

export default function AiRestingArt({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="6 22 110 72"
      aria-hidden="true"
      focusable="false"
      data-ai-resting-art
      className={className}
    >
      {/* The ground. */}
      <ellipse cx="60" cy="89" rx="48" ry="3.5" className="fill-muted-foreground/15" />
      {/* The cable runs from behind the elephant to the plug, which is out. */}
      <path
        d="M114 90 C 90 92 44 92 24 88"
        className="fill-none stroke-primary"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <rect x="14" y="83" width="11" height="8" rx="2" className="fill-primary" />
      <rect x="8.5" y="84.4" width="6" height="1.8" rx="0.9" className="fill-primary" />
      <rect x="8.5" y="87.8" width="6" height="1.8" rx="0.9" className="fill-primary" />

      <g className="ai-rest-breathe">
        {/* The tail. */}
        <path
          d="M93 64 C 100 66 102 72 100 78"
          className="fill-none stroke-muted-foreground"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <circle cx="100" cy="79.5" r="2.4" className="fill-muted-foreground" />
        {/* The far front leg, the body, the back leg, the near front leg. */}
        <rect x="61" y="70" width="11" height="17" rx="5.5" className="fill-muted-foreground stroke-card" strokeWidth="2" />
        <ellipse cx="71" cy="64" rx="25" ry="20" className="fill-muted-foreground stroke-card" strokeWidth="2" />
        <circle cx="83" cy="74" r="11" className="fill-muted-foreground stroke-card" strokeWidth="2" />
        <rect x="77" y="78" width="16" height="9" rx="4.5" className="fill-muted-foreground stroke-card" strokeWidth="2" />
        <rect x="49" y="68" width="12" height="19" rx="6" className="fill-muted-foreground stroke-card" strokeWidth="2" />
        {/* The trunk hangs down and curls on the ground. */}
        <path
          d="M31 54 C 25 62 23 71 26 79 C 28 85 35 85 35 80"
          className="fill-none stroke-muted-foreground"
          strokeWidth="8"
          strokeLinecap="round"
        />
        {/* The head. */}
        <circle cx="44" cy="45" r="19" className="fill-muted-foreground stroke-card" strokeWidth="2" />
        {/* The ear droops. */}
        <path
          d="M51 30 C 66 28 71 43 67 55 C 64 63 55 63 52 56 C 50 48 49 38 51 30 Z"
          className="fill-muted-foreground stroke-card"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <path
          d="M55 36 C 63 36 66 45 63 52 C 61 56 57 56 56 52 C 55 47 54 41 55 36 Z"
          className="fill-background/25"
        />
        {/* A shut, sad eye, and one tear. */}
        <path
          d="M34 45 Q 38.5 41.5 43 45"
          className="fill-none stroke-background"
          strokeWidth="2.2"
          strokeLinecap="round"
        />
        <path
          d="M35 49.5 C 33.6 51.7 33 52.9 33 53.9 A 2 2 0 0 0 37 53.9 C 37 52.9 36.4 51.7 35 49.5 Z"
          className="fill-primary"
        />
      </g>
    </svg>
  );
}
