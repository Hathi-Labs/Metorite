"""The setup page of the bridge (brief section 6.7), served on 127.0.0.1 only.

It may import the reader, the settings and the secret store. The tools and
the reader never import it. A later connector reuses it with its own reader.
The fence is tests/unit/test_tally_bridge_fences.py.
"""

from tally_bridge.setup.routes import build_setup_routes

__all__ = ["build_setup_routes"]
