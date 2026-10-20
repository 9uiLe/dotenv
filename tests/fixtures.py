"""Small, harmless shared knowledge used by the offline ai_knowledge."""

import json


def make_bundle(version: str = 'v1') -> dict:
    if not isinstance(version, str) or not version:
        raise ValueError('version must be a non-empty string')
    context = '共有設定 ' + version
    script = (
        '"""Read the event and return sample context; no side effects."""\n'
        'import json\n'
        'import sys\n\n'
        'event = json.load(sys.stdin)\n'
        'if not isinstance(event, dict):\n'
        '    raise ValueError("hook input must be an object")\n'
        'print(json.dumps({"hookSpecificOutput": {\n'
        '    "hookEventName": "SessionStart",\n'
        '    "additionalContext": ' + json.dumps(context, ensure_ascii=False) + '\n'
        '}}, ensure_ascii=False))\n'
    )
    return {
        'version': version,
        'rules': '変更の目的と確認結果を短く説明する。共有設定 ' + version + '\n',
        'files': {
            'skills/ai-knowledge-example/SKILL.md': (
                '---\n'
                'name: ai-knowledge-example\n'
                'description: 変更の目的と確認結果を短く整理するための架空の例。\n'
                '---\n\n'
                '変更内容・目的・確認結果をそれぞれ一文で説明する。\n'
                '共有設定 ' + version + '\n'
            ),
            'hooks/session_start.py': script,
        },
        'hooks': [{
            'id': 'shared-version',
            'event': 'SessionStart',
            'script': 'hooks/session_start.py',
            'tools': ['codex', 'claude'],
            'timeout': 5,
        }],
    }
