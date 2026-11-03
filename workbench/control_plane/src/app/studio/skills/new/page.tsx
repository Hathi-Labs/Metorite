"use client";

/** /studio/skills/new — make a private skill (`ai_studio.md` §14 F2b). */

import { AppTopBar } from "@/components/AppTopBar";
import SkillForm from "../../components/SkillForm";

export default function NewSkillPage() {
  return (
    <div className="flex h-full flex-col">
      <AppTopBar title="New skill" subtitle="Studio" back={{ href: "/studio", label: "Back to Studio" }} />
      <SkillForm />
    </div>
  );
}
