"""The Windows side of the program (TLY-5, brief section 6.7, build step 4).

Each part sits behind a seam that the rest of the program already has, so
the reader, the tools and the transport do not change:

* ``dpapi`` and ``stores``: Windows DPAPI at machine scope, behind
  ``SecretStore`` (the two hashes) and ``localcopy.KeyProvider`` (the key of
  the local copy).
* ``system``: the commands that change the machine. Start TallyPrime at
  sign-in (a Task Scheduler task), the firewall rule for ports 9000 and 8765,
  and keep the machine awake while the bridge runs.

Every Windows call goes through a small seam (a protector, a command runner,
a power API), so each test runs on Linux with a fake. This module imports
the settings, the secret store and the copy key seam, and nothing of the
reader, the tools or the transport. The fence is
``tests/unit/test_tally_bridge_fences.py``.
"""
