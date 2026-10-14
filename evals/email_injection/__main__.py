"""``python -m evals.email_injection --scripted`` runs :mod:`evals.email_injection.run`."""
from __future__ import annotations

import sys

from evals.email_injection.run import main

sys.exit(main())
