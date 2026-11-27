// The fixed Studio error codes and their sentences (STU-L6a). Data only.
// The twin of packages/acb_common/acb_common/studio_limits.py.
// tests/unit/test_studio_code_error_codes.py and codeErrors.test.ts hold them equal.

export const STUDIO_CODE_ERRORS = {
  compile_failed: "The screen did not compile, so open the console and fix the file and line it names.",
  build_failed: "The server build failed, so the live version stays and you can fix the code and build again.",
  limit_exceeded: "The version or the call went past a limit, so make the source smaller or the call lighter.",
  timeout: "The call ran too long, so make it do less work and run it again.",
  door_refused: "The door refused the call, so read its reason and change the call to fit.",
  not_approved: "A publisher has not approved this version yet, so ask a publisher to approve it.",
  code_off: "You have no code right, so ask an admin to turn code on for your group.",
  code_paused: "An admin stopped code for your organization, so ask an admin to turn it on again.",
  runtime_busy: "Try again in a few seconds.",
  bad_output: "The output does not match the output schema, so change the code to return the declared shape.",
  contract_unsupported: "The server does not serve the contract version that the folder pins, so pin a served version.",
} as const;

export type StudioCodeError = keyof typeof STUDIO_CODE_ERRORS;
