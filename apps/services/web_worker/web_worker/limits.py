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

#: Address checks of one organization at once, for all its jobs. A check that
#: hangs keeps its slot until its thread ends, also after its job ended. So
#: one organization holds at most this many threads of the pool.
CHECKS_PER_ORG = 2

#: Seconds that one HTTP hop can take, to connect and to read.
HTTP_TIMEOUT_S = 20

#: Fetches of one job at once. A request past this count waits for a slot.
FETCHES_PER_JOB = 6

#: Fetches in the container at once, for all jobs.
MAX_FETCHES = 16

#: Decoded bytes that one job can read, for all its answers together. The
#: route aborts each request that would pass this count.
MAX_JOB_BYTES = 40 * 1024 * 1024

#: The ports that a hop can use: these two, and the port of the top URL of
#: the job. A port in UNSAFE_PORTS is refused, also as the port of the top URL.
DEFAULT_PORTS = frozenset({80, 443})

#: Chromium's list of restricted ports (net/base/port_util.cc), and the ports
#: of common data stores. No hop connects to one of them.
UNSAFE_PORTS = frozenset({
    1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79,
    87, 95, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135, 137,
    139, 143, 161, 179, 389, 427, 465, 512, 513, 514, 515, 526, 530, 531, 532,
    540, 548, 554, 556, 563, 587, 601, 636, 989, 990, 993, 995, 1719, 1720, 1723,
    2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566, 6665, 6666, 6667, 6668, 6669,
    6679, 6697, 10080,
    # Data stores and admin ports that Chromium does not list.
    2375, 2376, 3306, 5432, 6379, 9200, 11211, 27017,
})
