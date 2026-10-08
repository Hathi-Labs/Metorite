"""acb_memory — Mem0 episodic memory + Graphiti bi-temporal KG.

Two memory layers:
- Mem0   : per-user episodic facts extracted from conversations.
           Uses our existing Postgres + pgvector (no new infra).
- Graphiti: bi-temporal entity knowledge graph.
           Uses Neo4j (see infra/docker-compose.yml).
           Feature-flagged via GRAPHITI_ENABLED=true.

Both are *optional* — if not configured, every public function returns a
graceful empty result and the agents work normally without memory.
"""
from acb_memory.compartments import Clearance, resolve_clearance
from acb_memory.mem0_client import (
    MemoryClient,
    get_memory_client,
    get_memory_context,
    add_memories_background,
    scope_key,
    get_scoped_context,
    add_scoped_memories,
    get_scoped_all,
    AGENT_SCOPE_PREFIX,
    ORG_SCOPE_KEY,
    PREFS_SCOPE_PREFIX,
    ROOM_SCOPE_PREFIX,
    scope_kind,
)
from acb_memory.graphiti_client import (
    GraphitiClient,
    get_graphiti_client,
    search_entity_timeline,
    add_episode,
)
from acb_memory.session_cache import (
    get_session_memory,
    invalidate_session_memory,
)
from acb_memory.blob_store import (
    put_file,
    get_file,
    list_files,
    delete_file,
    file_history,
    purge_files,
    session_paths,
    rehydrate_workspace,
    is_stored_path,
    folder_of,
    STORE_FOLDERS,
)

__all__ = [
    "MemoryClient",
    "get_memory_client",
    "get_memory_context",
    "add_memories_background",
    "scope_key",
    "get_scoped_context",
    "add_scoped_memories",
    "get_scoped_all",
    "AGENT_SCOPE_PREFIX",
    "ORG_SCOPE_KEY",
    "PREFS_SCOPE_PREFIX",
    "ROOM_SCOPE_PREFIX",
    "scope_kind",
    # Compartments — which scopes a run may read, and where its facts go.
    "Clearance",
    "resolve_clearance",
    "GraphitiClient",
    "get_graphiti_client",
    "search_entity_timeline",
    "add_episode",
    "get_session_memory",
    "invalidate_session_memory",
    "put_file",
    "get_file",
    "list_files",
    "delete_file",
    "file_history",
    "purge_files",
    "session_paths",
    "rehydrate_workspace",
    "is_stored_path",
    "folder_of",
    "STORE_FOLDERS",
]
