"""The trial workflow's `preparer` command, as the tests run it: an ordinary item goes to the review."""

import json
import sys

request = json.load(sys.stdin)
print("préparation de", request["item"]["id"], file=sys.stderr)
print(json.dumps({"decision": "advance", "reason": "élément préparé", "to": "revue"}))
