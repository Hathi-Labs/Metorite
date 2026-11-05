"""The limits of the web worker (WEB-6, ``web_research.md`` section 5.1).

The spec names each number here, and this file holds each one once. The
compose block in ``infra/docker-compose.yml`` sets the memory and the CPU.
Fence: ``tests/unit/test_web_worker.py``.
"""

from __future__ import annotations

#: Memory of the container (``mem_limit``). Compose sets it.
MEM_LIMIT = "2g"

#: CPUs of the container (``cpus``). Compose sets it.
CPUS = 2

#: Documents that one job can load: the top page, each redirect target and
#: each frame. The route aborts each document past this count.
MAX_PAGES_PER_JOB = 25

#: Jobs of one organization at once. A job past this count gets 429, and it
#: does not wait.
MAX_SESSIONS_PER_ORG = 2

#: Jobs in the container at once, for all organizations.
MAX_SESSIONS = 4

#: Seconds that one job can run, from the slot to the answer.
JOB_TIMEOUT_S = 60

#: The worker cuts the Markdown of a page to this size. This number is not in
#: the spec. It keeps one answer inside the memory of the container.
MAX_MARKDOWN_BYTES = 2 * 1024 * 1024

#: Redirects that the route follows for one request, each one checked. This
#: is the number of the fetch guard (section 3.1). Past it, the route aborts.
MAX_REDIRECTS = 5

#: Requests that one job can make: each request of the page and each hop of
#: a redirect. The route aborts each request past this count.
MAX_REQUESTS_PER_JOB = 200

#: Decoded bytes of one document answer. A bigger answer gets an abort.
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

#: Decoded bytes of one subresource answer. A bigger answer gets an abort.
MAX_SUBRESOURCE_BYTES = 5 * 1024 * 1024

#: Seconds that one address check can take. A slower check refuses the host.
CHECK_TIMEOUT_S = 5

#: Address checks of one job at once. A check that hangs keeps its thread
#: until it ends, so one job holds at most this many threads of the pool.
CHECKS_PER_JOB = 2

#: Seconds that one HTTP hop can take, to connect and to read.
HTTP_TIMEOUT_S = 20
