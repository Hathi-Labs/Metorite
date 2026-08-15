# Disabled workflows

Moved here by the Metorite migration (board row **MG-7**). GitHub only reads
`.github/workflows/`, so a file in this directory cannot trigger.

These target Fracktal's VPS and its 5-minute pull timer against `/opt/acb/app`.
Two repositories self-deploying into one path fight on every push. Re-enable a
file by moving it back, once Metorite's own deploy target and Actions secrets exist.
