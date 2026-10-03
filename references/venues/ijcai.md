# IJCAI (International Joint Conference on Artificial Intelligence)

> Portable playbook. Dated status below describes the accepted source snapshot. Read the installed catalog and campaign state for current status. `<db-home>` is the configured database home; `legacy-evidence://` references identify archived evidence that is not distributed with this skill. Never treat an unavailable historical receipt as freshly verified evidence.

## Current catalog status (2026-10-01)

- The current read-only catalog contains **8,995 `canonical_work` rows** for `ijcai`. The accepted IJCAI merge committed **8,995 included rows and 476 explicit exclusions** on 2026-09-06; the merge receipt and the subsequent reconcile receipt both report `PASS`.
- The reconciled source universe is **9,471 resolved detail identities** from **9,472 listing observations** for 2015–2025. The post-merge coverage report has zero missing, extra, duplicate, or provenance-gap records. The included rows have title, authors, abstract, landing URL, and observed PDF URL; DOI and exact publication date are present for 7,699 rows and carry structured missing reasons for 1,296 rows.
- Campaign state is `ACTIVE` with the 2025 waterline. The registry deliberately remains schema-compliant `status: UNSEEN`: the promotion audit records that the registry validator requires `UNSEEN`, while catalog/campaign state records the successful promotion. Do not read the registry status as evidence that the catalog is empty.
- This status is based on the saved run and read-only catalog evidence; it does not assert a fresh network check beyond the recorded 2015–2025 waterline.

## Identity and scope

- Venue ID: `ijcai`; annual proceedings; crawl start 2015; expected access `public_html`.
- Allowed domain: `ijcai.org`. The registry's main-track rule is `official_main_research_track`.
- Include the official main research track. Apply the registry exclusion rules for editorial, news, correction, retraction, front matter, index, committee, keynote abstract, workshop, poster-only, demo-only, tutorial, and doctoral-consortium material. Preserve every exclusion with its reason.
- The current reconciled scope is the official IJCAI proceedings listings and detail pages for 2015–2025. No 2026 record is implied by the 2025 waterline.

## Official entry points and source roles

The accepted waterline evidence records these year-specific official proceedings entry points:

- [2015](https://www.ijcai.org/proceedings/2015/), [2016](https://www.ijcai.org/proceedings/2016/), [2017](https://www.ijcai.org/proceedings/2017/), [2018](https://www.ijcai.org/proceedings/2018/), [2019](https://www.ijcai.org/proceedings/2019/)
- [2020](https://www.ijcai.org/proceedings/2020/), [2021](https://www.ijcai.org/proceedings/2021/), [2022](https://www.ijcai.org/proceedings/2022/), [2023](https://www.ijcai.org/proceedings/2023/), [2024](https://www.ijcai.org/proceedings/2024/), [2025](https://www.ijcai.org/proceedings/2025/)

Use the visible annual listing for enumeration and its linked official detail page for content fields. The accepted source-resolution policy makes the official listing primary for identity, title, ordered authors, and track; the official detail page is primary for detail content, with detail-page track resolution for `ijcai:2021:722`. Preserve both raw sources when a field is resolved. Do not invent aliases or derive a detail URL that was not observed.

## Reconciliation and identity notes

- The listing/detail union is exact: 9,471 unique native IDs, with no missing or extra detail IDs. One duplicate listing observation, `ijcai:2021:722`, is retained as an explicit conflict exclusion; the canonical record is the Doctoral Consortium entry.
- The saved reconciliation reports all years 2015–2025 passing strict staging and security checks. The union security scan found zero findings across 10,895 files.
- The 2021 ID 722 track decision and other field resolutions follow `source_resolution_policy_20260906.json`. Raw listing and detail evidence remains preserved; the policy records no invented aliases.
- PDF fields represent observed official PDF hrefs and provenance. The run did not claim that PDF bytes were downloaded or that every URL was freshly HTTP-tested.

## Durable evidence and receipts

- Status report: `legacy-evidence://workspace/IJCAI_FULL_RANGE_STATUS_20260906.md`
- Repair root: `legacy-evidence://database/runs/ijcai-discovery-20260906/venues/ijcai/repair-20260906`
- Source-resolution policy: `legacy-evidence://database/runs/ijcai-discovery-20260906/venues/ijcai/repair-20260906/source_resolution_policy_20260906.json`
- Accepted staging: `legacy-evidence://database/runs/ijcai-discovery-20260906/venues/ijcai/repair-20260906/reconciled_full_range_20260906/metadata_staging.jsonl`
- Merge receipt: `legacy-evidence://database/runs/ijcai-discovery-20260906/venues/ijcai/repair-20260906/reconciled_full_range_20260906/merge_receipt.json`
- Reconcile receipt: `legacy-evidence://database/runs/ijcai-discovery-20260906/venues/ijcai/repair-20260906/reconciled_full_range_20260906/reconcile_receipt.json`
- Coverage report: `legacy-evidence://database/runs/ijcai-discovery-20260906/venues/ijcai/repair-20260906/reconciled_full_range_20260906/venue_coverage_report.json`
- Catalog snapshot: `legacy-evidence://database/runs/ijcai-discovery-20260906/venues/ijcai/repair-20260906/reconciled_full_range_20260906/catalog_snapshot.json`
- Promotion audit: `legacy-evidence://database/runs/ijcai-discovery-20260906/venues/ijcai/repair-20260906/reconciled_full_range_20260906/catalog_registry_state_promotion_audit.json`
- Waterline evidence: `legacy-evidence://database/runs/ijcai-discovery-20260906/venues/ijcai/repair-20260906/reconciled_full_range_20260906/waterline_evidence.json`

## Update and download

Before an update, read the registry, campaign state, latest receipts, expected manifests, current catalog, and waterline evidence. Reuse the accepted 2015–2025 identity set and re-enumerate only a changed or newly published official edition, then rerun identity, field, exclusion, provenance, and drift checks before promotion. Do not infer a future-year URL from the observed path pattern; record the visible official entry point first.

For downloads, retain the observed official landing URL and PDF href from a validated detail row. Validate downloaded bytes before recording a local artifact, and stop on access control or an unresolved source/scope conflict.
