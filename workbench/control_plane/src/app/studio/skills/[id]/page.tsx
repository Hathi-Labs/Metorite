"use client";

/** /studio/skills/[id] — version, publish, pause and resume one skill (`ai_studio.md` §14 F2b). */

import { use } from "react";

import { AppTopBar } from "@/components/AppTopBar";
import SkillForm from "../../components/SkillForm";

export default function SkillPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <div className="flex h-full flex-col">
      <AppTopBar title="Skill" subtitle="Studio" back={{ href: "/studio", label: "Back to Studio" }} />
      <SkillForm skillId={id} />
    </div>
  );
}
