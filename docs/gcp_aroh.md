# Google Cloud for aroh: project, access, network and the dev box

<!-- ste-tier: strict -->

**Status:** the dev box `aroh-dev-1` runs, and its setup is finished
(2026-11-22, task 3899). `project-docs/HANDOFF.md` entries H-445 (step 4
only) to H-449 hold the open work.
**Verified:** 2026-11-22, by `gcloud` reads and checks on the box from the Mac of Ishaan Pilar (task 3899).
**Decisions:** D229 (the aroh name) and D230 (the dev box), in
`project-docs/work_plan.md` §3.

This page is the one reference for the Google Cloud setup. It states facts
and the command that re-checks each one. It holds no secret.

---

## 1. What this is

Another company wants the system on its own infrastructure. In that setting
the product takes the name **aroh**, not Metorite (D229). The Google Cloud project
holds that deployment. Today it holds one development box. Production still
runs on the Hostinger box and on Supabase (`deploy/hostinger/README.md`).

**The naming rule (D229).** Every Google Cloud resource takes the name `aroh`.
Examples: `aroh-vpc`, `aroh-dev-1`, `sa-aroh-dev`, the label `app=aroh`.
Google never changes a project ID, so the ID `metorite-513907` stays. The rule
covers Google Cloud only. It renames no code, no repo and no product text.

## 2. The project and who can act in it

| Item | Value |
|---|---|
| Project ID | `metorite-513907` (number `72341422828`) |
| Display name | "Metorite". The admin can change it to "aroh". |
| Billing | on |
| Region in use | Mumbai, `asia-south1`. Models use the location `global`. |
| Account on the PC | `vijay.v@dewin.in`, through `gcloud auth login` |
| Admin | the Google team (contact: Khan). We cannot read or change IAM. |

**What the account can do, 2026-11-19:**

| Can | Cannot |
|---|---|
| Vertex AI: call models | read or change the project IAM policy |
| create and change VMs, disks, networks, firewall rules, static IPs | create storage buckets |
| act as a service account (`iam.serviceAccounts.actAs`) | read logs |
| create Cloud SQL instances | SSH through IAP (`iap.tunnelInstances.accessViaIAP`) |
| create service accounts, API keys and secrets | give a role to a service account |

**Re-check one permission.** Send one permission per call. One bad name
rejects the whole list, and the answer then looks like "no access".

```bash
TOK=$(gcloud auth print-access-token)
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"permissions":["compute.instances.create"]}' \
  https://cloudresourcemanager.googleapis.com/v1/projects/metorite-513907:testIamPermissions
```

An empty `{}` means "no". An HTTP 429 means the read quota is full, not "no".
Wait, then ask again.

**Org policy:** the organization blocks service account key files
(`iam.disableServiceAccountKeyCreation`). Use an attached service account, or
workload identity federation. The policy allows a public IP on a VM.

## 3. The network

| Resource | Value |
|---|---|
| VPC | `aroh-vpc`, custom subnets, regional routing |
| Subnet `aroh-dev` | `asia-south1`, `10.40.0.0/20`, Private Google Access on |
| Firewall `aroh-vpc-allow-iap-ssh` | TCP 22 from `35.235.240.0/20` (Google IAP) to the tag `dev` |
| Firewall `aroh-vpc-allow-tailscale-udp` | UDP 41641 from anywhere to the tag `dev`, so Tailscale links direct and not through a relay (task 3899) |
| Firewall `aroh-vpc-tmp-ssh-office` | **Deleted** (2026-11-22). The `ufw` rule for the office IP on the box is gone too. |

**The address plan.** Keep `10.40.0.0/13` for aroh. No range may overlap
another, now or after a VPN joins a local network.

| Range | Use |
|---|---|
| `10.40.0.0/20` | `aroh-dev`, built |
| `10.40.16.0/20` | `aroh-prod`, the app box, later |
| `10.40.32.0/20` | `aroh-sandbox`, the sandbox box, later. No public IP. |
| `10.41.0.0/16` | Cloud SQL private IP, later |

Do not use these ranges. Local machines use them.

| Range | User |
|---|---|
| `192.168.0.0/16` | office and home networks (the PC is on `192.168.0.0/24`) |
| `172.16.0.0/12` | Docker and WSL |
| `100.64.0.0/10` | Tailscale |

**Resources that we did not make.** `metorite-vpc`, with `metorite-app-subnet`
(`10.0.1.0/24`) and `metorite-dev-subnet` (`10.0.2.0/24`), came from the Google
team on 2026-11-19. They are empty. Do not use them, and do not delete them.
H-447 asks the admin.

**The `default` network is unsafe.** Its rules open SSH and RDP to
`0.0.0.0/0`. Put no VM on it.

## 4. The dev box `aroh-dev-1` (D230)

| Setting | Value |
|---|---|
| Zone | `asia-south1-a` |
| Machine | `n2d-standard-16`: 16 vCPU, 64 GB, Standard provisioning |
| OS | Ubuntu 24.04 LTS (image family `ubuntu-2404-lts-amd64`) |
| Disk | `aroh-dev-1`, 300 GB `pd-balanced`, kept when the VM goes |
| Snapshots | `aroh-dev-daily`: 20:30 UTC each day, kept 7 days |
| Network | `aroh-dev`, internal `10.40.0.2`, external `34.47.154.167` (ephemeral) |
| Service account | `sa-aroh-dev@metorite-513907.iam.gserviceaccount.com`, scope `cloud-platform`, no roles yet (H-446) |
| Protection | Shielded VM, deletion protection |
| Labels and tag | `env=dev`, `app=aroh`, tag `dev` |
| Host name and Tailscale name | `aroh-dev-1` |

**Re-check it:**

```bash
gcloud compute instances describe aroh-dev-1 --project=metorite-513907 --zone=asia-south1-a \
  --format='yaml(status,machineType.basename(),labels,serviceAccounts[0].email,networkInterfaces[0].networkIP)'
```

**Why this size (D230).** The numbers come from `docs/dev_capacity.md` §3.

| Load at one time | Memory |
|---|---|
| Kept free for the system | 6 GB |
| One full local CI run (peak 17.7 GB) | 20 GB |
| One browser suite run | 8.5 GB |
| One `dev_up.sh` stack (estimate) | 3 GB |
| Eight agent lanes at 2 GB (estimate) | 16 GB |
| **Total** | **about 54 GB** |

Eight vCPU is too few. The home server ran its unit shards in 36 minutes,
against 14 on GitHub, when lanes and runners shared its CPU (2026-11-08).

**Cost, list price.** About USD 326 a month for the machine. The console
showed `n2d-standard-4` at USD 81.42, and the 16-vCPU type costs four times
that. The disk adds about USD 36. A stopped VM pays for its disk only.

**What the box must never hold:** customer data, production keys, or the
release timer. The home server keeps the release timer (`docs/home_box.md`).

## 5. How to reach the box

**Through Tailscale.** The box is on the tailnet as `aroh-dev-1`
(`100.64.118.14`), on the account of the home server. A direct link can
need 10 to 15 pings to form, so use `tailscale ping --c 15`. From a tailnet
machine:
`ssh dev@aroh-dev-1` and `ssh acb@aroh-dev-1`.

**Through Google IAP**, from a machine that is not on the tailnet. Use plain
`ssh` with an IAP tunnel. Put this in `~/.ssh/config`:

```
Host aroh-dev-1-iap
  HostName aroh-dev-1
  User dev
  IdentityFile ~/.ssh/google_compute_engine
  ProxyCommand gcloud compute start-iap-tunnel aroh-dev-1 22 --listen-on-stdin --zone=asia-south1-a --project=metorite-513907 --verbosity=warning
```

Then run `ssh aroh-dev-1-iap`. Your account needs the IAP role (H-446).
Put your public key in the **instance** metadata of `aroh-dev-1`, not in the
project metadata.

⚠️ **Do not use `gcloud compute ssh` for this box.** On each run it writes
your key to the project-wide metadata, so the key opens every VM in the
project. Task 3899 moved such a key to the instance metadata.

## 6. Setting up the box

`scripts/home_server/setup.sh` sets up the box. Its header says how. Run it
with two settings, because its defaults fit the home server:

```bash
BOX_NAME=aroh-dev-1 SETUP_NO_SIGNIN=1 bash setup.sh
```

**Three traps in that script on this box:**

1. **The name.** `BOX_NAME` defaults to `metorite-home`. Without the setting,
   the box takes the home server's name on the tailnet.
2. **The firewall.** Its `ufw` step allows SSH from private ranges and
   Tailscale only. A Google Cloud VM sees SSH from IAP and from the office
   as public addresses. The box already holds `ufw` rules for both, added
   before the script. Keep the IAP rule. The office rule is gone (2026-11-22).
3. **The keys.** It trusts the keys of `github.com/vjvarada.keys`. Add the key
   of each other person who needs `dev` or `acb`.

Then run `dev_toolchain.sh` and `dev_verify.sh` as `dev`. Sign in to Claude,
GitHub and the Metorite MCP as `dev` (`docs/home_box.md` §5).

## 7. Vertex AI: what works, measured 2026-11-19

| Model or route | Result |
|---|---|
| `gemini-4-argon`, `gemini-4-hydrogen`, `gemini-3.1-pro-preview`, `gemini-3.8-flash` | work, location `global` |
| `gemini-4-carbon` | refused: "Access requires an eligible paid billing account in a supported region" |
| Claude models (`claude-opus-5-5` and others) | listed, but "not found or your project does not have access". An admin must enable them in Model Garden. |
| The Gemini API key on the PC | free tier. Gemini Pro and Gemini 4 have zero quota on it. |

**Endpoints.** Both forms work with a Google OAuth token:

- native: `https://aiplatform.googleapis.com/v1/projects/metorite-513907/locations/global/publishers/google/models/<model>:generateContent`
- OpenAI shape: `https://aiplatform.googleapis.com/v1/projects/metorite-513907/locations/global/endpoints/openapi/chat/completions`, with model `google/<model>`

**A fixed API key does not work on Vertex.** The OpenAI endpoint answers 401,
"Expected OAuth 2 access token". The AI Router keeps one secret per provider
(`customer_console/router.py`, `provider_credential`). So the Router cannot
call Vertex today. H-449 holds the two ways forward.

**LiteLLM reaches Vertex** as `vertex_ai/<model>`, with `vertex_project` and
`vertex_location`, when the `google-auth` package is present. The repo's
`.venv` does not hold `google-auth` today.

**Trap: zsh changes `$m:generateContent`.** In zsh, `$var:g...` and
`$var:r...` are modifiers, so the URL breaks and Google answers with an HTML
404. Write `${m}:generateContent`, or run the loop under bash.

## 8. The coding eval of 2026-11-18 and 19

The eval replayed four merged PRs: #2490, #2349, #2561 and #2566. Each tool got
the PR text and its test files. Each tool could read and edit files only.

| Tool and model | Passed | Total time |
|---|---|---|
| Claude Code, Opus 5.5 (Max 20x plan) | 4 of 4 | about 8 min |
| Gemini CLI 0.67.0, `gemini-3.1-pro-preview` (Vertex) | 4 of 4 | about 29 min |
| Gemini CLI 0.67.0, `gemini-4-argon` (Vertex) | 2 of 4 | about 39 min |

Argon left a constant out on #2349 and hit the 25-minute limit on #2561. It
recorded no thinking tokens on three tasks, so its settings may need work.
Four tasks show a direction only. Gemini CLI stands in for the Antigravity
IDE. The Antigravity SDK (`google-antigravity` 0.1.23 on PyPI) supports
Vertex and an OpenAI-compatible agent config, and nobody has tested it here.

## 9. Open decisions

Each one has a HANDOFF entry with the recommendation.

- **The production database** (H-448). Recommendation: Cloud SQL, apart from
  the app box, as Supabase is today. First test the migration ladder and RLS
  on a small instance.
- **The production region.** The tenant database is in Supabase Tokyo
  (`ap-northeast-1`), and the app box is in Mumbai. Choose the app box's
  region together with the database.
- **The sandbox box** (D187, `specs/agent_sandbox.md` §4.6). Build it in
  `aroh-vpc`, at `10.40.32.0/20`, only when production moves here. D187 keeps
  member data with the provider that already processes it.
- **The Router and Vertex** (H-449).
