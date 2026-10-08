"""MAF orchestrator — dynamic agent loader + executor (WBS 0.5, 0.7)."""

# WS-49 BH-1 (H-270): every GitHubCopilotAgent that starts in a process that
# imports the orchestrator builds its client with copilot_env(), so its CLI
# child gets no gateway secret. The import installs the class-level guard.
# Fence: tests/unit/test_copilot_child_env.py.
from orchestrator.copilot_agent import install_child_env_guard as _install_child_env_guard

_install_child_env_guard()
