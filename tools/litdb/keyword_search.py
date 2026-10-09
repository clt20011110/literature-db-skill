"""Independent local title/abstract retrieval, with no embedding runtime.

``home`` is the derived search directory containing ``papers.sqlite``. The
source lookup is opened read-only; only keyword.sqlite and its lock are written.
English and other Unicode phrases use FTS5 unicode61 token boundaries, with
diacritics ignored. Multiword phrases require consecutive tokens in one field.
CJK-containing phrases additionally use normalized literal substring matching:
unicode61 otherwise treats an unspaced Chinese sentence as a single token.
Substring-only matches have score 0; other scores are negated SQLite BM25
(higher ranks first). These are ranking values, never relevance probabilities.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_CANDIDATES = 500
MAX_PHRASES = 32
MAX_PHRASE_LENGTH = 500
MAX_QUERY_LENGTH = 6000
_CJK = re.compile('[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af\u3040-\u30ff\uac00-\ud7af]')


class KeywordSearchError(RuntimeError):
    pass


def _normalize(text: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def _source_signature(source: Path) -> str:
    """Fingerprint replacement, edits and uncheckpointed WAL transactions."""
    files = []
    for path in (source, Path(str(source) + '-wal')):
        try:
            stat = path.stat()
        except FileNotFoundError:
            if path == source:
                raise KeywordSearchError('Search lookup is missing; run litdb search index first.') from None
            continue
        if path != source and not stat.st_size:
            continue
        files.append((path.name, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    return hashlib.sha256(json.dumps(files).encode()).hexdigest()


@contextlib.contextmanager
def _lock(home: Path, *, exclusive: bool):
    home.mkdir(parents=True, exist_ok=True)
    with (home / '.keyword.lock').open('a') as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise KeywordSearchError('Keyword index is in use or rebuilding; retry shortly.') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    connection.execute('PRAGMA query_only=ON')
    return connection


def _metadata(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with contextlib.closing(_read_only(path)) as connection:
            row = connection.execute('SELECT value FROM metadata WHERE key=?', ('index',)).fetchone()
            value = json.loads(row[0]) if row else {}
            return value if isinstance(value, dict) else {}
    except (sqlite3.Error, ValueError, TypeError):
        return {}


def _build(source: Path, destination: Path, signature: str) -> dict:
    handle, temporary = tempfile.mkstemp(prefix='.keyword-', suffix='.sqlite', dir=destination.parent)
    os.close(handle)
    temporary = Path(temporary)
    try:
        with contextlib.closing(_read_only(source)) as lookup, contextlib.closing(sqlite3.connect(temporary)) as index:
            lookup.execute('BEGIN')
            # Validate both required tables before creating a replacement index.
            count = lookup.execute('SELECT count(*) FROM papers').fetchone()[0]
            lookup.execute('SELECT paper_id,venue,year FROM editions LIMIT 0')
            index.executescript('''
                CREATE TABLE documents (
                    id TEXT NOT NULL UNIQUE, title TEXT NOT NULL, abstract TEXT NOT NULL,
                    title_normalized TEXT NOT NULL, abstract_normalized TEXT NOT NULL);
                CREATE TABLE editions (paper_id TEXT NOT NULL, venue TEXT NOT NULL, year INTEGER NOT NULL,
                    PRIMARY KEY(paper_id,venue,year));
                CREATE INDEX editions_scope ON editions(paper_id,venue,year);
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            try:
                index.execute("CREATE VIRTUAL TABLE paper_fts USING fts5(title,abstract,content='documents',content_rowid='rowid',tokenize='unicode61 remove_diacritics 2')")
            except sqlite3.OperationalError as exc:
                if 'no such module' in str(exc).lower():
                    raise KeywordSearchError('SQLite FTS5 is unavailable; use a Python build with SQLite FTS5 enabled.') from exc
                raise
            rows = lookup.execute('SELECT id,title,abstract FROM papers ORDER BY id')
            while batch := rows.fetchmany(1000):
                index.executemany('INSERT INTO documents VALUES(?,?,?,?,?)',
                    [(identity, title or '', abstract or '', _normalize(title or ''), _normalize(abstract or ''))
                     for identity, title, abstract in batch])
            rows = lookup.execute('SELECT paper_id,venue,year FROM editions')
            while batch := rows.fetchmany(1000):
                index.executemany('INSERT INTO editions VALUES(?,?,?)', batch)
            index.execute("INSERT INTO paper_fts(paper_fts) VALUES('rebuild')")
            metadata = dict(schema_version=SCHEMA_VERSION, source_signature=signature, papers=count,
                built_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                engine='sqlite_fts5', scope='titles_and_abstracts', cjk_matching='normalized_literal_substring')
            index.execute('INSERT INTO metadata VALUES(?,?)', ('index', json.dumps(metadata)))
            index.commit()
        # Never publish a mixed/stale snapshot if the lookup changed during copy.
        if _source_signature(source) != signature:
            raise KeywordSearchError('Search lookup changed while building the keyword index; retry.')
        temporary.replace(destination)
        return metadata
    except sqlite3.Error as exc:
        raise KeywordSearchError(f'Could not build keyword index from search lookup: {exc}. Run litdb search index to refresh it.') from exc
    finally:
        temporary.unlink(missing_ok=True)
        Path(str(temporary) + '-journal').unlink(missing_ok=True)


def ensure_index(home: Path) -> dict:
    """Atomically build or refresh the independent keyword index as needed."""
    home = Path(home)
    source, destination = home / 'papers.sqlite', home / 'keyword.sqlite'
    # Check before mkdir/locking so an absent lookup never creates an empty DB.
    _source_signature(source)
    with _lock(home, exclusive=True):
        signature = _source_signature(source)
        metadata = _metadata(destination)
        if metadata.get('schema_version') == SCHEMA_VERSION and metadata.get('source_signature') == signature:
            return {**metadata, 'rebuilt': False}
        return {**_build(source, destination, signature), 'rebuilt': True}


def _validate(phrases: list[str], options: dict, limit: int) -> tuple[list[str], dict]:
    if not isinstance(phrases, list) or not 1 <= len(phrases) <= MAX_PHRASES:
        raise ValueError(f'Keyword phrases must be a list of 1–{MAX_PHRASES} literal phrases.')
    if any(not isinstance(p, str) or not p.strip() or len(p) > MAX_PHRASE_LENGTH or '\x00' in p for p in phrases):
        raise ValueError(f'Each keyword phrase must contain 1–{MAX_PHRASE_LENGTH} characters without NUL.')
    phrases = list(dict.fromkeys(' '.join(p.split()) for p in phrases))
    if sum(map(len, phrases)) > MAX_QUERY_LENGTH:
        raise ValueError(f'Keyword phrases must total at most {MAX_QUERY_LENGTH} characters.')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError(f'Keyword candidate limit must be 1–{MAX_CANDIDATES}.')
    if not isinstance(options, dict):
        raise ValueError('Keyword options must be an object.')
    venues = options.get('venues', [])
    if not isinstance(venues, list) or len(venues) > 128 or any(not isinstance(v, str) or not re.fullmatch('[a-z0-9-]+', v) or len(v) > 100 for v in venues):
        raise ValueError('Invalid keyword venue filters.')
    years = [options.get(field) for field in ('year_from', 'year_to')]
    if any(y is not None and (isinstance(y, bool) or not isinstance(y, int) or not 1900 <= y <= 2200) for y in years):
        raise ValueError('Keyword years must be integers from 1900 to 2200.')
    if all(y is not None for y in years) and years[0] > years[1]:
        raise ValueError('Keyword starting year must not exceed ending year.')
    return phrases, dict(venues=list(dict.fromkeys(venues)), year_from=years[0], year_to=years[1])


def _scope(options: dict) -> tuple[str, list]:
    clauses, args = [], []
    if options['venues']:
        clauses.append('e.venue IN (' + ','.join('?' for _ in options['venues']) + ')')
        args.extend(options['venues'])
    if options['year_from'] is not None:
        clauses.append('e.year>=?')
        args.append(options['year_from'])
    if options['year_to'] is not None:
        clauses.append('e.year<=?')
        args.append(options['year_to'])
    # Venue and both year constraints must hold for the SAME alternate edition.
    return 'EXISTS (SELECT 1 FROM editions e WHERE e.paper_id=d.id AND ' + (' AND '.join(clauses) or '1') + ')', args


def _fts_phrase(phrase: str) -> str:
    # Bound parameters protect SQL; quotes protect FTS operators/column syntax.
    return '"' + phrase.replace('"', '""') + '"'


def query(home: Path, phrases: list[str], options: dict, limit: int = 500) -> list[dict]:
    """OR-recall literal phrases, scoped before ranking, at most 500 candidates."""
    phrases, options = _validate(phrases, options, limit)
    home = Path(home)
    ensure_index(home)
    scope, args = _scope(options)
    expressions = [_fts_phrase(p) for p in phrases]
    with _lock(home, exclusive=False), contextlib.closing(_read_only(home / 'keyword.sqlite')) as connection:
        rows = connection.execute(f'''SELECT d.rowid,d.id,bm25(paper_fts,2.0,1.0) FROM paper_fts
            JOIN documents d ON d.rowid=paper_fts.rowid WHERE paper_fts MATCH ? AND {scope}
            ORDER BY bm25(paper_fts,2.0,1.0),d.id LIMIT ?''', [' OR '.join(expressions), *args, limit]).fetchall()
        hits = {rowid: dict(id=identity, score=-float(score), matched_phrases=[]) for rowid, identity, score in rows}
        for phrase in phrases:
            if not _CJK.search(phrase):
                continue
            normalized = _normalize(phrase)
            rows = connection.execute(f'''SELECT d.rowid,d.id FROM documents d WHERE {scope}
                AND (instr(d.title_normalized,?)>0 OR instr(d.abstract_normalized,?)>0)
                ORDER BY d.id LIMIT ?''', [*args, normalized, normalized, limit]).fetchall()
            for rowid, identity in rows:
                hit = hits.setdefault(rowid, dict(id=identity, score=0.0, matched_phrases=[]))
                hit['matched_phrases'].append(phrase)
        ranked = sorted(hits.items(), key=lambda item: (-item[1]['score'], item[1]['id']))[:limit]
        if not ranked:
            return []
        rowids = [rowid for rowid, _ in ranked]
        placeholders = ','.join('?' for _ in rowids)
        for phrase, expression in zip(phrases, expressions):
            matched = {row[0] for row in connection.execute(
                f'SELECT rowid FROM paper_fts WHERE paper_fts MATCH ? AND rowid IN ({placeholders})',
                [expression, *rowids])}
            if _CJK.search(phrase):
                # A BM25 candidate may fall outside the substring route's cap;
                # attribute every matching phrase on the final candidates.
                normalized = _normalize(phrase)
                matched.update(row[0] for row in connection.execute(
                    f'''SELECT rowid FROM documents WHERE rowid IN ({placeholders})
                        AND (instr(title_normalized,?)>0 OR instr(abstract_normalized,?)>0)''',
                    [*rowids, normalized, normalized]))
            for rowid, hit in ranked:
                if rowid in matched and phrase not in hit['matched_phrases']:
                    hit['matched_phrases'].append(phrase)
        # Preserve caller phrase order even when one also matched the CJK path.
        for _, hit in ranked:
            hit['matched_phrases'] = [p for p in phrases if p in hit['matched_phrases']]
        return [hit for _, hit in ranked]
