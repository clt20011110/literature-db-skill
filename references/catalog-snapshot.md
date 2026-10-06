# Bundled catalog snapshot

The `v1.2.0` database snapshot was audited on 2026-10-06. The table reports canonical works, not source items or edition appearances. Abstract and DOI columns count populated metadata. PDF coverage counts works with a stored PDF location; it does not mean that a file was downloaded or that the link was tested for live accessibility. Both the bundle and a separate installation passed integrity checks and retained all 187,158 records, their academic fields, dates and public links.

| Venue | Papers | Abstract | DOI | PDF location |
|---|---:|---:|---:|---:|
| aaai | 16,810 | 16,809 | 16,810 | 16,810 |
| acl | 10,186 | 9,538 | 10,183 | 10,186 |
| asp-dac | 1,412 | 1,404 | 300 | 971 |
| bioinformatics | 10,127 | 10,125 | 10,127 | 10,127 |
| cvpr | 21,482 | 21,476 | 0 | 21,482 |
| dac | 2,617 | 2,617 | 1,841 | 2,617 |
| date | 4,119 | 4,119 | 3,715 | 4,119 |
| eccv | 9,416 | 9,416 | 9,379 | 9,416 |
| embedded-systems-letters | 775 | 774 | 775 | 775 |
| iccad | 1,915 | 1,915 | 1,472 | 1,915 |
| iccd | 1,093 | 1,093 | 1,093 | 1,093 |
| iccv | 8,691 | 8,690 | 0 | 8,691 |
| iclr | 16,926 | 16,882 | 9 | 16,926 |
| icml | 13,887 | 13,887 | 0 | 13,688 |
| ieee-design-test | 717 | 711 | 717 | 717 |
| ijcai | 8,995 | 8,995 | 7,699 | 8,995 |
| iscas | 9,747 | 9,747 | 9,747 | 9,747 |
| jetc | 442 | 442 | 442 | 442 |
| nature | 12,403 | 12,400 | 12,403 | 12,403 |
| nature-computational-science | 451 | 451 | 451 | 451 |
| nature-machine-intelligence | 845 | 844 | 845 | 845 |
| nature-methods | 1,892 | 1,892 | 1,892 | 1,892 |
| neurips | 23,529 | 23,529 | 15,209 | 23,528 |
| tcad | 3,875 | 3,875 | 3,875 | 3,875 |
| todaes | 1,034 | 1,034 | 1,034 | 1,034 |
| trets | 453 | 450 | 453 | 453 |
| tvlsi | 3,319 | 3,312 | 3,319 | 3,319 |

Total: **187,158 canonical works across 27 venues**, with 186,427 abstracts, 113,790 DOIs and 186,517 works with a PDF location. Bioinformatics contributes 10,127 canonical works from 10,448 enumerated source items: 10,127 included records and 321 exclusions. Its accepted-input reconciliation passed with zero missing, extra, duplicate or provenance-gap items.

The post-merge audit checked all 177,031 canonical works from the historical `v1.1.0` baseline. None were missing; changes to their academic core fields, dates, public links, and date/link fields were all zero. `v1.1.0` had 177,031 works across 26 venues and extended the `v1.0.0` baseline of 127,256 works across 22 venues with CVPR (21,482), ICCV (8,691), ECCV (9,416) and ACL (10,186). `v1.0.1` was an installer-only fix. The registry had 107 candidates in `v1.1.0`; the current source registry has 108 candidates, which is separate from collected coverage.

The Bioinformatics scope covers 2015 through content publicly available in 2026 at observation time. Archive directories were observed on 2026-10-05; article details were observed through 2026-10-06. This is not a claim that the 2026 calendar year is complete. See the [public Bioinformatics audit](../data/bioinformatics-v1.2.0-audit.json) for yearly source, inclusion and exclusion counts. The audit file contains summary metadata only; private raw pages, API headers and local evidence paths are not included.
