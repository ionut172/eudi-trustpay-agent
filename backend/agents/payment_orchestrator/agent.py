"""Google ADK agent definition compatible with `adk web` and `adk run`."""

import os
import sys
from pathlib import Path

# Ensure backend root is on sys.path
backend_dir = str(Path(__file__).resolve().parent.parent.parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.agents.orchestrator import get_orchestrator

# Root agent exposed for ADK CLI / Web UI
orchestrator = get_orchestrator()
root_agent = orchestrator.agent
