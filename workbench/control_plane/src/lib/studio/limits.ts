// The proposed limits of one Studio version (STU-L6a). Data only.
// The twin of LIMITS in packages/acb_common/acb_common/studio_limits.py.
// The numbers stay proposed until L6 measures them.
// tests/unit/test_studio_code_error_codes.py and codeErrors.test.ts hold them equal.

export const STUDIO_LIMITS = {
  browser: {
    source_bytes: 262144,
    source_files: 100,
    compile_seconds: 10,
    input_bytes: 1048576,
    output_bytes: 4194304,
    door_calls_per_second: 20,
  },
  server: {
    source_bytes: 262144,
    source_files: 100,
    build_seconds: 180,
    memory_bytes: 268435456,
    call_seconds: 30,
    input_bytes: 1048576,
    output_bytes: 4194304,
    writes_per_call: 5000,
    queue_calls: 16,
    warm_runtimes_per_org: 1,
    idle_stop_seconds: 600,
    packages_direct: 20,
    packages_total: 150,
  },
} as const;

export type StudioLimitKind = keyof typeof STUDIO_LIMITS;
