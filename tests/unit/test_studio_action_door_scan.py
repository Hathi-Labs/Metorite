"""The route-file scan of the studio door (WS-STU B2b-2b).

CIV-33f (task 3223). This test lists the route files, so a file that holds it is a
scanner file. It lives apart from ``test_studio_action_door.py``. The body is unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

from gateway.routes.studio import invoke


class TestTheDoorOpFences:
    _STUDIO = Path(invoke.__file__).parent

    def test_no_studio_route_file_reads_the_internal_token(self):
        """R7 (B2b-2b): the door forwards the inbound bearer, and it never
        reads the internal token to mint one. A read of the token in any file
        of ``routes/studio`` fails here."""
        pattern = re.compile(r"gateway_internal_token|_get_internal_token|internal_token",
                             re.IGNORECASE)
        files = sorted(self._STUDIO.glob("*.py"))
        assert {p.name for p in files} >= {"invoke.py", "door_op.py"}
        for path in files:
            assert not pattern.search(path.read_text(encoding="utf-8")), path.name
