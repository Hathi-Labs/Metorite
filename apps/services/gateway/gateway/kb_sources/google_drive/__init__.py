"""The Google Drive adapter of a Knowledge source (WS-61 KC-1a).

``auth`` holds the consent, the token life and the scope lists. ``client``
holds :class:`GoogleDriveSource`, the reads of the folder tree.
"""

from gateway.kb_sources.google_drive.client import PROVIDER, GoogleDriveSource

__all__ = ["PROVIDER", "GoogleDriveSource"]
