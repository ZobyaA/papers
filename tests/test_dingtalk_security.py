"""Focused push-path tests without importing the retrieval/PDF/AI dependencies."""
import ast
import base64
import contextlib
from datetime import datetime
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, quote_plus, urlsplit

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'testtoken123'
SECRET = 'SECtestsecret123'
WEBHOOK = f'https://oapi.dingtalk.com/robot/send?access_token={TOKEN}'


def functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    selected += [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in names]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


class RequestStub:
    def __init__(self, result=None, error=None, status=200):
        self.result, self.error, self.status = result, error, status
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(url)
        if self.error:
            raise self.error
        class Response:
            def raise_for_status(inner):
                if self.status >= 400:
                    raise RuntimeError(f'HTTP {self.status}: {url}')

            def json(inner):
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result
        return Response()


class DingTalkSecurityTests(unittest.TestCase):
    def setUp(self):
        self.requests = RequestStub({'errcode': 0, 'errmsg': 'ok'})
        self.ns = functions('common.py', ['DingTalkConfigError', 'load_dingtalk_credentials', 'DingTalkRobot'], {
            'os': os, 'urlsplit': urlsplit, 'parse_qs': parse_qs,
            'hmac': hmac, 'hashlib': hashlib, 'base64': base64, 'quote_plus': quote_plus,
            'time': time, 'requests': self.requests, 'json': json,
        })

    def test_missing_partial_and_placeholder_credentials(self):
        for values in ({}, {'DINGTALK_WEBHOOK': WEBHOOK},
                       {'DINGTALK_SECRET': SECRET},
                       {'DINGTALK_WEBHOOK': WEBHOOK, 'DINGTALK_SECRET': '****'},
                       {'DINGTALK_WEBHOOK': 'https://oapi.dingtalk.com/robot/send?access_token=****',
                        'DINGTALK_SECRET': SECRET},
                       {'DINGTALK_WEBHOOK': 'https://example.com/robot/send?access_token=abc',
                        'DINGTALK_SECRET': SECRET}):
            with self.subTest(values=tuple(values)), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(ValueError) as caught:
                    self.ns['load_dingtalk_credentials']()
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertNotIn(SECRET, str(caught.exception))
        self.assertFalse(self.requests.calls)

    def test_success_and_untrusted_response_are_sanitized(self):
        with patch.dict(os.environ, {'DINGTALK_WEBHOOK': WEBHOOK, 'DINGTALK_SECRET': SECRET}, clear=True):
            robot = self.ns['DingTalkRobot'](*self.ns['load_dingtalk_credentials']())
            self.assertEqual(robot.send_markdown('title', 'text')['errcode'], 0)
            self.assertIn('sign=', self.requests.calls[0])
            self.requests.result = {'errcode': 400, 'errmsg': f'{TOKEN} {SECRET} sign=abc'}
            result = robot.send_markdown('title', 'text')
            self.assertEqual(result['errcode'], 400)
            self.assertNotIn(TOKEN, str(result))
            self.assertNotIn(SECRET, str(result))
            self.assertNotIn('sign=', str(result))

    def test_network_exception_url_is_not_logged_or_returned(self):
        self.requests.error = RuntimeError(f'{WEBHOOK}&sign=private-sign {SECRET}')
        robot = self.ns['DingTalkRobot'](WEBHOOK, SECRET)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = robot.send_markdown('title', 'text')
        self.assertEqual(result['errcode'], -1)
        for sensitive in (TOKEN, SECRET, 'private-sign', 'sign='):
            self.assertNotIn(sensitive, out.getvalue() + str(result))

    def test_signing_exception_is_sanitized(self):
        robot = self.ns['DingTalkRobot'](WEBHOOK, SECRET)

        def fail_sign(timestamp):
            raise ValueError(f'{TOKEN} {SECRET} sign=private-sign')

        robot._generate_sign = fail_sign
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = robot.send_markdown('title', 'text')
        self.assertEqual(result['errcode'], -1)
        self.assertFalse(self.requests.calls)
        for sensitive in (TOKEN, SECRET, 'private-sign', 'sign='):
            self.assertNotIn(sensitive, out.getvalue() + str(result))

    def test_http_failure_and_malformed_json_never_succeed(self):
        robot = self.ns['DingTalkRobot'](WEBHOOK, SECRET)
        self.requests.status = 500
        self.assertEqual(robot.send_markdown('title', 'text')['errcode'], -1)
        self.requests.status = 200
        for payload in (ValueError(f'bad JSON: {TOKEN}'), [], {}, {'errcode': '0'},
                        {'errcode': True}, {'errcode': 400, 'errmsg': SECRET}):
            with self.subTest(payload=type(payload).__name__):
                self.requests.result = payload
                result = robot.send_markdown('title', 'text')
                self.assertNotEqual(result['errcode'], 0)
                self.assertNotIn(TOKEN, str(result))
                self.assertNotIn(SECRET, str(result))

    def test_unified_push_dry_run_and_missing_credentials(self):
        marked = []
        notices = []
        namespace = {
            'datetime': datetime, 'os': os, '_push_log': notices.append,
            'collect_results': lambda: [('domain', [{'link': 'paper'}], [], '')],
            'load_sent_papers': lambda: set(),
            'deduplicate_papers': lambda papers, sent: papers,
            'generate_markdown_content': lambda *args: 'preview',
            'mark_papers_sent': lambda papers: marked.extend(papers),
            'load_dingtalk_credentials': self.ns['load_dingtalk_credentials'],
            'DingTalkConfigError': self.ns['DingTalkConfigError'],
            'DingTalkRobot': self.ns['DingTalkRobot'],
        }
        functions('push_papers.py', ['push'], namespace)
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            namespace['push'](dry_run=True)
            namespace['push']()
        self.assertFalse(self.requests.calls)
        self.assertFalse(marked)
        self.assertTrue(any('缺少' in line for line in notices))

    def test_unified_push_network_failure_does_not_mark_sent(self):
        marked = []
        notices = []
        self.requests.error = RuntimeError(f'{WEBHOOK}&sign=private-sign {SECRET}')
        namespace = {
            'datetime': datetime, '_push_log': notices.append,
            'collect_results': lambda: [('domain', [{'link': 'paper'}], [], '')],
            'load_sent_papers': lambda: set(),
            'deduplicate_papers': lambda papers, sent: papers,
            'generate_markdown_content': lambda *args: 'preview',
            'mark_papers_sent': lambda papers: marked.extend(papers),
            'load_dingtalk_credentials': self.ns['load_dingtalk_credentials'],
            'DingTalkConfigError': self.ns['DingTalkConfigError'],
            'DingTalkRobot': self.ns['DingTalkRobot'],
        }
        functions('push_papers.py', ['push'], namespace)
        with patch.dict(os.environ, {'DINGTALK_WEBHOOK': WEBHOOK, 'DINGTALK_SECRET': SECRET}, clear=True):
            result = namespace['push']()
        self.assertEqual(result[0][1]['errcode'], -1)
        self.assertFalse(marked)
        for sensitive in (TOKEN, SECRET, 'private-sign', 'sign='):
            self.assertNotIn(sensitive, str(result) + str(notices))

    def test_unified_push_marks_sent_only_after_http_and_dingtalk_success(self):
        marked = []
        namespace = {
            'datetime': datetime, '_push_log': lambda message: None,
            'collect_results': lambda: [('domain', [{'link': 'paper'}], [], '')],
            'load_sent_papers': lambda: set(),
            'deduplicate_papers': lambda papers, sent: papers,
            'generate_markdown_content': lambda *args: 'preview',
            'mark_papers_sent': lambda papers: marked.extend(papers),
            'load_dingtalk_credentials': self.ns['load_dingtalk_credentials'],
            'DingTalkConfigError': self.ns['DingTalkConfigError'],
            'DingTalkRobot': self.ns['DingTalkRobot'],
        }
        functions('push_papers.py', ['push'], namespace)
        with patch.dict(os.environ, {'DINGTALK_WEBHOOK': WEBHOOK, 'DINGTALK_SECRET': SECRET}, clear=True):
            self.requests.status = 500
            self.assertEqual(namespace['push']()[0][1]['errcode'], -1)
            self.assertFalse(marked)
            self.requests.status = 200
            self.requests.result = {'errcode': 400, 'errmsg': 'rejected'}
            self.assertEqual(namespace['push']()[0][1]['errcode'], 400)
            self.assertFalse(marked)
            self.requests.result = {'errcode': 0, 'errmsg': 'ok'}
            self.assertEqual(namespace['push']()[0][1]['errcode'], 0)
            self.assertEqual(len(marked), 1)

    def test_retrieval_push_uses_same_config_and_reports_failure(self):
        paper = {'link': 'paper', 'relevance_score': 8}
        namespace = {
            'datetime': datetime, 'sys': sys,
            'setup_logging': lambda path: (io.StringIO(), sys.stdout),
            'load_keywords_and_authors': lambda kws, path: kws,
            'fetch_arxiv_papers': lambda *args, **kwargs: (True, [paper], ''),
            'load_sent_papers': lambda: set(),
            'deduplicate_papers': lambda papers, sent: papers,
            'rank_and_select_top_papers': lambda papers, **kwargs: papers,
            'analyze_papers_with_ai': lambda papers, *args, **kwargs: papers,
            'save_titles_to_file': lambda *args: None,
            'save_latest_json': lambda *args: None,
            'generate_markdown_content': lambda *args: 'preview',
            'extract_authors_from_log': lambda *args, **kwargs: [],
            'load_dingtalk_credentials': self.ns['load_dingtalk_credentials'],
            'DingTalkConfigError': self.ns['DingTalkConfigError'],
            'DingTalkRobot': self.ns['DingTalkRobot'],
        }
        functions('common.py', ['run_retrieval_pipeline'], namespace)
        args = (['keyword'], 'prompt', 'requirement', 'log.txt', 'authors.json')
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
            namespace['run_retrieval_pipeline'](*args, push_papers=True)
        self.assertIn('缺少 DINGTALK_WEBHOOK', out.getvalue())
        self.assertIn("'errcode': -1", out.getvalue())
        self.assertFalse(self.requests.calls)
        self.requests.error = RuntimeError(f'{WEBHOOK}&sign=private-sign {SECRET}')
        with patch.dict(os.environ, {'DINGTALK_WEBHOOK': WEBHOOK, 'DINGTALK_SECRET': SECRET}, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
            namespace['run_retrieval_pipeline'](*args, push_papers=True)
        self.assertEqual(len(self.requests.calls), 1)
        self.assertIn("'errcode': -1", out.getvalue())
        for sensitive in (TOKEN, SECRET, 'private-sign', 'sign='):
            self.assertNotIn(sensitive, out.getvalue())

        def broken_robot(*args):
            raise ValueError(f'{WEBHOOK}&sign=private-sign {SECRET}')

        namespace['DingTalkRobot'] = broken_robot
        with patch.dict(os.environ, {'DINGTALK_WEBHOOK': WEBHOOK, 'DINGTALK_SECRET': SECRET}, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
            namespace['run_retrieval_pipeline'](*args, push_papers=True)
        self.assertIn("'errcode': -1", out.getvalue())
        for sensitive in (TOKEN, SECRET, 'private-sign', 'sign='):
            self.assertNotIn(sensitive, out.getvalue())


if __name__ == '__main__':
    unittest.main()
