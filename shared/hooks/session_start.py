"""Read the event and return sample context; no side effects."""
import json
import sys

event = json.load(sys.stdin)
if not isinstance(event, dict):
    raise ValueError("hook input must be an object")
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "共有設定 example-v1"
}}, ensure_ascii=False))
