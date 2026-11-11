"use client";

/**
 * The button that leaves for Google (WS-17 EM-G11a item 8).
 *
 * It follows `https://developers.google.com/identity/branding-guidelines`.
 * The label, the mark and these colours are Google's, not ours, so this file
 * is the one Google palette in the app. `DESIGN_SYSTEM.md` §1 exception 2
 * allows that, and `COLOR_EXCEPTIONS` in `lib/theme/conformance.test.ts`
 * lists this file. The Meta button of the WhatsApp connect is the precedent.
 *
 * ⚠️ It is a raw `<button>` on purpose. The shared `Button` takes its fill from
 * `--primary` and its size from `rem`. Google's rules fix the fill, and "no
 * accent and no density change touches it", so each size here is in px.
 *
 * The page root carries `.light` in light mode and nothing in dark mode, so
 * the dark values are the base and `.light` swaps them, as `MetoriteMark` does.
 * The literals are whole class strings, because Tailwind finds a class only
 * when the source spells it.
 */

import { Roboto } from "next/font/google";
import { DISCLOSURE_COPY, GOOGLE_G_MARK } from "../lib/connect";

const PALETTE =
  "border-[#8E918F] bg-[#131314] text-[#E3E3E3] " +
  "[.light_&]:border-[#747775] [.light_&]:bg-[#FFFFFF] [.light_&]:text-[#1F1F1F]";

/**
 * Roboto Medium, 14 px, because Google's rules name it for this label. It is
 * loaded here and not in the root layout, so no other app carries its CSS.
 */
const roboto = Roboto({ weight: "500", subsets: ["latin"], preload: false, display: "swap" });

export function GoogleButton({ disabled, onClick }: { disabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      style={roboto.style}
      className={`inline-flex h-[40px] shrink-0 items-center gap-[10px] rounded-[4px] border px-[12px] text-[14px] leading-[20px] font-medium outline-none hover:opacity-90 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 ${PALETTE}`}
    >
      <img src={GOOGLE_G_MARK} alt="" width={20} height={20} />
      {DISCLOSURE_COPY.gmail.button}
    </button>
  );
}
