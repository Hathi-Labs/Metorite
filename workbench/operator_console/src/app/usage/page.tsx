import { redirect } from "next/navigation";

// "AI usage" became "Money" (WS-50 slice 4, decision D94). The route stays so
// an old bookmark or link still lands on the right page.
export default function UsagePage() {
  redirect("/money");
}
