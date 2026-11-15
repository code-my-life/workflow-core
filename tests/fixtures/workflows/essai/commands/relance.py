"""A command that fails until the owner unblocks it (tests of the `unblock` gesture): with the answer `unblock`, the
item goes to the review."""

import json
import sys

request = json.load(sys.stdin)
answer = request.get("answer") or {}
if answer.get("gesture") == "unblock":
    print(json.dumps({"decision": "advance", "reason": f"relancé : {answer.get('note')}", "to": "revue"}))
else:
    print(json.dumps({"decision": "fail", "reason": "préparation impossible", "to": None}))
