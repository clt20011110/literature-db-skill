"""Regression checks for the observed slides-as-papers and lost-ID failures."""
import unittest
from iclr_virtual_detail_runner import canonical_url, discover_paper_pdf

BASE = 'https://iclr.cc/virtual/2026/poster/10010257'

class PaperPdfDiscoveryTests(unittest.TestCase):
    def test_preserves_openreview_paper_identity_without_tracking(self):
        self.assertEqual(canonical_url('https://openreview.net/attachment?id=Abc123&name=pdf&utm_source=x#p'),
                         'https://openreview.net/attachment?id=Abc123&name=pdf')

    def test_slides_are_not_a_paper_even_in_citation_metadata(self):
        self.assertEqual(discover_paper_pdf('<meta name="citation_pdf_url" content="/media/iclr-2026/Slides/10.pdf"><a href="/media/iclr-2026/Slides/10.pdf">Slides</a>', BASE), '')

    def test_later_paper_link_wins_over_slides(self):
        page = '<a href="/media/iclr-2026/Slides/10.pdf">Slides</a><a href="https://openreview.net/pdf?id=Abc123">Paper</a>'
        self.assertEqual(discover_paper_pdf(page, BASE), 'https://openreview.net/pdf?id=Abc123')

    def test_missing_identity_and_supplement_are_rejected(self):
        page = '<a href="https://openreview.net/pdf">Paper</a><a href="https://openreview.net/attachment?id=Abc123&amp;name=supplementary_material">Supplementary</a>'
        self.assertEqual(discover_paper_pdf(page, BASE), '')

    def test_explicit_attachment_is_retained(self):
        self.assertEqual(discover_paper_pdf('<a href="https://openreview.net/attachment?id=Abc123&amp;name=pdf">Download PDF</a>', BASE), 'https://openreview.net/attachment?id=Abc123&name=pdf')

    def test_other_pdf_requires_paper_label(self):
        self.assertEqual(discover_paper_pdf('<a href="/media/schedule.pdf">Schedule</a>', BASE), '')
        self.assertEqual(discover_paper_pdf('<a href="/media/papers/paper.pdf">Paper</a>', BASE), 'https://iclr.cc/media/papers/paper.pdf')

if __name__ == '__main__':
    unittest.main()
