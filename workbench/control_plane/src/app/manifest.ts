import type { MetadataRoute } from "next";

/**
 * The web manifest, served at `/manifest.webmanifest`. It names the app and
 * gives it the Metorite comet when a member adds it to a home screen.
 *
 * The icons come from `brand/make_icons.py`. Every path here is in
 * `proxy.ts`'s `PUBLIC_PAGES`, because a browser fetches the manifest with no
 * cookie, and a redirect to /signin is not an icon.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    // A stable identity: the install survives a later change of `start_url`.
    id: "/",
    name: "Metorite",
    short_name: "Metorite",
    description: "Metorite, the AI company operating system.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    categories: ["business", "productivity"],
    // The app's default colour mode is dark (`Providers.tsx`).
    background_color: "#0b1020",
    theme_color: "#0b1020",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
