/** @type {import('next').NextConfig} */
const nextConfig = {
  // Built beside the running build and swapped in on success — the same
  // contract `workbench/control_plane/next.config.ts` carries, and for the same
  // defect. `scripts/vps_apply.sh` owns the staging directory and the rename.
  // Two Next builds run per deploy, so this app is inside that window too.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Plain, theming-exempt staff app (D35.4). No design-system integration, no
  // shared workbench config — it is a DIFFERENT application by construction,
  // which is what makes "shares tables, never routes" a deployment boundary
  // rather than a guard (D35.2).
  reactStrictMode: true,
};

export default nextConfig;
