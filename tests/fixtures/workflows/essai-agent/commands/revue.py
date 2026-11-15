"""The agent trial workflow's `revue` command, as the tests run it: the owner's answer decides, and `refaire` sends the
item back to its draft."""

import json
import sys

request = json.load(sys.stdin)
gesture = (request.get("answer") or {}).get("gesture")
if gesture == "valider":
    print(json.dumps({"decision": "advance", "reason": "revue validée", "to": "clore"}))
elif gesture == "refaire":
    print(json.dumps({"decision": "advance", "reason": "à refaire", "to": "brouillon"}))
else:
    print(json.dumps({"decision": "wait", "reason": "en attente d'un geste connu", "to": None}))
