# Web research — Mira reads the web at large, on open-source parts (WS-WEB)

> **Product:** Metorite · **Feature:** web search, page and file reading, crawling, browsing and research jobs for the one assistant · **Updated:** 2026-11-04 · **Owner:** vjvarada
> **Status:** 🔨 **IN BUILD.** WEB-1 (the fetch guard) is built, 2026-11-01. WEB-2 (the local HTML reader) is built dark behind `WEB_RESEARCH_ORGS`, 2026-11-02, with Trafilatura in a child process. WEB-2b (the confinement of that child) is built, 2026-11-02, and it must merge before that flag flips. WEB-10, the goal record, is built on 2026-11-01 (§6.1): the table `goal_records` and its store, with no caller yet. WEB-3 (the file readers) is built dark behind `WEB_RESEARCH_ORGS`, 2026-11-04. MarkItDown reads PDF, DOCX, PPTX, XLSX, CSV and EPUB in the same child. pdfplumber reads a PDF that it cannot. A long file spills through CTX-1. The type refusal of §3.1 rule 5 is built with it. WEB-4 (the search provider list) is built dark, 2026-11-05. WEB-6 (the web worker container) is built, 2026-11-05, in the profile `webworker` with no caller. WEB-7 adds the caller, and WEB-5 waits on WEB-Q1. `web_search` and `fetch_page` existed before WEB-0 (§2).
> **Verified against code on 2026-11-05 at `44ca36243`.** Each path in §12 was read on that commit.
> **Board:** WS-WEB in `work_plan.md` §2.1. **Decision:** D148. D85, D117, D124, D129, D130 and D139 bind it.
> **Metorite:** project "Agent Tooling", epic 1235, this spec task 1237. The slices WEB-1 to WEB-12 are tasks 1259 to 1270, in order.
> **Research first:** §10 here, the open-source survey of 2026-11-01, with the licences.
> **Gate summary:** every slice is AGENT-SAFE, except WEB-5, which waits on WEB-Q1, and the live eval of WEB-12 above USD 0.50.

---

## 0. Thesis

The owner wants Mira to do in chat what a research agent does: search the web at large, read any page or file, crawl a site, use a browser, and write up what she found with sources. The owner wants it on open-source tools that we run, with no paid search key.

**One web service, many providers.** Every web read goes through one fetch guard and one set of readers. Every search goes through one provider list. The chat, AI Studio and the coding session (WS-43 CA-4) call the same service.

**The web is untrusted.** Every page that Mira reads is marked as outside text. A page can never change which tools Mira calls or where data goes.

**Research is a job, not a turn.** A research job has a goal, a cap on rounds and cost, parallel reads, notes and a report with citations.

---

## 1. Scope and non-goals

### 1.1 In scope

1. **The fetch guard** (§3.1): one SSRF check on every hop, caps, and the untrusted mark.
2. **Readers** (§3.2): HTML, PDF and Office files to Markdown on our own servers.
3. **Search providers** (§3.3): SearXNG, ddgs, SerpAPI with a key, and scholarly sources.
4. **The web worker** (§5): a container with a browser, a crawler and a heavy PDF reader.
5. **The tools Mira sees** (§4): `web_search`, `fetch_page`, `crawl_site`, `extract_from_pages`, `browse_*` and `research`.
6. **The goal record** (§6) for long jobs.

### 1.2 Non-goals

1. **No coding tools here.** Shell, edit, git and sub-agents for code stay in WS-43 CA-1 to CA-15 (`maf_coding_engine.md` §18, D139).
2. **No member logins in the browser in v1.** A page that needs a login stops and asks (WEB-Q2).
3. **No AGPL code in our process.** An AGPL tool runs only as its own unmodified service (§10).
4. **No hosted reader by default.** Jina's reader becomes opt-in, so a member's reading list does not leave by default (WEB-Q3).
5. **No paid search key needed.** SerpAPI stays as an option when a key is set.
6. **No code from a page runs on the box** outside the browser sandbox of the worker (D85).

---

## 2. What exists today

| Piece | Where | State |
|---|---|---|
| `web_search` | `packages/acb_skills/acb_skills/web_tools.py` | SerpAPI when `SERPAPI_API_KEY` is set, else the `ddgs` engines (DuckDuckGo, Bing, Brave, by rotation). Up to 10 results as plain text |
| `fetch_page` | same file | Sends the URL to `https://r.jina.ai`. If that fails, it fetches the page itself with `follow_redirects=True` and strips tags with a regex. Cap of 8,000 characters |
| Injection | `apps/services/orchestrator/orchestrator/_tool_injection.py` (line 48) | Both tools are added to every agent |
| The egress card | `packages/acb_skills/acb_skills/egress.py` | A direct egress tool such as `fetch_page` asks its card |
| The SSRF check | `packages/acb_common/acb_common/public_address.py` (`pinned_public_address`, `host_is_public`) | Shared since #1256. `gateway/outbound_guard.py` wraps it. `web_tools.py` does not use it |
| The untrusted mark | `packages/acb_skills/acb_skills/untrusted.py` (`UntrustedFrame`) | The one convention for outside text, with the fence `tests/unit/test_untrusted_frame.py` |
| A browser on the box | `apps/services/meeting_bot/` | Playwright 1.48 with Chromium in its own container. Proof that a browser worker can run here |
| The coding session's network | `maf_coding_engine.md` §18.4 CA-4 (task 1032) | Spec only: an egress proxy with an allow-list, `web_fetch` and `web_search` inside the sandbox |

**⚠️ A finding.** The direct fallback of `fetch_page` follows redirects and checks no address. A redirect to a private address is not refused in that code. The egress card checks no address, so nothing else closes it. The code also sends a private target to Jina before any check. WEB-1 closed it first, on 2026-11-01.

---

## 3. The web service

The service lives in one package, `packages/acb_skills/acb_skills/web/`. It replaces the body of `web_tools.py`. The tool names stay, so no agent config changes.

### 3.1 The fetch guard

Every web read goes through one function. The rules:

1. **Scheme.** `http` and `https` only.
2. **Address.** Resolve the host. Refuse it unless each address is public. Connect to the address that was checked. The rule is `acb_common.public_address`, which the gateway already uses. WEB-1 makes `web_tools.py` use it too, so the gateway and the orchestrator share one copy.
3. **Redirects.** Follow them by hand, at most 5. Each hop passes rules 1 and 2 again.
4. **Caps.** 20 seconds and 15 MB for each read. The reader output has its own cap, and an overflow spills through CTX-1 with a locator.
5. **Type.** HTML, PDF, the Office types, CSV, JSON, plain text and images. The guard refuses any other type and names it. WEB-3 builds this refusal, and WEB-1 refuses no type.
6. **The mark.** `UntrustedFrame` wraps the output as outside text. The header (title, the URL that gave the text, date, type) goes inside the frame, because a page controls it. WEB-2 adds the title and the date, because its reader reads them from the page.
7. **Jina.** A target passes rules 1 to 3 before any reader sees it, Jina included. Jina gets the URL of the call after the walk ends. Each hop that the walk reached passes the check first. A network fault can end the walk early. Then Jina's own network follows the hops that the walk did not reach.

### 3.2 Readers

The guard picks the reader by the content type.

| Input | Reader | Licence | Where it runs |
|---|---|---|---|
| HTML | **Trafilatura** (main text, title, author, date, as Markdown) | Apache-2.0 | In a child process of the gateway |
| HTML that needs JavaScript | **Crawl4AI** on Playwright Chromium | Apache-2.0 | The web worker (§5) |
| PDF, DOCX, PPTX, XLSX, CSV, EPUB | **MarkItDown** | MIT | In a child process of the gateway |
| A hard PDF (tables, scans, layout) | **Docling** | MIT | The web worker |
| Any page, when the owner opts in | Jina Reader (`r.jina.ai`) | Hosted | A third party |

**Fallback order for HTML:** Trafilatura. Then the worker render when the text is empty or too short. Then Jina, only when `WEB_READER_REMOTE=jina` is set and the local text is under 200 characters.

**As built, WEB-2 (2026-11-01).** The rules are rule 8 of the module docstring of `web_tools.py`.

- `WEB_RESEARCH_ORGS` turns the local reader on for an org. `web_research_on` is its one reader. The tool reads it on each call, and the org comes from the run binding. Off, `fetch_page` gives the WEB-1 output, byte for byte.
- Trafilatura 2.3.1 reads an HTML type: `text/html`, `application/xhtml+xml` or no type. It gets the first 192 KiB, as text and never as bytes, because it inflates bytes that look compressed.
- **Trafilatura work runs in a child process, because a regex holds the GIL.** A thread cannot bound it. One public page with a long `<meta name="author">` value stopped the event loop for 21 s, and no timeout could fire. The parent never imports Trafilatura.
- **WEB-3 (MarkItDown, pdfplumber) and each later parser must run the same way.**
- The child is `web_reader_worker.py`, started as a script with `python -I` and never with `-m`. A `-m` start runs the package `__init__` and costs about 1.2 seconds more. The child gets the `child_env` allowlist, so no API key and no database URL reach it. On Linux it caps its address space at 512 MiB and its CPU time at 10 seconds. The parent kills and reaps it at 8 seconds.
- One child serves one read, and that costs about 0.25 s on Linux and 1.2 s on the dev PC. A warm pool is a later optimisation, and only if that cost is bad.
- A read takes a slot before the child starts: 4 in the process and 2 for each org. With no slot, the read gives the strip at once and does not wait.
- No slot, a missing package, a crash, a bad answer, a timeout or an empty result gives the regex strip of WEB-1. Each fault writes one log line.
- The header lines are URL, Title, Date and Type. A line appears only when it has a value. `Date:` appears only when a `meta` tag, a `time` tag or a JSON-LD date field of the page states that date. A date in prose, a heading, a footer or a URL gives no line.
- The charset comes from the HTTP header, and then from a `<meta>` tag in the first 4 KiB.
- A binary type gets the WEB-3 line and no body. A body whose first 64 KiB decode to more than 10 percent NUL and U+FFFD characters gets the same line.
- Each other type gets the regex strip, and one line says that its reader comes with WEB-3.
- `WEB_READER_REMOTE=jina` asks Jina last, only when the local text is under 200 characters. Jina's text wins when it is longer.
- Fences: `tests/unit/test_web_readers.py` and `tests/unit/test_no_agpl_dependency.py`.

**As built, WEB-2b (2026-11-02).** The reader child is confined before it reads a page.

- `acb_skills/child_confine.py` is the one confinement module. The OCR child and the web child load it by path. WEB-3 and each later parser use it.
- The order of `main()` is the control. It closes every fd from 3 up and sets the limits. Then it confines (Landlock and seccomp), runs one warm-up page and closes the limits. Only then does it read the page.
- The child can read the Python and library dirs only. It cannot write, open a socket or signal its parent.
- The floor is Landlock ABI 1. A kernel with no Landlock, a machine that is not x86-64 or a second thread gives `{"fault": "unavailable"}`, and the read falls to the strip.
- `close_fds` is not in force under uvloop, so the child shuts the fds itself. The parent kills the process group at the end of each read, also when the leader exited 0.
- The seccomp deny list also holds `truncate`. It holds the calls that change a file in place too. These are `chmod`, `chown`, `utime` and the `xattr` families, each with its `f`, `l` and `at` forms. Landlock covers a truncate by path only from ABI 3, and it never covers a mode change. So the list protects `.env` at every ABI. `ftruncate` is not on the list. It needs an open writable fd, and Landlock refuses that open.
- The floor stays at ABI 1. The box runs ABI 4. A floor of 4 would turn the reader off on every older kernel, and the deny list closes the gap that ABI 1 leaves.
- The OCR child now closes its inherited fds first too. The OCR parent passes no fd on purpose. With no `/proc/self/fd` listing, the close runs to the hard `RLIMIT_NOFILE`, and a hard limit of infinity refuses.
- The parent kills the group after the reap. The group id can then go only to a new session leader, such as a concurrent reader or OCR child. That read falls to the strip. This has no security effect, and we accept it.
- Fence: `tests/unit/test_web_reader_confine.py`. The TCP rule of ABI 4 is proved on CI only. A test fails on GitHub Actions when the runner has an ABI below 4.

**As built, WEB-3 (2026-11-04).** `fetch_page` reads six file types, with `WEB_RESEARCH_ORGS` on. Off, the output is the WEB-1 output, byte for byte.

- **One child, a second mode.** `web_reader_worker.py` takes an extension as argv: `python -I web_reader_worker.py .pdf`. The parent picks the extension from `_FILE_TYPES` by the content type. A generic type (`application/octet-stream`) uses the extension of the URL path, if the table knows it. The file comes on stdin, and the child opens no path. `main()` keeps its order. It adds one step before the confinement: `mimetypes.init()`, because openpyxl reads `/etc/mime.types` at import, and Landlock refuses that read.
- **The reader.** MarkItDown 0.1.9 reads the file (extras pdf, docx, pptx, xlsx). Its EPUB reader needs no extra. pdfplumber 0.11.10 reads a PDF that MarkItDown cannot, or reads as empty. The child checks the first bytes of the file first (`%PDF-`, or `PK` for a zip type), because MarkItDown reads unknown bytes as plain text. The parent never imports a parser (fence: `test_web_files.py`).
- **Caps.** The guard keeps 15 MB. The file child has `FILE_TIMEOUT_S` (25 s), 20 s of CPU and 2 GiB of address space. Below about 1 GiB, MarkItDown hangs or faults (measured), so the cap stays at 2 GiB. A zip-bomb run peaked at about 1.2 GB resident, and the box has 7.9 GB. So file reads have their own slots inside the pool: 2 in the process and 1 for each org (`MAX_FILE_CHILDREN`, `MAX_FILE_CHILDREN_PER_ORG`). A page read keeps 4 and 2. With no file slot, the read falls back at once and writes one log line. The reader output cap is `MAX_FILE_TEXT_CHARS` (300,000 characters). Past it, the text ends with a line that says so.
- **The spill.** A file text longer than `max_chars` spills through the one CTX-1 store: `tool_result_spill.spill_result` keeps the framed text and returns its preview. The footer is the locator, `read_tool_result(ref=..., offset=...)`. It spills only with `TOOL_RESULT_SPILL` on, for a run that has an org, a member and a thread. Else the text is cut as a page is, with `[... truncated at N chars]`.
- **The mark.** The file text sits in one `WEB PAGE` block. The URL, the Type and the Title (when the file states one, as EPUB does) sit inside it.
- **Faults.** Each fault gives the WEB-2 read of the type, and one log line. A fault is no slot, a missing package, a crash, a bad answer, a timeout or an empty text. A PDF or an Office file then gets the binary line. CSV gets the strip.
- **The type refusal (§3.1 rule 5).** With the flag on, `_allowed_type` passes HTML, plain text (`text/*` and `application/javascript`), JSON, XML, PDF, Office, EPUB and images. Each other type gets one line that names the type (`zip`, `audio`, `video`, a font, an unknown generic). The name is cleaned to `type/subtype`, because the server writes it. With the flag off, nothing is refused.
- **The log.** Each file read writes one `web_reader.read` event: the URL with no query, the reader, the size in bytes, the characters and the spill ref.
- **Left for later.** Image text (OCR) and the old binary Office types (`.doc`, `.xls`, `.ppt`) keep the binary line. The line still says "comes later (WEB-3)", and a later change can reword it. Docling for a hard or scanned PDF is WEB-7.
- Fences: `tests/unit/test_web_files.py`, and `TestTheFileChild` in `tests/unit/test_web_reader_confine.py` (Linux).

### 3.3 Search providers

| Provider | Kind | Key | When it is offered |
|---|---|---|---|
| **SearXNG** (self-hosted, §5.2) | Metasearch: many engines in parallel, JSON | None | When `SEARXNG_URL` answers its health check |
| **ddgs** | Engine rotation by scraping | None | Always. It is the fallback |
| **SerpAPI** | Google results | `SERPAPI_API_KEY` | When the key is set |
| **OpenAlex, arXiv, Wikipedia** | Scholarly and reference sources | None | When the call names `source: "papers"` or `source: "wiki"` |

**A provider is offered only when it is ready.** The list skips a provider whose health check failed in the last 5 minutes. The result names the provider that answered. Results from two providers are merged by URL.

**As built, WEB-4 (2026-11-05).** `web_search` asks the provider list when `WEB_RESEARCH_ORGS` holds the org of the run. Off, the SerpAPI and ddgs chain runs as before, byte for byte.

- **One module.** `acb_skills/web_search_providers.py` holds the list. `web_tools.web_search` gains `source` (`web`, `papers`, `wiki`). Another value means `web`.
- **Order.** A web call asks SearXNG, SerpAPI and ddgs in that order. It stops when the merged hits fill `max_results`. A fault or an empty answer passes to the next provider. `papers` asks OpenAlex and arXiv together. `wiki` asks Wikipedia.
- **Ready.** SearXNG needs `SEARXNG_URL` and a `200` from `/healthz`. SerpAPI needs its key. A provider that faults, or fails its check, is skipped for `HEALTH_TTL_S` (300 s). A success clears the mark.
- **Stamp and merge.** A hit shows `Provider:` and `Date:`. The date is the one the source states. With none, it reads `retrieved <day>`. Two hits merge when their URLs match after the host is lower-cased, `www.`, the fragment, the default port, `utm_*` and the trailing slash are removed. The first provider keeps the hit and the others join `also`.
- **Safety.** `SEARXNG_URL` is an operator value (`env_guard` prefix `SEARXNG_`). Each answer has a 2 MB cap. arXiv XML with a DTD is refused. Hit text is one plain line with a cap.
- **Left for later.** Hit text has no `UntrustedFrame` mark yet. SearXNG itself is WEB-5.
- Fence: `tests/unit/test_web_search_providers.py`.

---

## 4. The tools Mira sees

| Tool | What it does | Class (D136) | Runs in |
|---|---|---|---|
| `web_search(query, max_results, source)` | Search, with the date and the provider of each hit | A | The orchestrator |
| `fetch_page(url, max_chars, render)` | Read one page or file as Markdown. `render: true` uses the worker | A | The orchestrator, or the worker |
| `crawl_site(url, max_pages, path_prefix)` | Read up to 25 pages of one host, with `robots.txt` honoured | A | The worker |
| `extract_from_pages(urls, fields)` | Fill named fields from each page into one table | A | The worker |
| `browse_open`, `browse_read`, `browse_click`, `browse_type`, `browse_screenshot`, `browse_close` | One interactive browser session | A for reads. **C** for a form submit or any non-GET request | The worker |
| `research(question, depth)` | A research job on a goal record (§6) | A, with a cost cap | The orchestrator, with parallel reads |

**Tool packs.** `web_search` and `fetch_page` stay in the floor. The rest join one pack, `web-research`, that loads on a turn that needs it (WS-67, D117). The pack holds at most 12 tools.

**A browse session** ends after 5 minutes with no call, or when the chat ends. It holds no cookies after it ends. A page that asks for a login stops the session and tells the member why.

---

## 5. Services on the box

### 5.1 The web worker

One container, `web_worker`, in the pattern of `apps/services/meeting_bot/`:

- Playwright Chromium, Crawl4AI, Docling and the Playwright MCP server.
- An internal HTTP API on a private port. Only the orchestrator calls it, with the internal token.
- **Egress.** No proxy of the fetch guard exists, so the worker holds the check. Every request of the page passes `context.route`: the top URL, each redirect and each subresource. The route calls `acb_common.public_address.pinned_public_address`, the one address rule (§3.1). A private target, the metadata address and `::1` get an abort. The compose network adds a second layer, and it never replaces this check.
- **Limits.** The worker runs as user `1000:1000`, not root. The numbers (all in `web_worker/limits.py`, and the compose block sets the first two):

  | Limit | Value |
  |---|---|
  | Memory (`mem_limit`) | 2 GiB |
  | CPU (`cpus`) | 2 |
  | Pages per job | 25 |
  | Sessions per organization at once | 2 |
  | Sessions in the container at once | 4 |
  | Timeout of one job | 60 s |
  | Requests per job | 200 |
  | Bytes of one document | 10 MiB |
  | Bytes of one subresource | 5 MiB |
  | Time of one address check | 5 s |
  | Bytes of one job | 40 MiB |
  | Fetches of one job at once | 6 |
  | Fetches in the container at once | 16 |
  | Address checks of one organization at once | 2 |

  The container drops all capabilities (`cap_drop: [ALL]`) and sets `no-new-privileges`. It binds `127.0.0.1` only (`test_compose_ports_local.py`).
- **The API.** `POST /render` takes a URL, an organization id and a job id. It answers with Markdown. The bearer token is the internal token. The worker refuses to start with an empty token. Docling joins in WEB-7 and Playwright MCP in WEB-9.
- **Every call names the organization.** The orchestrator sends the organization of the run binding, and the worker trusts it only behind the token. The worker holds no database. Each job gets a new browser context with no shared profile, and a directory made with mode `0700` under a root keyed by job id. The worker deletes both when the job ends. A job of org B cannot read or name a file of org A (`404`).
- **The build.** The context is the repo root, because the image installs `acb_common`. The module is never copied. The worker joins the AGPL fence: `test_no_agpl_dependency.py` reads its requirements too.

**As built, WEB-6 (2026-11-05).** The worker is `apps/services/web_worker/`. No caller exists, and WEB-7 adds the orchestrator call.

- **The pinned fetch.** Chromium fetches nothing itself. The route gives each request to `fetch.pinned_fetch` and fulfills it with the answer. The fetch resolves each host once, checks the address with `acb_common.public_address`, and connects to that address. `Host` and the TLS name stay the name of the URL. So a DNS answer that changes later (DNS rebinding) never reaches a socket.
- **Each hop.** A hop URL with a backslash, a control character, a space or userinfo gets a refusal before a parser reads it. Two parsers can read such a URL as two hosts. A hop to another origin gets no `Cookie`, `Authorization` or `Proxy-Authorization`.
- **Redirects.** Playwright does not route the next hop of a redirect. So the fetch follows each redirect itself, up to 5, and checks each hop first. Chromium never sees a `3xx`. A refused hop of the top document answers `422 refused`.
- **Caps.** The fetch reads at most 10 MiB of a document and 5 MiB of a subresource, decoded. It asks only for gzip and deflate, and it decodes them with a bound. It takes deflate in the zlib form and in the raw form. A job makes at most 200 requests. Each cap aborts the request.
- **The checks.** One pool of 8 threads runs the address checks. An organization runs 2 checks at once, for all its jobs. A hung check keeps its slot until its thread ends, also after its job ended. The wait for a slot and the resolve together have 5 s. A job checks each host once. The image sets `RES_OPTIONS=timeout:2 attempts:1`, so a hung DNS query in a pool thread ends in about 2 s.
- **The end of a job.** Each fetch runs in a task of the job scope, inside the deadline of the job. When the job ends, the scope cancels each fetch and each check that waits. A check in the pool queue of an ended job never runs.
- **The job budget.** A job reads at most 40 MiB, decoded, for all its answers. A decode step never gives more than the budget that is left. A job runs 6 fetches at once, and the container runs 16. The renderer asks the size of the DOM before it reads the page, and a page past 10 MiB gets `422 too large`.
- **Ports.** A hop uses port 80, 443 or the port of the top URL. No hop uses a port of Chromium's restricted list or a data-store port, such as 25 or 6379.
- **IDN.** A host with other letters parses, and the fetch sends its ASCII form as `Host` and as the TLS name.
- **Redirects and the URL.** Chromium gets the last answer of a redirect under the first URL. So a relative link of a redirected subresource resolves against the first URL. The Markdown of the top page uses the last URL.
- **The Markdown pool.** It has 4 threads, one for each job slot. A job that times out stops waiting, and its thread stays inside this pool.
- **Sockets and workers.** Each browser context blocks the service worker of a page. A route closes each WebSocket, and no socket reaches a server.
- **No network for Chromium.** Chromium gets a proxy address where nothing listens, also for loopback. So a preconnect, a favicon or a DNS prefetch goes nowhere. WebRTC gets no UDP.
- **The sandbox.** Chromium runs with the default `--no-sandbox` of Playwright. So the container is the only sandbox: uid 1000, no capabilities, `no-new-privileges`, a read-only root, a tmpfs `/tmp` and a pids limit of 1024. The 512 MiB tmpfs holds the job directories and the shared memory of Chromium, because Chromium runs with `--disable-dev-shm-usage`.
- **The files.** No HTTP route reads a job file. The store keys each job by organization and job id, so a job id of org A never gives org B a `409`.
- **The closure.** `requirements.lock.txt` pins the full closure with hashes. `licences.json` holds the licence of each package. The AGPL fence fails when the two do not name the same packages.
- **The compose block.** The block names the image `acb-web-worker:latest` and no build, because the root copy of the box does not hold the repo root. Build the image from a checkout. The block is in the profile `webworker` and binds `127.0.0.1:8096`. The network `web_worker_net` holds no other service. With no `WEB_WORKER_TOKEN`, the worker does not start.
- **The check in the image.** A Chromium probe used a fake public server and a fake private server. The probe refused a backslash redirect, a private hop late in a chain and a 12 MiB page. It also refused a private script, image, frame, fetch and socket.
- **Rebinding in the image.** In the same probe, Chromium's own DNS gave the private address for the page host. The worker connected to the checked address. The private server got no TCP connection, also from a preconnect and a favicon.
- **Load in the image.** A page with a slow image ended at the job timeout. Its fetch stopped within 1.5 seconds, and another organization then rendered in 0.1 seconds. A page with 200 gzip bombs kept the job at its 40 MiB budget, and the worker stayed under 300 MiB. A failed read frees its buffer at once.
- **Public pages.** Under the compose flags, `https://example.com/` returned Markdown, and an `http` to `https` redirect worked.
- **Open work.** The network layer does not block the metadata address. Only the worker check refuses it. A rule on the box for `169.254.169.254` is open work.

### 5.2 SearXNG

The official SearXNG image, unmodified, in its own container (WEB-Q1). It listens on a private port only. Its settings turn on the JSON output and the engines of WEB-Q4. The limiter is off for the internal caller, and the outbound rate stays low.

**Privacy.** A search query leaves for the engines that SearXNG asks. The settings send no member identity with it.

---

## 6. The goal record

A research job needs more than one turn. The goal record comes from DeepSeek Harness (`packages/goal/goal`).

- **One objective** for each job, kept across turns and restarts.
- **A round cap** (default 40 for research) and a cost cap in credits.
- **Compare-and-set** updates, so a stale view cannot overwrite a newer one.
- **Stable block codes**, such as `cap_reached`, `needs_member` and `source_unreachable`, each with one sentence for the member.
- **Permission to continue is not durable.** A restart asks again before a new round.

The table is tenant-scoped. WS-43 CA runs and Studio builds can use the same record later.

### 6.1 The record as built (WEB-10)

WEB-10 built the record and its store only. It added no route, no tool, no pack and no flag. WEB-11 is the first caller.

- **Table.** `goal_records`, from the migration that creates it (325). It has the tenant `organization_id` and FORCE RLS. The name is generic, because CA runs and Studio builds use it later.
- **Columns.** `organization_id`, `id`, `member_email`, `kind` (default `research`), `objective` (1 to 2000 characters), `state`, `version`, `round_count`, `round_cap`, `cost_used_credits`, `cost_cap_credits`, `block_code`, `job_id`, `created_at`, `updated_at` and `finished_at`.
- **`job_id`** names the `jobs` row that runs the goal. It has no foreign key. The goal is the state of the job, and the `jobs` queue stays as it is.
- **States.** `active` and `blocked` are open. `done`, `failed` and `cancelled` are final. A goal in a final state does not change again, and its `finished_at` is set.
- **Block codes.** The closed set is `cap_reached`, `needs_member` and `source_unreachable`. A CHECK holds the set, and the store holds one sentence for the member for each code. A goal is `blocked` if and only if it has a code.
- **Caps.** The round cap is a count of rounds, from 1 to 10000, and 40 for research. The cost cap is in AI credits, as NUMERIC(14, 4), the type of `usage_event.billed_credits`.
- **The cap rule.** A goal reaches its cap when `round_count >= round_cap` or `cost_used_credits >= cost_cap_credits`. The write that reaches a cap moves the goal to `blocked` with `cap_reached`. A CHECK keeps each active goal under both caps.
- **A round.** The worker records a round when it ends, so the cost cap stops the next round. A blocked or final goal takes no round.
- **Compare-and-set.** The token is the `version` integer. Each write names the version that the caller read, and raises it by one. A stale token raises `StaleGoal`, and nothing changes. A trigger refuses an UPDATE that does not raise the version by exactly one.
- **The objective never changes.** The store refuses a change. A trigger refuses a change of the objective, the member, the kind, the id, the org and `created_at`.
- **Who reads.** A goal is private to its member. The store filters each statement on `member_email`, and RLS filters on the org. No share to a group or to the org is in this slice.
- **Permission to continue is not durable.** No column holds a permission, a consent or an approval. A restart asks the member again before a new round.
- **Retention.** No pruning yet.
- **The store.** `packages/acb_common/acb_common/goal_store.py` is the one writer. It takes an async session that the caller opened with `tenant_session(org)`, and it opens none. It lives in `acb_common` because WEB-11 runs in the orchestrator, and the orchestrator cannot import gateway code.
- **The member purge** deletes the member's goals of the bound org.

---

## 7. Safety

1. **SSRF.** The guard of §3.1 binds every read, including the worker's.
2. **Prompt injection.** Every page is marked untrusted (§3.1 rule 6). The skill rules of the `web-research` pack say that page text is data, never an instruction. A page never changes which tools run or where data goes.
3. **Writes.** A form submit or a non-GET request in a browse session is class C and asks on the member's card every time (D124).
4. **Credentials.** No member credential enters the worker in v1 (WEB-Q2).
5. **Cost.** Each research job has a credit cap. Each model call writes a `usage_event` row.
6. **Scale.** Each organization has a cap on worker jobs at one time. A crawl honours `robots.txt` and waits between pages of one host.
7. **Licences.** No AGPL code runs in our process (§10).

---

## 8. Harness lessons carried here

The owner chose these on 2026-11-01 (D147, PKG-Q5). Each one lands in this workstream first, and the other workstreams can reuse it.

1. **Replay tests** (DeepSeek Harness `snapshots/`). A web turn is recorded once, with its model calls and its network replies. Then it replays with a stubbed model and a stubbed network. The tool chain gets free and repeatable tests (WEB-12).
2. **"Model-visible means logged."** Each page that Mira reads is logged as an event with its URL, reader, size and spill locator. A replay and an audit both need it (WEB-12).
3. **The goal record** (§6, WEB-10).

WS-CHAT owns the same lessons for the rest of the chat. This workstream proves them on web turns first.

---

## 9. Copilot SDK parity, web half

`maf_coding_engine.md` §18.2 holds the full matrix for code. This table covers the rows that touch the web, for the chat.

| Capability | Copilot SDK | Metorite after WS-WEB | Slice |
|---|---|---|---|
| URL fetch | A web fetch tool | `fetch_page` with local readers, the guard and render mode | WEB-1, WEB-2, WEB-7 |
| Web search | A search tool | `web_search` over SearXNG, ddgs, SerpAPI and scholarly sources | WEB-4, WEB-5 |
| Files | Through the shell | `fetch_page` reads PDF and Office files | WEB-3 |
| Browser use | Through MCP (Playwright MCP) | `browse_*` in the worker | WEB-9 |
| Crawl and extract | Not built in | `crawl_site`, `extract_from_pages` | WEB-8 |
| Long jobs | Plans and to-dos | `research` on the goal record | WEB-10, WEB-11 |
| The same in the coding session | — | CA-4 calls this service through its egress proxy | CA-4 (task 1032) |

---

## 10. What other apps do (open-source survey)

Research of 2026-11-01. The licence and the last commit of each repo were read from the GitHub API on that day.

| Tool | What it does | Licence | We take | We do not take |
|---|---|---|---|---|
| **SearXNG** | Self-hosted metasearch with a JSON API. Perplexica and Open WebUI use it | AGPL-3.0 | The search provider, as its own unmodified container (WEB-5) | Its code in our process |
| **ddgs** | Engine rotation with no key | MIT | The fallback (today's code) | — |
| **Trafilatura** | Main-text extraction from HTML, with metadata | Apache-2.0 | The default HTML reader (WEB-2) | — |
| **Crawl4AI** | A Playwright crawler that writes Markdown for an LLM, with deep crawl and extraction | Apache-2.0 | Render, crawl and extract in the worker (WEB-6 to WEB-8) | — |
| **MarkItDown** (Microsoft) | PDF, Office, EPUB and more to Markdown | MIT | The file reader (WEB-3) | — |
| **Docling** (IBM) | Layout-aware PDF and Office conversion, with tables and OCR | MIT | Hard PDFs in the worker (WEB-7) | — |
| **pdfplumber** | Light PDF text and tables | MIT | A fallback when MarkItDown fails | — |
| **Playwright** and **Playwright MCP** (Microsoft) | A browser, and browser control by the accessibility tree | Apache-2.0 | The browse session (WEB-9) | — |
| **browser-use**, **Stagehand** | LLM-driven browsing | MIT | Design sources for WEB-9 | A second agent loop beside MAF (D92) |
| **GPT Researcher**, **Perplexica** | Open research agents | Apache-2.0, MIT | The research loop design: plan, parallel reads, notes, report (WEB-11) | A second runtime |
| **Firecrawl** | Scrape, crawl and extract | AGPL-3.0 | — | AGPL in our process. Crawl4AI covers the same need |
| **PyMuPDF4LLM** | Fast PDF to Markdown | AGPL-3.0 | — | AGPL in our process |
| **Jina Reader** | A hosted reader. The repo is Apache-2.0 | Apache-2.0 (repo) | Opt-in only (WEB-3) | The default: it sends each URL to a third party. Its ReaderLM model is for non-commercial use |
| **DeepSeek Harness** | One `ctx.web` service with swappable search and fetch providers. A tool shows only when its provider is ready. Fetched text is marked untrusted | MIT | The service shape (§3), "offered when ready" (§3.3), the mark (§3.1), the goal record (§6) | In-process trust |

**Not verified.** The engine quality of SearXNG at our volume. The Docling cost on the box. Whether the ddgs engines hold up under load.

---

## 11. Slices

**Tier** is the review tier of `next-ticket.md` step 1b. Every slice ships dark behind `WEB_RESEARCH_ORGS` unless it fixes a defect. Migration numbers are taken at build time (R1).

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **WEB-1** | The fetch guard (§3.1). Route every `fetch_page` read and redirect hop through `acb_common.public_address`, pinned to the checked address. Refuse a private target before Jina sees it. Wrap the output in `UntrustedFrame` | A test refuses a URL that resolves to 127.0.0.1, 10.0.0.0/8, 169.254.169.254 and `::1`, and a public URL that redirects to one. A private target sends no request to Jina. The gateway's `outbound_guard.py` uses the same copy. The output carries the untrusted mark | HIGH | AGENT-SAFE |
| **WEB-2** | Trafilatura as the HTML reader. Jina becomes opt-in by `WEB_READER_REMOTE` | A test page gives its title and main text as Markdown with no menu text. With the flag off, the output is as before | STD | AGENT-SAFE |
| **WEB-3** | MarkItDown for PDF, DOCX, PPTX, XLSX, CSV and EPUB, with pdfplumber as the fallback, and the CTX-1 spill for a long file | Each type reads from a fixture. A file over the cap spills with a locator | STD | AGENT-SAFE |
| **WEB-4** | The search provider list (§3.3): SearXNG, ddgs, SerpAPI and the scholarly sources, offered only when ready | A provider with a failed health check is skipped. Each result names its provider and date. Results merge by URL | STD | AGENT-SAFE |
| **WEB-5** | The SearXNG container on the box (§5.2), private port only | `/healthz` answers from the orchestrator and from nowhere outside. `web_search` returns SearXNG results in a real turn. The log line names the provider | HIGH | **Waits on WEB-Q1 and WEB-Q4.** Deploy grant open until 2026-11-30 |
| **WEB-6** | The web worker container (§5.1) | The worker refuses a private address. It runs as non-root with its caps. One render returns Markdown. A second organization's job sees no file of the first | HIGH | AGENT-SAFE |
| **WEB-7** | `fetch_page(render: true)`, the automatic render when the static text is empty, and hard PDFs through Docling | A page that draws its text with JavaScript reads in full. A scanned PDF fixture gives its text | STD | AGENT-SAFE |
| **WEB-8** | `crawl_site` and `extract_from_pages` | A crawl stays on one host, honours `robots.txt` and stops at its page cap. An extract fills each named field or leaves it empty, never invented | STD | AGENT-SAFE |
| **WEB-9** | The browse session (§4), on Playwright MCP in the worker | A read runs at once. A form submit asks on the card. A session ends after 5 idle minutes and keeps no cookies. A login page stops the session | HIGH | AGENT-SAFE |
| **WEB-10** | The goal record (§6) | Expand-only migration (R6). R8: org B reads none of org A's goals. A stale update is refused. The cap stops a job with `cap_reached` | HIGH | AGENT-SAFE |
| **WEB-11** | The `research` job: a plan card, parallel search and reads, notes, and a report artifact with a citation for each claim | A job stops at its cost cap. Each claim in the report links to a page that the job read. Each model call writes a `usage_event` row | HIGH | AGENT-SAFE |
| **WEB-12** | Replay fixtures for web turns, the page-read events of §8, and a live eval of 20 research questions | A recorded turn replays with no network and no model. The eval reports the pass rate and the cost | STD | **OWNER-GATE** for a live run over USD 0.50 |

**The first dispatchable slice is WEB-1.** It fixes a defect, waits on no question, and every later slice reads through it.

---

## 12. Current file paths

Each path was read on `f59a636b5`.

- `packages/acb_skills/acb_skills/web_tools.py` (`web_search`, `fetch_page`, `_serpapi_search`)
- `packages/acb_skills/acb_skills/web_reader_worker.py` (the Trafilatura child, and from WEB-3 its file mode)
- `packages/acb_skills/acb_skills/tool_result_spill.py` (`spill_result`, the CTX-1 spill that `fetch_page` calls for a long file)
- `packages/acb_skills/acb_skills/egress.py` (the egress card)
- `packages/acb_skills/AGENTS.md` (item 4 names the web tools)
- `apps/services/orchestrator/orchestrator/_tool_injection.py` (line 48)
- `packages/acb_common/acb_common/public_address.py` (the shared public-address check)
- `apps/services/gateway/gateway/outbound_guard.py` (its wrapper, and the pinned transport pattern `_PinnedTransport`)
- `packages/acb_skills/acb_skills/untrusted.py` (`UntrustedFrame`)
- `apps/services/meeting_bot/Dockerfile`, `apps/services/meeting_bot/README.md` (the browser container pattern)
- `packages/acb_skills/acb_skills/tool_packs.py` (`PACKS`, `MAX_PACK_TOOLS`)
- `project-docs/specs/maf_coding_engine.md` §18 (CA-4, the coding session's network)

WEB-10 added these two paths on 2026-11-01:

- `infra/postgres/325_goal_records.sql` (the table `goal_records`)
- `packages/acb_common/acb_common/goal_store.py` (the one writer of `goal_records`)

---

## 13. Verification commands

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"   # R8 needs a database (WEB-10, WEB-11)
uv run python scripts/affected_tests.py --run
uv run pytest tests/unit/test_web_fetch_guard.py tests/unit/test_web_readers.py tests/unit/test_web_search_providers.py -q
uv run pytest tests/unit/test_web_worker.py tests/unit/test_no_agpl_dependency.py tests/unit/test_compose_ports_local.py -q   # WEB-6
docker build -f apps/services/web_worker/Dockerfile -t web-worker-check . && docker run --rm --entrypoint id web-worker-check -u   # WEB-6: must not print 0
uv run pytest tests/unit/test_goal_record_r8.py -q -rs                 # WEB-10. R8: it skips with no database
uv run ruff check packages/acb_skills packages/acb_common apps/services/orchestrator --select F821,F601,F602,F502,F7,B006   # the blocking set of pr-check. The full set holds old findings
bash scripts/ci_local.sh
```

---

## 14. Fences (R7)

| Rule | Fence |
|---|---|
| Every web read passes the guard | New with WEB-1: `tests/unit/test_web_fetch_guard.py` (private targets, redirect hops, and a scan that finds no `httpx` web read outside the guard) |
| The output of a web read is marked untrusted | New with WEB-1: same file |
| A provider is offered only when ready | New with WEB-4: `tests/unit/test_web_search_providers.py` |
| No AGPL package in our Python or Node lock files | New with WEB-2: `tests/unit/test_no_agpl_dependency.py` |
| The worker refuses a private target (top URL, redirect, subresource), runs as non-root, and keeps one org's job files from another | New with WEB-6: `tests/unit/test_web_worker.py` |
| A browse write asks on the card | New with WEB-9: `tests/unit/test_browse_session.py` |
| A parent never imports a parser, and the file child reads the six types, falls back to pdfplumber and spills a long file | New with WEB-3: `tests/unit/test_web_files.py` |
| The file child is confined and meets the same refusals as the page child | New with WEB-3: `TestTheFileChild` and `test_file_mode_refuses_each_attack_too` in `tests/unit/test_web_reader_confine.py` (Linux) |
| The guard refuses a type outside the list and names it | New with WEB-3: `tests/unit/test_web_files.py` (`test_the_guard_refuses_another_type_and_names_it`) |
| A new table is tenant-scoped | `tests/unit/test_tenant_coverage.py` |
| Org B reads no goal of org A, a member reads only their own goals, a stale write changes nothing, and the cap stops a job with `cap_reached` | WEB-10: `tests/unit/test_goal_record_r8.py` (R8) |
| The objective never changes, each UPDATE raises the version by one, and no column holds a permission | WEB-10: same file |
| The member purge deletes the member's goals of the bound org only | WEB-10: same file, and `tests/unit/test_admin_member_purge.py` |

---

## 15. Owner questions

| Id | Question | Recommendation |
|---|---|---|
| **WEB-Q1** | Do we run SearXNG (AGPL-3.0) as its own unmodified container? | **Yes.** It is the usual way to run it, and none of its code enters ours. Confirm the reading with your legal review |
| **WEB-Q2** | May a browse session use a member's own login? | **Not in v1.** It needs a vault, a consent card for each site, and an audit. A later spec |
| **WEB-Q3** | Does Jina's hosted reader become opt-in? | **Yes.** The local readers cover it, and a member's reading list stays with us |
| **WEB-Q4** | Which engines does SearXNG ask? | **DuckDuckGo, Brave, Bing, Wikipedia, arXiv and Mojeek first.** Add Google only after a check of its terms and of the block rate |
