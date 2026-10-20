"""CI trust and availability checks use offline API fixtures only."""

from copy import deepcopy
import io
import json
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from ai_knowledge.source import (
    CI_WORKFLOW, CiGateError, MAX_CI_RESPONSE, _NoRedirects, require_ci_success,
)


SOURCE = {'kind': 'git', 'url': 'https://github.com/Example/knowledge',
          'branch': 'master', 'manifest': 'shared/manifest.json', 'commit': 'a' * 40}


def successful_run(**changes):
    result = {
        'id': 123, 'run_attempt': 1, 'path': CI_WORKFLOW,
        'head_sha': SOURCE['commit'], 'head_branch': 'master', 'event': 'push',
        'repository': {'full_name': 'Example/knowledge', 'private': False},
        'head_repository': {'full_name': 'Example/knowledge', 'private': False},
        'created_at': '2026-10-20T01:00:00Z', 'updated_at': '2026-10-20T01:05:00Z',
        'status': 'completed', 'conclusion': 'success',
    }
    result.update(changes)
    return result


class Response(io.BytesIO):
    def __init__(self, raw, url, status=200):
        super().__init__(raw)
        self.url = url
        self.status = status
        self.requested_size = None

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.requested_size = size
        return super().read(size)


class ApiFixture:
    def __init__(self, runs=None, *, payload=None, raw=None, status=200, redirected=False):
        if payload is None:
            runs = [successful_run()] if runs is None else runs
            payload = {'total_count': len(runs), 'workflow_runs': runs}
        self.raw = json.dumps(payload).encode('utf-8') if raw is None else raw
        self.status = status
        self.redirected = redirected
        self.calls = []
        self.response = None

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        url = 'https://elsewhere.example/' if self.redirected else request.full_url
        self.response = Response(self.raw, url, self.status)
        return self.response


class SourceGateTests(unittest.TestCase):
    def test_accepts_only_the_requested_public_push_and_sends_no_credentials(self):
        fixture = ApiFixture()
        result = require_ci_success(SOURCE, opener=fixture)
        self.assertEqual(result, {'run_id': 123, 'run_attempt': 1,
                                  'workflow_path': CI_WORKFLOW, 'commit': SOURCE['commit']})
        request, timeout = fixture.calls[0]
        self.assertEqual(timeout, 10)
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(urlsplit(request.full_url).netloc, 'api.github.com')
        self.assertEqual(urlsplit(request.full_url).path, '/repos/Example/knowledge/actions/runs')
        self.assertEqual(parse_qs(urlsplit(request.full_url).query), {
            'head_sha': [SOURCE['commit']], 'branch': ['master'], 'event': ['push'],
            'per_page': ['100'],
        })
        self.assertNotIn('authorization', {name.casefold() for name in request.headers})
        self.assertNotIn('cookie', {name.casefold() for name in request.headers})
        self.assertTrue(fixture.response.closed)

    def test_transport_suffix_and_documented_workflow_ref_form_are_supported(self):
        for url in ('https://github.com/Example/knowledge.git',
                    'git@github.com:Example/knowledge.git'):
            with self.subTest(url=url):
                fixture = ApiFixture([successful_run(path=CI_WORKFLOW + '@master')])
                self.assertEqual(require_ci_success(dict(SOURCE, url=url), opener=fixture)['run_id'], 123)

    def test_pending_failed_cancelled_or_unaccepted_conclusions_keep_previous_version(self):
        for status, conclusion in (
                ('queued', None), ('in_progress', None), ('waiting', None),
                ('completed', 'failure'), ('completed', 'cancelled'),
                ('completed', 'neutral'), ('completed', 'skipped'), ('completed', None)):
            with self.subTest(status=status, conclusion=conclusion):
                fixture = ApiFixture([successful_run(status=status, conclusion=conclusion)])
                with self.assertRaisesRegex(CiGateError, '最新CI'):
                    require_ci_success(SOURCE, opener=fixture)

    def test_run_identity_must_match_repository_commit_branch_and_push_event(self):
        changes = [
            {'repository': {'full_name': 'stranger/knowledge', 'private': False}},
            {'head_repository': {'full_name': 'Example/fork', 'private': False}},
            {'repository': {'full_name': 'Example/knowledge', 'private': True}},
            {'head_repository': None}, {'head_sha': 'b' * 40},
            {'head_branch': 'other'}, {'event': 'pull_request'},
        ]
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaisesRegex(CiGateError, '一致'):
                    require_ci_success(SOURCE, opener=ApiFixture([successful_run(**change)]))
        for runs in ([], [successful_run(path='.github/workflows/another.yml')],
                     [successful_run(path=CI_WORKFLOW + '@other')]):
            with self.subTest(runs=runs):
                with self.assertRaisesRegex(CiGateError, '見つかりません'):
                    require_ci_success(SOURCE, opener=ApiFixture(runs))

    def test_latest_execution_wins_even_if_api_order_is_unexpected(self):
        older = successful_run(id=120, conclusion='failure',
                               created_at='2026-10-19T01:00:00Z',
                               updated_at='2026-10-19T01:05:00Z')
        newer = successful_run(id=124)
        for runs in ([newer, older], [older, newer]):
            with self.subTest(runs=runs):
                self.assertEqual(require_ci_success(SOURCE, opener=ApiFixture(runs))['run_id'], 124)
        newer['conclusion'] = 'failure'
        older['conclusion'] = 'success'
        with self.assertRaisesRegex(CiGateError, '最新CI'):
            require_ci_success(SOURCE, opener=ApiFixture([older, newer]))

    def test_a_later_rerun_cannot_fall_back_to_an_old_success(self):
        original = successful_run()
        rerun = successful_run(run_attempt=2, status='in_progress', conclusion=None,
                               updated_at='2026-10-20T03:00:00Z')
        for runs in ([original, rerun], [rerun, original]):
            with self.subTest(runs=runs):
                with self.assertRaisesRegex(CiGateError, '最新CI'):
                    require_ci_success(SOURCE, opener=ApiFixture(runs))
        second_id = successful_run(id=124, updated_at='2026-10-20T02:00:00Z')
        with self.assertRaisesRegex(CiGateError, '最新CI'):
            require_ci_success(SOURCE, opener=ApiFixture([second_id, rerun]))
        rerun.update(status='completed', conclusion='success')
        self.assertEqual(require_ci_success(SOURCE, opener=ApiFixture([original, rerun]))['run_attempt'], 2)

    def test_unavailable_redirected_and_non_success_http_responses_are_rejected(self):
        def unavailable(request, *, timeout):
            raise URLError('confidential response text must stay private')

        for opener in (unavailable, ApiFixture(status=429), ApiFixture(redirected=True)):
            with self.subTest(opener=opener):
                with self.assertRaises(CiGateError) as caught:
                    require_ci_success(SOURCE, opener=opener)
                self.assertNotIn('confidential', str(caught.exception))
        request = Request('https://api.github.com/repos/Example/knowledge/actions/runs')
        with self.assertRaises(HTTPError) as caught:
            _NoRedirects().redirect_request(request, None, 302, 'Moved', {},
                                            'https://elsewhere.example')
        caught.exception.close()

    def test_oversized_malformed_or_incomplete_responses_cannot_pass(self):
        fixtures = [
            ApiFixture(raw=b'x' * (MAX_CI_RESPONSE + 1)),
            ApiFixture(raw=b'not JSON'), ApiFixture(raw=b'\xff'),
            ApiFixture(raw=b'{"total_count":0,"total_count":0,"workflow_runs":[]}'),
            ApiFixture(payload=[]), ApiFixture(payload={'total_count': True, 'workflow_runs': []}),
            ApiFixture(payload={'total_count': 101, 'workflow_runs': [successful_run()]}),
            ApiFixture(payload={'total_count': 2, 'workflow_runs': [successful_run()]}),
            ApiFixture(payload={'total_count': 1, 'workflow_runs': [None]}),
        ]
        for fixture in fixtures:
            with self.subTest(raw_size=len(fixture.raw)):
                with self.assertRaises(CiGateError):
                    require_ci_success(SOURCE, opener=fixture)
                self.assertEqual(fixture.response.requested_size, MAX_CI_RESPONSE + 1)

    def test_malformed_run_fields_and_duplicates_are_rejected(self):
        changes = [{'id': True}, {'id': 0}, {'run_attempt': False}, {'run_attempt': -1},
                   {'created_at': None}, {'updated_at': 'malformed'},
                   {'updated_at': '2026-10-19T01:00:00Z'}, {'status': []}, {'conclusion': {}}]
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(CiGateError):
                    require_ci_success(SOURCE, opener=ApiFixture([successful_run(**change)]))
        missing = successful_run()
        del missing['conclusion']
        for runs in ([missing], [successful_run(), successful_run()]):
            with self.subTest(runs=runs):
                with self.assertRaises(CiGateError):
                    require_ci_success(SOURCE, opener=ApiFixture(runs))

    def test_invalid_sources_are_rejected_before_network_access(self):
        changes = [{'kind': 'file'}, {'url': '/not-a-github-url/remote.git'},
                   {'url': 'https://github.com/Example/knowledge?token=secret'},
                   {'url': 'https://user:password@github.com/Example/knowledge'},
                   {'url': 'https://github.com.evil.example/Example/knowledge'},
                   {'branch': '../master'}, {'branch': 'master\n'}, {'branch': False},
                   {'branch': '\ud800'},
                   {'manifest': '../manifest.json'}, {'manifest': '/manifest.json'},
                   {'manifest': '\ud800'}, {'commit': 'a' * 39}, {'commit': False}]
        for change in changes:
            fixture = ApiFixture()
            with self.subTest(change=change):
                with self.assertRaises(CiGateError):
                    require_ci_success(dict(SOURCE, **change), opener=fixture)
                self.assertEqual(fixture.calls, [])
        original = deepcopy(SOURCE)
        fixture = ApiFixture()
        require_ci_success(SOURCE, opener=fixture)
        self.assertEqual(SOURCE, original)


if __name__ == '__main__':
    unittest.main()
