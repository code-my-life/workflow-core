"""A command that fails (tests of a `fail` decision)."""

import json
import sys

json.load(sys.stdin)
print(json.dumps({"decision": "fail", "reason": "préparation impossible", "to": None}))
