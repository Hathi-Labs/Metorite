import SignInForm, { type SignInProvider } from "./SignInForm";

/**
 * The sign-in page derives its buttons from the CONFIGURED providers — the
 * same environment keys `src/auth.ts` registers providers from — so offering
 * a customer's IdP is configuration, never a code change. This page used to
 * hardcode a single Microsoft button (plus copy claiming a domain restriction
 * that nothing enforced); as a SaaS product it could onboard exactly one
 * customer that way.
 *
 * Server component on purpose: provider env vars are server-only, and the
 * client half (`SignInForm`) receives the derived list as props.
 */
export default function SignIn() {
  const providers: SignInProvider[] = [];
  if (process.env.AUTH_GOOGLE_ID) {
    providers.push({ id: "google", label: "Continue with Google" });
  }
  if (process.env.AUTH_MICROSOFT_ENTRA_ID_ID) {
    providers.push({
      id: "microsoft-entra-id",
      label: "Continue with Microsoft",
    });
  }
  return <SignInForm providers={providers} />;
}
