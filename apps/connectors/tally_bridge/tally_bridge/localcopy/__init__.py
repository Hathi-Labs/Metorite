"""The local copy (TLY-4, ``integrations_console.md`` §3.8.13).

While TallyPrime is open, the bridge keeps an encrypted copy of the ledgers
and of the vouchers of this financial year and the last one, for each allowed
company. With TallyPrime closed, ``find_ledgers``, ``list_vouchers`` and
``get_voucher`` answer from the copy and name its age. A copy older than the
maximum age answers nothing. A write never uses the copy (writes are rung 3).

The owner's answers of 2026-11-22 on task 3769 (prompt 5b7d7a06):

* LNK-Q8: no copy inside Metorite. The copy stays on the customer's machine.
* LNK-Q9: a copy every 30 minutes while TallyPrime is open, and a maximum age
  of 7 days. ``COPY_INTERVAL_MIN`` and ``COPY_MAX_AGE_DAYS`` change both.

Modules: ``keys`` (the key provider: DPAPI on Windows in build step 4, a file
for a dev box), ``store`` (one encrypted file for each company), ``sync`` (the
first copy and the copy of changes by AlterID) and ``schedule`` (the loop).

It imports the reader and never the tools or the transport. The tools import
it to read. The fence is tests/unit/test_tally_bridge_fences.py.
"""

from tally_bridge.localcopy.keys import DevFileKeyProvider, KeyProvider, MemoryKeyProvider
from tally_bridge.localcopy.store import CopyStore, Snapshot

__all__ = ["CopyStore", "DevFileKeyProvider", "KeyProvider", "MemoryKeyProvider", "Snapshot"]
