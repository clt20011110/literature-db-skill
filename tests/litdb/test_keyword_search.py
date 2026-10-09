from __future__ import annotations

import contextlib
import fcntl
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

from litdb.keyword_search import KeywordSearchError, ensure_index, query
from litdb import keyword_search


class KeywordSearchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.home = Path(self.folder.name) / 'search'
        self.home.mkdir()
        self.source = self.home / 'papers.sqlite'
        with self.connection() as connection:
            connection.executescript('''
                CREATE TABLE papers(id TEXT PRIMARY KEY,title TEXT,abstract TEXT);
                CREATE TABLE editions(paper_id TEXT,venue TEXT,year INTEGER);
            ''')

    @contextlib.contextmanager
    def connection(self):
        connection = sqlite3.connect(self.source)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def tearDown(self):
        self.folder.cleanup()

    def add(self, identity, title, abstract='', editions=None):
        with self.connection() as connection:
            connection.execute('INSERT INTO papers VALUES(?,?,?)', (identity, title, abstract))
            connection.executemany('INSERT INTO editions VALUES(?,?,?)',
                [(identity, venue, year) for venue, year in (editions or [('icml', 2020)])])

    def ids(self, phrases, options=None, limit=500):
        return {hit['id'] for hit in query(self.home, phrases, options or {}, limit)}

    def test_literal_phrase_and_or_recall_with_attributed_phrases(self):
        self.add('graph', 'Graph neural network placement', 'A congestion method.')
        self.add('vision', 'Vision transformer', 'Graph-based neural models form a network.')
        self.add('both', 'Vision transformer uses a graph neural network')
        self.add('cross-field', 'Graph neural', 'network')
        self.add('unrelated', 'Protein folding')
        hits = query(self.home, ['graph neural network', 'vision transformer'], {}, 500)
        self.assertEqual({hit['id'] for hit in hits}, {'graph', 'vision', 'both'})
        attributed = {hit['id']: hit['matched_phrases'] for hit in hits}
        self.assertEqual(attributed['graph'], ['graph neural network'])
        self.assertEqual(attributed['both'], ['graph neural network', 'vision transformer'])
        self.assertTrue(all(isinstance(hit['score'], float) and hit['score'] > 0 for hit in hits))
        self.assertEqual(hits, sorted(hits, key=lambda hit: (-hit['score'], hit['id'])))

    def test_fts_operator_and_sql_syntax_are_literal(self):
        self.add('graph', 'graph')
        self.add('vision', 'vision')
        self.add('literal-or', 'graph OR vision')
        self.add('literal-column', 'title graph')
        self.add('literal-near', 'NEAR graph vision 2')
        self.assertEqual(self.ids(['graph OR vision']), {'literal-or'})
        self.assertEqual(self.ids(['title:graph']), {'literal-column'})
        self.assertEqual(self.ids(['NEAR(graph vision, 2)']), {'literal-near'})
        self.assertEqual(self.ids(['"graph" OR "vision"']), {'literal-or'})
        self.assertEqual(self.ids(["x'); DROP TABLE papers; --"]), set())
        self.assertEqual(self.ids(['*']), set())
        with self.connection() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM papers').fetchone()[0], 5)

    def test_token_boundaries_case_and_unicode_diacritics(self):
        self.add('unicode', 'CAFÉ naïve Müller', 'Δίκτυο graph.')
        self.add('longer', 'Graphical models')
        self.assertEqual(self.ids(['cafe naive muller']), {'unicode'})
        self.assertEqual(self.ids(['δίκτυο']), {'unicode'})
        self.assertEqual(self.ids(['graph']), {'unicode'})
        self.assertEqual(self.ids(['naive', 'NAIVE']), {'unicode'})

    def test_chinese_substring_inside_unspaced_sentence_and_mixed_phrase(self):
        self.add('chinese', '利用图神经网络优化芯片布局', '本方法缓解布线拥塞问题。')
        self.add('mixed', '图神经网络 GRAPH placement')
        self.add('wrong-order', '神经图网络')
        hits = query(self.home, ['图神经网络', '布线拥塞'], {}, 500)
        attributed = {hit['id']: hit['matched_phrases'] for hit in hits}
        self.assertEqual(attributed, {'chinese': ['图神经网络', '布线拥塞'], 'mixed': ['图神经网络']})
        self.assertEqual(self.ids(['图神经网络 graph']), {'mixed'})
        self.assertEqual(self.ids(['芯片布局']), {'chinese'})

    def test_scope_uses_single_alternate_edition(self):
        self.add('shared', 'Graph placement', editions=[('icml', 2020), ('neurips', 2019)])
        self.add('other', 'Graph placement', editions=[('icml', 2019)])
        self.assertEqual(self.ids(['graph'], {'venues': ['neurips'], 'year_from': 2019, 'year_to': 2019}), {'shared'})
        self.assertEqual(self.ids(['graph'], {'venues': ['icml'], 'year_from': 2019, 'year_to': 2019}), {'other'})
        self.assertEqual(self.ids(['graph'], {'venues': ['neurips'], 'year_from': 2020}), set())
        self.assertEqual(self.ids(['graph'], {'year_to': 2019}), {'shared', 'other'})

    def test_cjk_supplement_respects_alternate_edition_scope(self):
        self.add('shared', '利用图神经网络', editions=[('icml', 2020), ('neurips', 2019)])
        self.assertEqual(self.ids(['图神经网络'], {'venues': ['neurips'], 'year_to': 2019}), {'shared'})
        self.assertEqual(self.ids(['图神经网络'], {'venues': ['icml'], 'year_to': 2019}), set())

    def test_phrase_attribution_is_complete_after_route_candidate_caps(self):
        self.add('a', '利用图神经网络优化布局')
        self.add('z', 'Graph', '利用图神经网络优化布局')
        hits = query(self.home, ['graph', '图神经网络'], {}, 1)
        self.assertEqual(hits[0]['id'], 'z')
        self.assertEqual(hits[0]['matched_phrases'], ['graph', '图神经网络'])

    def test_scope_is_applied_before_candidate_limit(self):
        for number in range(10):
            self.add(str(number), 'Graph graph graph', editions=[('icml', 2020)])
        self.add('scoped', 'Graph', editions=[('neurips', 2019)])
        self.assertEqual(self.ids(['graph'], {'venues': ['neurips']}, limit=1), {'scoped'})

    def test_freshness_updates_deletions_and_empty_valid_source(self):
        self.add('first', 'Graph placement')
        first = ensure_index(self.home)
        keyword_mtime = (self.home / 'keyword.sqlite').stat().st_mtime_ns
        self.assertTrue(first['rebuilt'])
        second = ensure_index(self.home)
        self.assertFalse(second['rebuilt'])
        self.assertEqual(second['built_at'], first['built_at'])
        self.assertEqual(keyword_mtime, (self.home / 'keyword.sqlite').stat().st_mtime_ns)
        with self.connection() as connection:
            connection.execute('UPDATE papers SET title=? WHERE id=?', ('Protein folding', 'first'))
        self.assertEqual(self.ids(['graph']), set())
        self.assertEqual(self.ids(['protein folding']), {'first'})
        with self.connection() as connection:
            connection.execute('DELETE FROM papers')
            connection.execute('DELETE FROM editions')
        self.assertEqual(self.ids(['protein']), set())
        self.assertEqual(ensure_index(self.home)['papers'], 0)

    def test_freshness_detects_uncheckpointed_wal_change(self):
        self.add('first', 'Graph')
        connection = sqlite3.connect(self.source)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA wal_autocheckpoint=0')
            before = hashlib.sha256(self.source.read_bytes()).hexdigest()
            self.assertEqual(self.ids(['graph']), {'first'})
            signature = ensure_index(self.home)['source_signature']
            connection.execute('UPDATE papers SET title=?', ('Protein',))
            connection.commit()
            self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
            self.assertEqual(self.ids(['graph']), set())
            self.assertEqual(self.ids(['protein']), {'first'})
            self.assertNotEqual(ensure_index(self.home)['source_signature'], signature)
        finally:
            connection.close()

    def test_source_lookup_and_catalog_are_unchanged_and_no_runtime_needed(self):
        self.add('paper', 'Graph neural network')
        catalog = self.home.parent / 'catalog.sqlite'
        catalog.write_bytes(b'not opened by keyword retrieval')
        snapshots = [(path, path.read_bytes(), path.stat().st_mtime_ns) for path in (self.source, catalog)]
        with patch('subprocess.Popen', side_effect=AssertionError('No external runtime allowed')):
            self.assertEqual(self.ids(['graph neural network']), {'paper'})
        for path, content, mtime in snapshots:
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mtime_ns, mtime)
        self.assertEqual(list(self.home.glob('.keyword-*.sqlite')), [])

    def test_absent_lookup_never_creates_empty_source(self):
        missing_home = self.home.parent / 'missing' / 'search'
        with self.assertRaisesRegex(KeywordSearchError, 'search index'):
            ensure_index(missing_home)
        self.assertFalse(missing_home.exists())

    def test_failed_rebuild_preserves_previous_atomic_index(self):
        self.add('first', 'Graph')
        ensure_index(self.home)
        old = (self.home / 'keyword.sqlite').read_bytes()
        with self.connection() as connection:
            connection.execute('DROP TABLE editions')
        with self.assertRaisesRegex(KeywordSearchError, 'search lookup'):
            ensure_index(self.home)
        self.assertEqual((self.home / 'keyword.sqlite').read_bytes(), old)
        self.assertEqual(list(self.home.glob('.keyword-*.sqlite')), [])

    def test_missing_fts5_has_actionable_error(self):
        self.add('first', 'Graph')
        original_connect = sqlite3.connect

        class NoFTSConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql.startswith('CREATE VIRTUAL TABLE'):
                    raise sqlite3.OperationalError('no such module: fts5')
                return super().execute(sql, *args, **kwargs)

        def connect(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=NoFTSConnection)

        with patch('litdb.keyword_search.sqlite3.connect', side_effect=connect):
            with self.assertRaisesRegex(KeywordSearchError, 'Python build with SQLite FTS5'):
                ensure_index(self.home)
        self.assertFalse((self.home / 'keyword.sqlite').exists())

    def test_busy_writer_lock_returns_explicit_retry_error(self):
        self.add('first', 'Graph')
        with (self.home / '.keyword.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(KeywordSearchError, 'retry'):
                ensure_index(self.home)
        self.assertEqual(self.ids(['graph']), {'first'})

    def test_lookup_change_during_copy_preserves_previous_index(self):
        self.add('first', 'Graph')
        ensure_index(self.home)
        old = (self.home / 'keyword.sqlite').read_bytes()
        with self.connection() as connection:
            connection.execute('UPDATE papers SET title=?', ('Protein',))
        original_signature = keyword_search._source_signature
        calls = 0

        def signature(source):
            nonlocal calls
            calls += 1
            if calls == 3:
                with self.connection() as connection:
                    connection.execute('UPDATE papers SET title=?', ('Changed during copy',))
            return original_signature(source)

        with patch('litdb.keyword_search._source_signature', side_effect=signature):
            with self.assertRaisesRegex(KeywordSearchError, 'changed while building'):
                ensure_index(self.home)
        self.assertEqual((self.home / 'keyword.sqlite').read_bytes(), old)
        self.assertEqual(self.ids(['changed during copy']), {'first'})

    def test_candidate_bound_and_input_validation(self):
        for number in range(505):
            self.add(str(number), 'Graph')
        self.assertEqual(len(query(self.home, ['graph'], {}, 500)), 500)
        invalid = [([], {}, 10), ('graph', {}, 10), ([''], {}, 10), (['x' * 501], {}, 10),
                   (['x'] * 33, {}, 10), (['x\x00'], {}, 10), (['x'], {}, 501),
                   (['x'], {}, True), (['x'], {'venues': 'icml'}, 10),
                   (['x'], {'year_from': True}, 10), (['x'], {'year_to': 2201}, 10),
                   (['x'], {'year_from': 2021, 'year_to': 2020}, 10)]
        for phrases, options, limit in invalid:
            with self.subTest(phrases=phrases, options=options, limit=limit):
                with self.assertRaises(ValueError):
                    query(self.home, phrases, options, limit)


if __name__ == '__main__':
    unittest.main()
