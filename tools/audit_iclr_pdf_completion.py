"""Audit an ICLR PDF collection against its original row scope, without network IO."""
import argparse
import collections
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def rows(path):
    return [json.loads(line) for line in Path(path).open() if line.strip()]


def pdf_problem(row):
    url = row.get('pdf_url')
    if not url:
        return 'missing_pdf'
    parsed = urlparse(url)
    if parsed.scheme != 'https':
        return 'non_https_pdf'
    if re.search(r'/(slides?|posters?|supplement(?:ary|al)?)/', parsed.path, re.I):
        return 'presentation_or_supplement_not_paper'
    if parsed.hostname == 'openreview.net':
        if re.fullmatch(r'/pdf/[a-f0-9]{40}\.pdf', parsed.path):
            return None
        params = parse_qs(parsed.query)
        if len(params.get('id', [])) != 1 or not params['id'][0].strip():
            return 'openreview_missing_or_ambiguous_id'
        if parsed.path == '/pdf':
            return None
        if parsed.path == '/attachment' and params.get('name') == ['pdf']:
            return None
        return 'not_an_openreview_paper_pdf_endpoint'
    if parsed.hostname == 'arxiv.org':
        if row.get('year') not in [2015, 2016]:
            return 'arxiv_outside_user_approved_year_scope'
        return None if re.fullmatch(r'/pdf/\d{4}\.\d{4,5}(v\d+)?(\.pdf)?', parsed.path) else 'invalid_arxiv_pdf'
    if parsed.hostname == 'iclr.cc' and parsed.path.lower().endswith('.pdf'):
        return None
    if parsed.hostname in {'jmlr.org', 'www.jmlr.org'}:
        # ICLR also presents journal papers and links their official JMLR pages.
        # This checks URL structure only; the per-paper source chain is audited separately.
        return None if re.fullmatch(r'/papers/(?:volume|v)\d+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?\.pdf', parsed.path) else 'not_a_jmlr_paper_pdf_endpoint'
    return 'non_official_or_non_pdf_location'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--metadata-decisions', help='Explicitly reviewed old/new field corrections, with source evidence.')
    parser.add_argument('--scope-decisions', help='Reviewed exclusions based on official document-type evidence.')
    parser.add_argument('--restoration-decisions', help='Reviewed research papers to restore from prior exclusions.')
    parser.add_argument('--duplicate-decisions', help='Reviewed same-paper presentations preserved in multiple years.')
    parser.add_argument('--baseline-exclusions')
    parser.add_argument('--candidate-exclusions')
    args = parser.parse_args()
    before, after = rows(args.baseline), rows(args.candidate)
    old = {r['source_native_id']: r for r in before}
    corrections = {}
    if args.metadata_decisions:
        for decision in json.loads(Path(args.metadata_decisions).read_text())['decisions']:
            evidence = Path(decision['evidence_source'])
            if hashlib.sha256(evidence.read_bytes()).hexdigest() != decision['evidence_sha256']:
                raise ValueError('Reviewed metadata correction evidence changed')
            corrections[decision['source_native_id']] = decision['field_changes']
    ids = collections.Counter(r.get('source_native_id') for r in after)
    issues, changes, missing, by_year = [], [], [], {}
    reviewed_exclusions = {}
    restorations = {}
    scope_accounting = None
    if args.restoration_decisions:
        if not args.scope_decisions:
            raise ValueError('Restorations require complete scope accounting')
        for decision in json.loads(Path(args.restoration_decisions).read_text())['decisions']:
            evidence = Path(decision['evidence_source'])
            if hashlib.sha256(evidence.read_bytes()).hexdigest() != decision['evidence_sha256']:
                raise ValueError('Restored paper evidence changed')
            restorations[decision['source_native_id']] = decision['restored_fields']
    if args.scope_decisions:
        if not args.baseline_exclusions or not args.candidate_exclusions:
            raise ValueError('Scope correction requires both baseline and candidate exclusion files')
        scope = json.loads(Path(args.scope_decisions).read_text())
        if hashlib.sha256(Path(scope['policy_path']).read_bytes()).hexdigest() != scope['policy_sha256']:
            raise ValueError('Original scope policy changed')
        evidence_cache = {}
        for decision in scope['decisions']:
            evidence_path = decision['evidence_source']
            if evidence_path not in evidence_cache:
                raw = Path(evidence_path).read_bytes()
                evidence_cache[evidence_path] = (hashlib.sha256(raw).hexdigest(), json.loads(raw)['results'])
            evidence_hash, records = evidence_cache[evidence_path]
            if evidence_hash != decision['evidence_sha256']:
                raise ValueError('Scope evidence changed')
            matching = [r for r in records if str(r['id']) == decision['native_id']]
            if len(matching) != 1 or matching[0].get('event_type') != 'Blog Track Poster':
                raise ValueError('Excluded identity is not explicitly a Blog Track Poster')
            reviewed_exclusions[decision['source_native_id']] = decision
        prior_exclusions = {r['source_native_id']: r for r in rows(args.baseline_exclusions)}
        candidate_exclusion_rows = rows(args.candidate_exclusions)
        excluded_ids = collections.Counter(r['source_native_id'] for r in candidate_exclusion_rows)
        if not set(restorations) <= set(prior_exclusions):
            raise ValueError('Restoration must refer to an original excluded source identity')
        expected_excluded = (set(prior_exclusions) - set(restorations)) | set(reviewed_exclusions)
        if set(excluded_ids) != expected_excluded or any(n != 1 for n in excluded_ids.values()):
            issues.append({'code': 'exclusion_identity_set_mismatch'})
        if set(ids) & set(excluded_ids):
            issues.append({'code': 'identity_both_included_and_excluded'})
        if set(ids) | set(excluded_ids) != set(old) | set(prior_exclusions):
            issues.append({'code': 'source_identity_accounting_changed'})
        scope_accounting = {'baseline_included': len(before), 'baseline_excluded': len(prior_exclusions),
                            'candidate_included': len(after), 'candidate_excluded': len(candidate_exclusion_rows),
                            'reviewed_blog_exclusions': len(reviewed_exclusions),
                            'reviewed_research_restorations': len(restorations),
                            'source_identities_accounted': len(set(ids) | set(excluded_ids))}
    for ident in old.keys() - ids.keys():
        if ident not in reviewed_exclusions:
            issues.append({'source_native_id': ident, 'code': 'scope_row_missing'})
    for ident in ids.keys() - old.keys():
        if ident not in restorations:
            issues.append({'source_native_id': ident, 'code': 'unreviewed_scope_addition'})
    for ident, count in ids.items():
        if count != 1:
            issues.append({'source_native_id': ident, 'code': 'duplicate_identity', 'count': count})
    pdf_to_ids = collections.defaultdict(list)
    stable = ['year', 'title', 'authors', 'abstract', 'doi', 'inclusion_decision']
    for row in after:
        ident = row['source_native_id']
        stats = by_year.setdefault(str(row['year']), {'rows': 0, 'paper_pdf_present': 0, 'missing': 0, 'invalid': 0})
        stats['rows'] += 1
        problem = pdf_problem(row)
        if problem == 'missing_pdf':
            stats['missing'] += 1
            missing.append({'source_native_id': ident, 'title': row['title'], 'reason': row.get('missing_fields', {}).get('pdf_url')})
        elif problem:
            stats['invalid'] += 1
            issues.append({'source_native_id': ident, 'code': problem, 'url': row.get('pdf_url')})
        else:
            stats['paper_pdf_present'] += 1
            pdf_to_ids[row['pdf_url']].append(ident)
        if ident in restorations:
            for field, expected in restorations[ident].items():
                if row.get(field) != expected:
                    issues.append({'source_native_id': ident, 'code': 'restored_field_mismatch', 'field': field})
            for field in ['title', 'authors', 'pdf_url']:
                provenance = row.get('field_provenance', {}).get(field, {})
                if not all(provenance.get(k) for k in ['method', 'observed_at', 'source_url']):
                    issues.append({'source_native_id': ident, 'code': 'restored_field_provenance_incomplete', 'field': field})
            changes.append({'source_native_id': ident, 'action': 'restored_research_paper',
                            'old_pdf': None, 'new_pdf': row.get('pdf_url'),
                            'provenance': row.get('field_provenance', {}).get('pdf_url', {})})
        if ident in old:
            for field in stable:
                if row.get(field) != old[ident].get(field):
                    approved = corrections.get(ident, {}).get(field)
                    if not approved or approved.get('old') != old[ident].get(field) or approved.get('new') != row.get(field):
                        issues.append({'source_native_id': ident, 'code': 'non_pdf_metadata_changed', 'field': field})
            if row.get('pdf_url') != old[ident].get('pdf_url'):
                provenance = row.get('field_provenance', {}).get('pdf_url', {})
                changes.append({'source_native_id': ident, 'old_pdf': old[ident].get('pdf_url'), 'new_pdf': row.get('pdf_url'), 'provenance': provenance})
                if row.get('pdf_url') and not all(provenance.get(k) for k in ['method', 'observed_at', 'source_url']):
                    issues.append({'source_native_id': ident, 'code': 'new_pdf_provenance_incomplete'})
    duplicate_pdfs = {u: names for u, names in pdf_to_ids.items() if len(names) > 1}
    approved_duplicates = {'https://arxiv.org/pdf/1411.7676': {'iclr:2015:1411.7676', 'iclr:2016:1411.7676'}}
    if args.duplicate_decisions:
        for decision in json.loads(Path(args.duplicate_decisions).read_text())['decisions']:
            evidence = Path(decision['evidence_source'])
            if hashlib.sha256(evidence.read_bytes()).hexdigest() != decision['evidence_sha256']:
                raise ValueError('Reviewed duplicate-PDF evidence changed')
            approved_duplicates[decision['pdf_url']] = set(decision['source_native_ids'])
    after_by_id = {row['source_native_id']: row for row in after}
    approved_tmlr_duplicates = {}
    for url, names in duplicate_pdfs.items():
        provenance = [after_by_id[name].get('field_provenance', {}).get('pdf_url', {}) for name in names]
        if provenance and all(
            item.get('source_kind') == 'official_tmlr_accepted_papers_list'
            and item.get('tmlr_pdf_href') == url
            and item.get('tmlr_forum_href')
            and item.get('tmlr_source_sha256') == provenance[0].get('tmlr_source_sha256')
            and item.get('tmlr_forum_href') == provenance[0].get('tmlr_forum_href')
            and item.get('title') == after_by_id[names[0]].get('title')
            for item in provenance
        ):
            approved_tmlr_duplicates[url] = set(names)
    for url, names in duplicate_pdfs.items():
        if set(names) != approved_duplicates.get(url) and set(names) != approved_tmlr_duplicates.get(url):
            issues.append({'code': 'unreviewed_duplicate_pdf', 'url': url, 'source_native_ids': names})
    secret_keys = {'authorization', 'cookie', 'set-cookie', 'password', 'access_token', 'session_token', 'csrf_token'}
    def scan(value, ident):
        if isinstance(value, dict):
            for key, val in value.items():
                if key.lower() in secret_keys and val:
                    issues.append({'source_native_id': ident, 'code': 'sensitive_key_present', 'key': key})
                else:
                    scan(val, ident)
        elif isinstance(value, list):
            for val in value:
                scan(val, ident)
        elif isinstance(value, str) and value.startswith('https://'):
            keys = set(parse_qs(urlparse(value).query))
            if keys & secret_keys:
                issues.append({'source_native_id': ident, 'code': 'sensitive_query_key_present'})
    for row in after:
        scan(row, row['source_native_id'])
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    summary = {'observed_at': datetime.now(timezone.utc).isoformat(), 'baseline': args.baseline,
               'candidate': args.candidate, 'baseline_rows': len(before), 'candidate_rows': len(after),
               'validation_status': 'PASS' if not issues else 'FAIL',
               'completion_status': 'COMPLETE' if not issues and not missing else 'INCOMPLETE',
               'changed_pdf_rows': len(changes), 'missing_pdf_rows': len(missing),
               'issues': issues, 'by_year': by_year, 'duplicate_pdf_groups': duplicate_pdfs,
               'approved_tmlr_duplicate_pdf_groups': approved_tmlr_duplicates,
               'candidate_sha256': hashlib.sha256(Path(args.candidate).read_bytes()).hexdigest(),
               'metadata_decisions': args.metadata_decisions,
               'scope_accounting': scope_accounting,
               'restoration_decisions': args.restoration_decisions,
               'duplicate_decisions': args.duplicate_decisions,
               'limitations': 'Offline structural/provenance audit; direct source evidence must also be reviewed for completion.'}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    (output / 'changes.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in changes))
    (output / 'missing.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in missing))
    print(json.dumps({k: summary[k] for k in ['validation_status', 'completion_status', 'changed_pdf_rows', 'missing_pdf_rows', 'by_year']}, ensure_ascii=False))
    print('issues', len(issues))


if __name__ == '__main__':
    main()
