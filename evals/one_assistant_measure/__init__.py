"""WS-67 OA-5: measure the email tool pack against delegation (``specs/one_assistant_context.md``)."""
import os

# The harness never needs the live LiteLLM price map. Without this, the first import of
# ``litellm`` (through the gateway) tries raw.githubusercontent.com. The package backup is the same data.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
