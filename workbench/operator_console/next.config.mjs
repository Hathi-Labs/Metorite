/** @type {import('next').NextConfig} */
const nextConfig = {
  // Plain, theming-exempt staff app (D35.4). No design-system integration, no
  // shared workbench config — it is a DIFFERENT application by construction,
  // which is what makes "shares tables, never routes" a deployment boundary
  // rather than a guard (D35.2).
  reactStrictMode: true,
};

export default nextConfig;
