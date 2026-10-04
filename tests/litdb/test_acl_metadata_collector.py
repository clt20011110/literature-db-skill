from __future__ import annotations

import unittest

from tools.collect_acl_metadata import compare_source_ids, parse_collection, parse_page, source_drift_status


class ACLMetadataCollectorTests(unittest.TestCase):
    def test_full_source_identity_comparison_keeps_front_matter_and_flags_true_removal(self) -> None:
        prior = {"P24-research", "P24-front"}
        current = {"P24-research", "P24-front"}
        new_ids, missing_ids = compare_source_ids(prior, current)
        self.assertEqual((new_ids, missing_ids), ([], []))
        self.assertEqual(source_drift_status(new_ids, missing_ids), "NO_DRIFT")

        new_ids, missing_ids = compare_source_ids(prior, {"P24-research"})
        self.assertEqual((new_ids, missing_ids), ([], ["P24-front"]))
        self.assertEqual(source_drift_status(new_ids, missing_ids), "UNRESOLVED_DRIFT")

    def test_source_drift_status_allows_additions_but_never_masks_removals(self) -> None:
        self.assertEqual(source_drift_status([], []), "NO_DRIFT")
        self.assertEqual(source_drift_status(["new-id"], []), "WITHIN_THRESHOLD")
        self.assertEqual(source_drift_status([], ["missing-id"]), "UNRESOLVED_DRIFT")

    def test_xml_pointer_is_a_closed_xpath_predicate(self) -> None:
        xml = b"""<?xml version='1.0'?>
        <collection id='P15'>
          <volume id='1'>
            <!-- https://aclanthology.org/P15-1/ -->
            <meta><booktitle>ACL Long Papers</booktitle><month>July</month><year>2015</year><venue>acl</venue></meta>
            <frontmatter><!-- https://aclanthology.org/P15-1000/ --></frontmatter>
            <paper id='1'>
              <!-- https://aclanthology.org/P15-1001/ -->
              <title>Example paper</title><author><first>Jane</first><last>Doe</last></author>
              <pages>1-4</pages><doi>10.3115/v1/P15-1001</doi><abstract>Example abstract.</abstract>
            </paper>
          </volume>
        </collection>"""

        volumes, items, summary = parse_collection(
            xml,
            2015,
            "https://raw.githubusercontent.com/acl-org/acl-anthology/0e1bf6e/data/xml/P15.xml",
            "2026-10-04T00:00:00Z",
        )

        self.assertEqual(summary["collection_id"], "P15")
        self.assertEqual(volumes["P15-1"]["frontmatter_id"], "P15-1000")
        self.assertEqual(items[0]["source_native_id"], "P15-1001")
        self.assertEqual(
            items[0]["xml_pointer"],
            "/collection[@id='P15']/volume[@id='1']/paper[@id='1']",
        )

    def test_page_links_are_taken_from_visible_official_anchors(self) -> None:
        parser = parse_page(
            b"""<a href='/P15-1001/'>paper</a>
            <a class='pdf' href='/P15-1001.pdf'>pdf</a>""",
            "https://aclanthology.org/volumes/P15-1/",
            {"P15-1001"},
        )

        self.assertEqual(parser.landings["P15-1001"], "https://aclanthology.org/P15-1001/")
        self.assertEqual(parser.pdfs["P15-1001"], "https://aclanthology.org/P15-1001.pdf")


if __name__ == "__main__":
    unittest.main()
