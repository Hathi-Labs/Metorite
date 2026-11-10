"""The orchestration playbook (PB-1, D186).

Plain Markdown topics with a header. One loader reads them. The edge (prompts,
resources) and the gateway (the default guidance layer) read this one package.
The content holds no rule that belongs to one organization (D186.2).
"""

from acb_playbook.loader import PlaybookError, Topic, load_all, load_topic

__all__ = ["PlaybookError", "Topic", "load_all", "load_topic"]
