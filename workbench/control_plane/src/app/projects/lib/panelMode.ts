/**
 * The side panel and full card rules moved to `lib/panelMode.ts` (ORC-1, D181),
 * because the CRM record card reads them too. This path stays until ORC-2
 * points Projects at the shared file.
 */
export * from "@/lib/panelMode";
