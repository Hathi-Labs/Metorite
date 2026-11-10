import { redirect } from "next/navigation";

export default function SettingsRoot() {
  // `/settings/models` was deleted (CP-5). Appearance is open to every member,
  // so it is the one landing that never refuses.
  redirect("/settings/appearance");
}
