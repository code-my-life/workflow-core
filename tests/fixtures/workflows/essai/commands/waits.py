"""A command that waits (tests of a `wait` decision at a `code` station)."""

import json
import sys

json.load(sys.stdin)
print(json.dumps({"decision": "wait", "reason": "pas encore prêt", "to": None}))
