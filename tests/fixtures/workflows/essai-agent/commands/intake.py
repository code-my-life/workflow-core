"""The trial workflow's `intake` command, as the tests run it: an issue becomes an item unless its title or its labels
say otherwise."""

import json
import sys

issue = json.load(sys.stdin)["issue"]
if issue["title"].startswith("[refus]"):
    print(json.dumps({"decision": "refuse", "reason": "titre marqué [refus]"}))
elif any(label.startswith("type:") for label in issue["labels"]):
    print(json.dumps({"decision": "refuse", "reason": "une issue typée appartient à la factory"}))
else:
    print(json.dumps({"decision": "accept", "id": str(issue["number"]), "reason": "issue d'essai acceptée"}))
