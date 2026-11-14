"""The trial workflow's `clore` command, as the tests run it: the item is done."""

import json
import sys

json.load(sys.stdin)
print(json.dumps({"decision": "advance", "reason": "élément clos", "to": None}))
