"""Require a successful CI run before applying a received public GitHub commit."""

from datetime import datetime, timezone
import json
from pathlib import PurePosixPath
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


CI_WORKFLOW = '.github/workflows/ci.yml'
MAX_CI_RESPONSE = 2 * 1024 * 1024
CI_TIMEOUT = 10
_OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
_ENDPOINT = re.compile(
    r'(?:https://github\.com/|git@github\.com:)'
    r'([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_][A-Za-z0-9_.-]*)'
)


class CiGateError(ValueError):
    """CI could not be verified; callers must preserve the previous shared state."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise HTTPError(request.full_url, code, 'Redirects are disabled', headers, response)


def _source_identity(source):
    if (not isinstance(source, dict)
            or set(source) != {'kind', 'url', 'branch', 'manifest', 'commit'}
            or source.get('kind') != 'git'):
        raise CiGateError('CIを確認する受信元の形式が不正です')
    url = source['url']
    match = _ENDPOINT.fullmatch(url) if isinstance(url, str) else None
    if not match or (url.startswith('git@') and not url.endswith('.git')):
        raise CiGateError('CIの確認先は公開GitHubリポジトリに限定しています')
    owner, repository = match.groups()
    if repository.endswith('.git'):
        repository = repository[:-4]
    if not repository:
        raise CiGateError('CIの確認先が不正です')
    branch = source['branch']
    if (not isinstance(branch, str) or not branch or len(branch) > 1024
            or branch.startswith(('-', '/')) or branch.endswith(('/', '.'))
            or branch == '@' or '..' in branch or '@{' in branch or '//' in branch
            or any(ord(char) < 33 or ord(char) == 127 or char in '~^:?*[\\'
                   for char in branch)
            or any(part.startswith('.') or part.endswith('.lock') for part in branch.split('/'))):
        raise CiGateError('CIを確認するブランチが不正です')
    try:
        branch.encode('utf-8')
    except UnicodeError:
        raise CiGateError('CIを確認するブランチが不正です') from None
    manifest = source['manifest']
    if (not isinstance(manifest, str) or not manifest or '\\' in manifest
            or len(manifest) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in manifest)):
        raise CiGateError('CIを確認する受信元パスが不正です')
    try:
        if len(manifest.encode('utf-8')) > 4096:
            raise CiGateError('CIを確認する受信元パスが不正です')
    except UnicodeError:
        raise CiGateError('CIを確認する受信元パスが不正です') from None
    path = PurePosixPath(manifest)
    if path.is_absolute() or '..' in path.parts or str(path) != manifest or manifest == '.':
        raise CiGateError('CIを確認する受信元パスが不正です')
    if not isinstance(source['commit'], str) or not _OID.fullmatch(source['commit']):
        raise CiGateError('CIを確認するコミットが不正です')
    return owner, repository


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value):
        raise CiGateError('CIの実行情報が不正です')
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise CiGateError('CIの実行情報が不正です') from None


def _repository_matches(value, expected):
    return (isinstance(value, dict) and isinstance(value.get('full_name'), str)
            and value['full_name'].casefold() == expected.casefold()
            and value.get('private') is False)


def _latest_ci_run(payload, source, repository):
    if (not isinstance(payload, dict) or type(payload.get('total_count')) is not int
            or not isinstance(payload.get('workflow_runs'), list)):
        raise CiGateError('CIの応答形式が不正です')
    runs = payload['workflow_runs']
    if not 0 <= payload['total_count'] <= 100 or payload['total_count'] != len(runs):
        raise CiGateError('CIの実行一覧を完全に確認できません。前の共有版を保持します')
    candidates = {}
    seen = set()
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get('path'), str):
            raise CiGateError('CIの実行情報が不正です')
        if run['path'] not in {CI_WORKFLOW, CI_WORKFLOW + '@' + source['branch']}:
            continue
        if (run.get('head_sha') != source['commit'] or run.get('head_branch') != source['branch']
                or run.get('event') != 'push'
                or not _repository_matches(run.get('repository'), repository)
                or not _repository_matches(run.get('head_repository'), repository)):
            raise CiGateError('CIの実行元が受信コミットと一致しません')
        if any(type(run.get(key)) is not int or run[key] <= 0 for key in ('id', 'run_attempt')):
            raise CiGateError('CIの実行情報が不正です')
        identity = run['id'], run['run_attempt']
        if identity in seen:
            raise CiGateError('CIの実行情報が重複しています')
        seen.add(identity)
        created = _timestamp(run.get('created_at'))
        updated = _timestamp(run.get('updated_at'))
        if updated < created:
            raise CiGateError('CIの実行日時が不正です')
        if (not isinstance(run.get('status'), str) or 'conclusion' not in run
                or not (run.get('conclusion') is None or isinstance(run['conclusion'], str))):
            raise CiGateError('CIの実行状態が不正です')
        record = {'run': run, 'order': (updated, created, run['id'])}
        current = candidates.get(run['id'])
        if current is None or current['run']['run_attempt'] < run['run_attempt']:
            candidates[run['id']] = record
    if not candidates:
        raise CiGateError('受信コミットのCIが見つかりません。前の共有版を保持します')
    # A rerun keeps its run ID. Prefer that ID's newest attempt, then the most
    # recently updated execution across IDs so a later rerun cannot hide failure.
    return max(candidates.values(), key=lambda record: record['order'])['run']


def require_ci_success(source: dict, *, opener=None) -> dict:
    """Read public GitHub CI status without credentials; never apply shared files.

    ``opener`` can be replaced by an offline response fixture. It receives an
    HTTPS Request and ``timeout=10`` and returns a context-managed HTTP response.
    CI unavailability, incomplete data, pending runs and failures all raise
    ``CiGateError`` so the applying transaction can retain its previous version.
    """
    owner, name = _source_identity(source)
    query = urlencode({'head_sha': source['commit'], 'branch': source['branch'],
                       'event': 'push', 'per_page': 100})
    url = f'https://api.github.com/repos/{owner}/{name}/actions/runs?{query}'
    request = Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2026-03-10',
        'User-Agent': 'ai-knowledge-ci-gate',
    })
    if opener is None:
        opener = build_opener(ProxyHandler({}), _NoRedirects()).open
    try:
        with opener(request, timeout=CI_TIMEOUT) as response:
            if type(response.status) is not int or response.status != 200 or response.geturl() != url:
                raise CiGateError('GitHubのCI確認先が一致しないか、正常に応答しませんでした')
            raw = response.read(MAX_CI_RESPONSE + 1)
        if len(raw) > MAX_CI_RESPONSE:
            raise CiGateError('CIの応答がサイズ上限を超えています')
        payload = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    except HTTPError as error:
        error.close()
        raise CiGateError('GitHubのCIを確認できません。前の共有版を保持します') from None
    except (URLError, OSError, TimeoutError):
        raise CiGateError('GitHubのCIを確認できません。前の共有版を保持します') from None
    except (UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, CiGateError):
            raise
        raise CiGateError('CIの応答を読み取れません。前の共有版を保持します') from None
    run = _latest_ci_run(payload, source, owner + '/' + name)
    if run['status'] != 'completed' or run['conclusion'] != 'success':
        raise CiGateError('最新CIの成功を確認できません。前の共有版を保持します')
    return {'run_id': run['id'], 'run_attempt': run['run_attempt'],
            'workflow_path': CI_WORKFLOW, 'commit': source['commit']}
