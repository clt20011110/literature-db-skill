# Integration, the VLSI Journal playbook

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

Last attempted: 2026-08-25. Venue ID: `integration-vlsi-journal`; Elsevier journal initialization target is 2015 onward.

## Official source and access boundary

- Official journal page: `https://www.sciencedirect.com/journal/integration`.
- Allowed formal domain: `sciencedirect.com`.
- The accepted 2026-08-25 attempt reached a ScienceDirect human-confirmation CAPTCHA before journal content loaded. No bypass, alternate source, raw challenge content, cookies, tokens, or network identifiers were used or saved.

A CAPTCHA is a hard stop. Ask the user to complete it in the already-open in-app Browser tab. Do not refresh, navigate away, or create replacement tabs until the user explicitly confirms completion. Then reuse that same tab, wait 15–30 seconds, verify the visible journal title and official origin, and discover archive/current-issue navigation from visible links. Do not guess an issues URL.

## Initialize and update

After access is restored, enumerate the visible issue/archive surface from 2015 through the current waterline. Save volume/issue/year, visible page/range totals, termination evidence, official article identity, title, authors, type labels, and source timestamps. Include official research articles; preserve editorials, news, corrections, retractions, front matter, and indexes as explicit exclusions.

Open canonical ScienceDirect article pages as needed to validate ordered authors/affiliations, abstract, publication date, document type, DOI/native identity, landing URL, and visible PDF action. A visible listing/count is expected-manifest evidence only until every included row satisfies the formal metadata schema and provenance gates.

For updates, reuse accepted issue identities, receipts, listing metadata, detail samples, and the last watermark; recheck the current/latest visible issue and reopen only new, changed, missing, stale, conflicting, or drift-sensitive records. Never treat an open issue or access-blocked page as a closed-year zero.

Formal blocked-run root: `legacy-evidence://database/runs/bootstrap-20260820T081741.316646Z/venues/integration-vlsi-journal/7eef23ca-d76f-474e-80e7-30db03a3fccb`. Reuse `controller_source_blocked_evidence.json`, `browser_evidence.jsonl`, and `errors.jsonl` when resuming; they prove the CAPTCHA boundary only and contain no catalog metadata.

## Download

Use a saved official PDF URL when it remains valid. Otherwise open the canonical ScienceDirect article page in the user-authorized in-app Browser and click the visible PDF control. If access or another CAPTCHA appears, stop for the user; do not bypass it. Validate the resulting PDF header, parseability, page count, size, identity/title, and SHA-256, and do not persist cookies, session tokens, or transient signed parameters.
