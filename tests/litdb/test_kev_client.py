from __future__ import annotations

import ast
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

from litdb.kev_client import CRITERIA, KevClient, relevance_request
from litdb.search import SearchError


@contextlib.contextmanager
def service(responder):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            payload = json.loads(raw)
            requests.append((self.path, payload))
            status, body, headers = responder(self.path, payload, len(requests))
            encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def bulk(payload, *, reverse=False):
    results = [dict(index=i, paper_id=paper['id'], choice='direct', probabilities=[0.7, 0.2, 0.1])
               for i, paper in enumerate(payload['papers'])]
    return dict(model='local-optimized', results=results[::-1] if reverse else results, truncated=False)


def official():
    return dict(model='kev-latest', answers={'relevance': dict(type='choice', choice='background',
        probabilities={'direct': 0.1001, 'background': 0.6, 'unrelated': 0.3})},
        usage={'input_tokens': 12}, truncated=False)


class KevClientTests(unittest.TestCase):
    def setUp(self):
        self.papers = [dict(id='one', title='Graph placement', abstract='An abstract.'),
                       dict(id='two', title='Vision transformer', abstract=None)]
        self.topic = 'Graph neural networks for chip placement'

    def test_bulk_reordered_response_is_restored_to_input_order(self):
        def responder(path, payload, _):
            self.assertEqual(path, '/v1/rerank')
            return 200, bulk(payload, reverse=True), {}

        with service(responder) as (url, requests):
            result = KevClient(url).rerank(self.topic, self.papers)
        self.assertEqual([r['paper_id'] for r in result['results']], ['one', 'two'])
        self.assertEqual(result['model'], 'local-optimized')
        self.assertEqual(result['endpoint'], url + '/v1/rerank')
        self.assertEqual(result['metadata'], {'fallback': False, 'requests': 1})
        self.assertEqual(requests[0][1], {'papers': [dict(id='one', title='Graph placement', abstract='An abstract.', topic=self.topic),
            dict(id='two', title='Vision transformer', abstract='', topic=self.topic)]})
        self.assertTrue(all(set(r['probabilities']) == set(CRITERIA) for r in result['results']))

    def test_only_404_or_405_falls_back_to_sequential_systemone(self):
        for unsupported in (404, 405):
            def responder(path, payload, _):
                if path == '/v1/rerank':
                    return unsupported, {'detail': 'not supported'}, {}
                return 200, official(), {}

            with self.subTest(status=unsupported), service(responder) as (url, requests):
                result = KevClient(url).rerank(self.topic, self.papers)
            self.assertEqual([path for path, _ in requests], ['/v1/rerank', '/v1/systemone', '/v1/systemone'])
            self.assertEqual(requests[1][1], relevance_request(self.topic, self.papers[0]))
            self.assertEqual(requests[2][1], relevance_request(self.topic, self.papers[1]))
            self.assertEqual(result['endpoint'], url + '/v1/systemone')
            self.assertEqual(result['metadata'], {'fallback': True, 'requests': 3})
            self.assertEqual([r['paper_id'] for r in result['results']], ['one', 'two'])
            self.assertAlmostEqual(sum(result['results'][0]['probabilities'].values()), 1)
            self.assertAlmostEqual(result['results'][0]['probabilities']['background'], 0.6 / 1.0001)

    def test_empty_candidates_make_no_http_request(self):
        with service(lambda *_: (500, {}, {})) as (url, requests):
            self.assertEqual(KevClient(url).rerank(self.topic, []),
                {'results': [], 'model': None, 'endpoint': None, 'elapsed_ms': 0})
            self.assertEqual(requests, [])

    def test_request_matches_existing_fixed_engine_rubric_without_importing_engine(self):
        # Read source via AST only; the model checkout is never a dependency.
        engine = ROOT / 'data/literature-db/search/benchmarks/kev-opt-20261009/engine.py'
        if not engine.is_file():
            self.skipTest('Developer benchmark is absent from standalone installs')
        tree = ast.parse(engine.read_text())
        criteria_node = next(node.value for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'CRITERIA' for target in node.targets))
        request_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'request')
        isolated = ast.Module(body=[request_node], type_ignores=[])
        namespace = {'CRITERIA': ast.literal_eval(criteria_node)}
        exec(compile(isolated, str(engine), 'exec'), namespace)
        self.assertEqual(CRITERIA, namespace['CRITERIA'])
        expected = namespace['request']({**self.papers[0], 'topic': self.topic})
        self.assertEqual(relevance_request(self.topic, self.papers[0]), expected)

    def test_invalid_bulk_identity_order_and_result_count_rejected(self):
        def duplicate(body):
            body['results'][1]['index'] = 0
            body['results'][1]['paper_id'] = 'one'

        mutations = [duplicate,
            lambda b: b['results'][0].update(index=True),
            lambda b: b['results'][0].update(index=2),
            lambda b: b['results'][0].update(paper_id='two'),
            lambda b: b['results'][0].pop('index'),
            lambda b: b['results'].pop(),
            lambda b: b['results'].append(dict(b['results'][0])),
            lambda b: b.update(results={})]
        for mutation in mutations:
            def responder(path, payload, _):
                body = bulk(payload)
                mutation(body)
                return 200, body, {}

            with self.subTest(mutation=mutation), service(responder) as (url, requests):
                with self.assertRaises(SearchError):
                    KevClient(url).rerank(self.topic, self.papers)
                self.assertEqual(len(requests), 1)

    def test_invalid_probabilities_and_choice_rejected(self):
        invalid = [[True, 0, 0], [-0.1, 0.8, 0.3], [1.1, 0, 0], [float('nan'), 0, 0],
                   [float('inf'), 0, 0], ['0.7', 0.2, 0.1], [0, 0, 0], [0.7, 0.2],
                   [0.7, 0.2, 0.11], {'direct': 0.7, 'background': 0.2},
                   {'direct': 0.7, 'background': 0.2, 'unrelated': 0.1, 'extra': 0}, [10**1000, 0, 0]]
        for values in invalid:
            def responder(path, payload, _):
                body = bulk(payload)
                body['results'][0]['probabilities'] = values
                return 200, body, {}

            with self.subTest(values=values), service(responder) as (url, _):
                with self.assertRaises(SearchError):
                    KevClient(url).rerank(self.topic, self.papers)
        for choice in ('background', 'unknown', [], None):
            def responder(path, payload, _):
                body = bulk(payload)
                body['results'][0]['choice'] = choice
                return 200, body, {}

            with self.subTest(choice=choice), service(responder) as (url, _):
                with self.assertRaises(SearchError):
                    KevClient(url).rerank(self.topic, self.papers)

    def test_truncated_output_rejected_for_bulk_and_fallback(self):
        for route, location in [('bulk', 'body'), ('bulk', 'result'), ('official', 'body'), ('official', 'usage')]:
            def responder(path, payload, _):
                if route == 'official' and path == '/v1/rerank':
                    return 404, {}, {}
                body = bulk(payload) if route == 'bulk' else official()
                if location == 'result':
                    body['results'][0]['truncated'] = True
                elif location == 'usage':
                    body['usage'].update(state_tokens=100, state_tokens_used=99)
                else:
                    body['truncated'] = True
                return 200, body, {}

            with self.subTest(route=route, location=location), service(responder) as (url, _):
                with self.assertRaisesRegex(SearchError, 'truncated|incomplete'):
                    KevClient(url).rerank(self.topic, self.papers)

    def test_nonfallback_http_errors_are_not_retried(self):
        for status in (400, 401, 422, 429, 500):
            with self.subTest(status=status), service(lambda *_: (status, {'detail': 'error'}, {})) as (url, requests):
                with self.assertRaisesRegex(SearchError, str(status)):
                    KevClient(url).rerank(self.topic, self.papers)
                self.assertEqual(len(requests), 1)

    def test_fallback_429_is_not_retried(self):
        def responder(path, *_):
            return (404 if path == '/v1/rerank' else 429), {}, {}

        with service(responder) as (url, requests):
            with self.assertRaisesRegex(SearchError, '429'):
                KevClient(url).rerank(self.topic, self.papers)
        self.assertEqual(len(requests), 2)

    def test_fallback_model_change_and_missing_model_fail(self):
        def responder(path, payload, count):
            if path == '/v1/rerank':
                return 404, {}, {}
            body = official()
            if count == 3:
                body['model'] = 'changed-model'
            return 200, body, {}

        with service(responder) as (url, _):
            with self.assertRaisesRegex(SearchError, 'model changed'):
                KevClient(url).rerank(self.topic, self.papers)

        def no_model(path, payload, count):
            body = bulk(payload)
            body.pop('model')
            return 200, body, {}

        with service(no_model) as (url, _):
            with self.assertRaisesRegex(SearchError, 'model name'):
                KevClient(url).rerank(self.topic, self.papers)

    def test_invalid_json_or_official_schema_rejected(self):
        invalid_json = [b'{', b'[]', b'{"model":"one","model":"two"}', b'\xff']
        for encoded in invalid_json:
            with self.subTest(encoded=encoded), service(lambda *_: (200, encoded, {})) as (url, _):
                with self.assertRaisesRegex(SearchError, 'malformed JSON'):
                    KevClient(url).rerank(self.topic, self.papers)
        for answer in ({}, {'answers': []}, {'answers': {'other': {}}},
                       {'model': 'kev-latest', 'answers': {'relevance': {'type': 'score'}}}):
            def responder(path, *_):
                return (404, {}, {}) if path == '/v1/rerank' else (200, answer, {})

            with self.subTest(answer=answer), service(responder) as (url, _):
                with self.assertRaises(SearchError):
                    KevClient(url).rerank(self.topic, self.papers)

    def test_shared_deadline_stops_sequential_fallback(self):
        def responder(path, payload, count):
            if path == '/v1/rerank':
                time.sleep(0.045)
                return 404, {}, {}
            time.sleep(0.045)
            return 200, official(), {}

        with service(responder) as (url, requests):
            with self.assertRaisesRegex(SearchError, 'timeout'):
                KevClient(url, timeout=0.07).rerank(self.topic, self.papers)
        self.assertEqual([path for path, _ in requests], ['/v1/rerank', '/v1/systemone'])

    def test_response_size_bound(self):
        with service(lambda *_: (200, b'x' * (8 * 1024 * 1024 + 1), {})) as (url, _):
            with self.assertRaisesRegex(SearchError, '8 MiB'):
                KevClient(url).rerank(self.topic, self.papers)

    def test_proxy_environment_is_ignored_and_redirects_refused(self):
        with service(lambda _, payload, __: (200, bulk(payload), {})) as (url, requests):
            with patch.dict(os.environ, {'http_proxy': 'http://remote.invalid:9', 'HTTP_PROXY': 'http://remote.invalid:9', 'ALL_PROXY': 'http://remote.invalid:9', 'NO_PROXY': ''}):
                result = KevClient(url).rerank(self.topic, self.papers)
            self.assertEqual(len(result['results']), 2)
            self.assertEqual(len(requests), 1)
        with service(lambda *_: (302, {}, {'Location': 'http://example.com/v1/rerank'})) as (url, requests):
            with self.assertRaisesRegex(SearchError, 'redirect'):
                KevClient(url).rerank(self.topic, self.papers)
            self.assertEqual(len(requests), 1)

    def test_url_and_timeout_validation(self):
        for url in ('http://127.0.0.1:8019', 'http://localhost:8019/', 'https://[::1]:8019', 'http://127.2.3.4:9'):
            self.assertTrue(KevClient(url).url)
        invalid = ['https://example.com', 'http://192.168.1.1', 'http://0.0.0.0', 'http://127.0.0.1.example.com',
                   'http://127.1', 'http://2130706433', 'file:///tmp/kev', 'http://user@localhost',
                   'http://localhost:0', 'http://localhost:65536', 'http://localhost:', 'http://localhost/?',
                   'http://localhost/#', 'http://localhost/?key=x', 'http://localhost/#fragment',
                   'http://localhost\\@example.com', 'http://localhost\n', 'http://[::1%zone]:80', '', None]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                KevClient(url)
        for timeout in (True, False, '180', 0, -1, 3601, 10**1000, float('nan'), float('inf'), None):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                KevClient(timeout=timeout)

    def test_paper_validation_and_input_are_not_mutated(self):
        before = json.dumps(self.papers)
        relevance_request(self.topic, self.papers[1])
        self.assertEqual(json.dumps(self.papers), before)
        invalid = [(self.topic, self.papers * 2), (self.topic, [{'id': 'x', 'title': 'title', 'abstract': 3}]),
                   (self.topic, [{'id': 'x', 'title': 3}]), (self.topic, [{'id': True, 'title': 'title'}]),
                   (self.topic, [{'id': 'x', 'title': 'x' * 10001}]),
                   (self.topic, [{'id': 'x', 'title': 'title', 'abstract': 'x' * 50001}]),
                   (self.topic, [None]), (self.topic, [{}]), (self.topic, self.papers[:1] * 501),
                   ('', self.papers), (True, self.papers), ('x' * 10001, self.papers)]
        with service(lambda *_: (500, {}, {})) as (url, requests):
            client = KevClient(url)
            for topic, papers in invalid:
                with self.subTest(topic=str(topic)[:20], papers=str(papers)[:40]), self.assertRaises(ValueError):
                    client.rerank(topic, papers)
            self.assertEqual(requests, [])


if __name__ == '__main__':
    unittest.main()
