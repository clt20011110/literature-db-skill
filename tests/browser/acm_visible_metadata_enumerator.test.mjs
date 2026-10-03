import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  canonicalAcmDetailUrl,
  canonicalAcmTocUrl,
  classifyAcmInclusion,
  crawlAcmMetadata,
  deduplicateAcmListingRows,
  detectAcmDetailPageCondition,
  detectAcmPageCondition,
  inspectAcmReachedDetailUrl,
  parseAcmDetailSnapshot,
  parseAcmIssueItem,
  parseAcmTocUrl,
  validateAcmObservationBatchRows,
} from "../../tools/browser/acm_visible_metadata_enumerator.mjs";


test("ACM URL allowlists keep TOC and DOI detail targets official", () => {
  assert.equal(
    canonicalAcmTocUrl("https://dl.acm.org/toc/todaes/2026/31/6/"),
    "https://dl.acm.org/toc/todaes/2026/31/6",
  );
  assert.deepEqual(parseAcmTocUrl("https://dl.acm.org/toc/todaes/justaccepted"), {
    kind: "justaccepted",
    journalCode: "todaes",
    year: null,
    volume: null,
    issue: null,
    url: "https://dl.acm.org/toc/todaes/justaccepted",
  });
  assert.equal(canonicalAcmDetailUrl("10.1145/3774323"), "https://dl.acm.org/doi/10.1145/3774323");
  assert.deepEqual(
    inspectAcmReachedDetailUrl("https://dl.acm.org/doi/10.1145/3774323?__cf_chl_rt_tk=temporary-secret"),
    {
      doi: "10.1145/3774323",
      url: "https://dl.acm.org/doi/10.1145/3774323",
      transient_query_observed: true,
      transient_query_key_count: 1,
    },
  );
  assert.throws(() => inspectAcmReachedDetailUrl("https://dl.acm.org/doi/10.1145/3774323?download=true"));
  assert.throws(() => canonicalAcmTocUrl("https://example.org/toc/todaes/2026/31/6"));
  assert.throws(() => canonicalAcmDetailUrl("https://dl.acm.org/article/10.1145/3774323"));
});


test("listing parser retains teaser-only abstract and visible card fields", () => {
  const row = parseAcmIssueItem({
    text: "Research-Article Article No. 12 Pages 1-9 DOI 10.1145/1234567",
    doi: "10.1145/1234567",
    title: "Visible title",
    authors: ["Ada Lovelace", "Grace Hopper"],
    authorsTruncated: true,
    abstractTeaser: "Short visible teaser",
    articleType: "RESEARCH-ARTICLE",
    publicationDate: "November 2026",
    articleNumber: "12 DOI 10.1145/1234567",
    pages: "1-9 DOI 10.1145/1234567",
  }, {
    venueId: "test-venue",
    pageInfo: parseAcmTocUrl("https://dl.acm.org/toc/todaes/2026/31/6"),
    observedAt: "2026-08-25T00:00:00.000Z",
  });
  assert.equal(row.article_type, "Research Article");
  assert.equal(row.abstract, null);
  assert.equal(row.abstract_status, "partial_teaser");
  assert.equal(row.article_number, "12");
  assert.equal(row.pages, "1-9");
  assert.deepEqual(row.authors, [{name: "Ada Lovelace"}, {name: "Grace Hopper"}]);
  assert.equal(row.authors_status, "partial_visible_subset");
});


test("literal Loading marker remains loading even with section content", () => {
  assert.deepEqual(
    detectAcmPageCondition({
      body_text_length: 500,
      page_title: "Table of Contents",
      has_content_marker: true,
      section_title_count: 2,
      loading_visible_count: 0,
      loading_text_visible_count: 1,
    }),
    {hardStop: false, loading: true, reason: "LOADING_VISIBLE"},
  );
});


test("Offloading in an article title is not mistaken for a loading shell", () => {
  assert.deepEqual(
    detectAcmDetailPageCondition({
      body_text_length: 86217,
      page_title: "Deep Reinforcement Learning-based Mining Task Offloading Scheme",
      has_detail_marker: true,
      loading_visible_count: 0,
      loading_text_visible_count: 0,
      hard_stop: false,
    }),
    {hardStop: false, loading: false, reason: "READY"},
  );
});


test("batch validation allows issue/Just Accepted overlap but rejects same-page duplicate", () => {
  const base = {doi: "10.1145/1234567", title: "T", article_type: "Research Article", abstract_status: "partial_teaser", abstract: null};
  assert.equal(validateAcmObservationBatchRows([
    {...base, source_page_url: "https://dl.acm.org/toc/todaes/2026/31/6"},
    {...base, source_page_url: "https://dl.acm.org/toc/todaes/justaccepted"},
  ]).status, "PASS");
  assert.equal(validateAcmObservationBatchRows([
    {...base, source_page_url: "https://dl.acm.org/toc/todaes/2026/31/6"},
    {...base, source_page_url: "https://dl.acm.org/toc/todaes/2026/31/6"},
  ]).status, "FAIL");
});


test("detail parser records full abstract, field provenance, and publication date", () => {
  const record = parseAcmDetailSnapshot({
    page_url: "https://dl.acm.org/doi/10.1145/3774323",
    title: "A detail title",
    authors: ["First Author", "Second Author"],
    abstract: "This is the complete abstract.",
    abstract_status: "full",
    abstract_selector: "section#abstract [role=paragraph]",
    article_type: "RESEARCH-ARTICLE",
    doi: "https://dl.acm.org/doi/10.1145/3774323",
    publication_date: "12 June 2026",
    native_id: "3774323",
    volume: "31",
    issue: "6",
    article_number: "123",
    pages: "1-14",
    field_sources: {
      title: "h1",
      authors: ".loa a",
      abstract: "section#abstract [role=paragraph]",
      article_type: ".article-type",
      doi: "visible DOI link",
      publication_date: "visible_text:Published",
      native_id: "[data-article-id]@data-article-id",
      volume: "meta[name=citation_volume]",
      issue: "meta[name=citation_issue]",
      article_number: "visible_text:Article No.",
      pages: "meta[name=citation_firstpage]",
    },
  }, {
    venueId: "todaes",
    journalCode: "todaes",
    expectedDoi: "10.1145/3774323",
    listingRow: {year: 2027, source_page_url: "https://dl.acm.org/toc/todaes/2027/32/1"},
    observedAt: "2026-08-25T00:00:00.000Z",
  });
  assert.equal(record.doi, "10.1145/3774323");
  assert.equal(record.abstract_status, "full");
  assert.equal(record.abstract, "This is the complete abstract.");
  assert.equal(record.publication_month, 6);
  assert.equal(record.publication_year, 2026);
  assert.equal(record.year, 2027);
  assert.equal(record.field_provenance.abstract.status, "present");
  assert.equal(record.field_provenance.abstract.selector, "section#abstract [role=paragraph]");
  assert.deepEqual(classifyAcmInclusion(record.document_type_raw), {
    decision: "include",
    eligibility: "research_article",
    rule: "explicit_research_article_type",
  });
});


test("detail classifier requires explicit contract types", () => {
  assert.equal(classifyAcmInclusion("SURVEY").decision, "include");
  assert.equal(classifyAcmInclusion("INTRODUCTION").reason_code, "introduction");
  assert.equal(classifyAcmInclusion("EDITORIAL").reason_code, "editorial");
  assert.equal(classifyAcmInclusion("CORRECTION").reason_code, "correction");
  assert.equal(classifyAcmInclusion("FRONT-MATTER").reason_code, "front_matter");
  assert.equal(classifyAcmInclusion("TUTORIAL").reason_code, "non_research_content");
  assert.equal(classifyAcmInclusion("KEYNOTE").reason_code, "non_research_content");
  assert.equal(classifyAcmInclusion("SHORT-PAPER").eligibility, "short_paper");
  assert.equal(classifyAcmInclusion("NOTE").eligibility, "technical_note");
  assert.deepEqual(classifyAcmInclusion("research-article", {title: "Guest Editorial: Special Issue"}), {
    decision: "exclude",
    reason_code: "editorial",
    rule: "explicit_editorial_title",
  });
  assert.deepEqual(classifyAcmInclusion("research-article", {title: "Introduction to the Special Section on FPL 2015"}), {
    decision: "exclude",
    reason_code: "introduction",
    rule: "explicit_special_section_introduction_title",
  });
  assert.equal(classifyAcmInclusion("research-article", {title: "Introduction to FPGA Routing Algorithms"}).decision, "include");
  assert.equal(classifyAcmInclusion("Proceedings Notice").decision, "unknown");
});


test("listing DOI deduplication merges missing fields without changing identity", () => {
  const rows = deduplicateAcmListingRows([
    {doi: "10.1145/1234567", title: "T", authors: []},
    {doi: "https://dl.acm.org/doi/10.1145/1234567", title: "T", authors: ["Author"], abstract_teaser: "Teaser"},
  ]);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].authors, ["Author"]);
  assert.equal(rows[0].abstract_teaser, "Teaser");
});


test("detail crawl emits pipeline-shaped staging rows and resumable outputs", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "acm-visible-detail-test-"));
  let currentUrl = null;
  const fakeSnapshot = {
    page_url: "https://dl.acm.org/doi/10.1145/1234567",
    page_title: "ACM detail",
    body_text_length: 500,
    has_detail_marker: true,
    loading_visible_count: 0,
    loading_text_visible_count: 0,
    title: "T",
    authors: ["Author One"],
    abstract: "Complete abstract",
    abstract_status: "full",
    abstract_selector: "section#abstract [role=paragraph]",
    article_type: "research-article",
    doi: "10.1145/1234567",
    publication_date: "12 June 2026",
    native_id: "1234567",
    volume: "31",
    issue: "6",
    article_number: "10",
    pages: "1-8",
    pdf_url: "https://dl.acm.org/doi/pdf/10.1145/1234567",
    field_sources: {
      title: "dc.Title",
      authors: ".loa a",
      abstract: "section#abstract [role=paragraph]",
      article_type: "dc.Type",
      doi: "visible DOI link",
      publication_date: "visible_text:Published",
      native_id: "meta[name=dc.Identifier]",
      volume: "visible_text:Volume, Issue",
      issue: "visible_text:Volume, Issue",
      article_number: "visible_text:Article No.",
      pages: "visible_text:Pages",
      pdf_discovery_status: "visible PDF/eReader link",
    },
  };
  const fakeTab = {
    async goto(url) { currentUrl = url; },
    async url() { return currentUrl; },
    playwright: {
      async waitForTimeout() {},
      async evaluate() { return {...fakeSnapshot, page_url: currentUrl}; },
    },
  };
  const result = await crawlAcmMetadata({
    tab: fakeTab,
    venueId: "todaes",
    journalCode: "todaes",
    listingRows: [{
      doi: "10.1145/1234567",
      title: "T",
      source_page_url: "https://dl.acm.org/toc/todaes/2026/31/6",
      year: 2026,
    }],
    stagingPath: path.join(root, "metadata_staging.jsonl"),
    exclusionPath: path.join(root, "metadata_exclusions.jsonl"),
    evidencePath: path.join(root, "browser_evidence.jsonl"),
    checkpointPath: path.join(root, "checkpoint.json"),
    errorPath: path.join(root, "errors.jsonl"),
    summaryPath: path.join(root, "summary.json"),
    minStartIntervalMs: 0,
    initialWaitMs: 0,
    retryWaitMs: 0,
    maxWaitRounds: 1,
  });
  const requiredProvenance = ["source_native_id", "title", "authors", "year", "document_type", "landing_url", "abstract", "doi", "publication_date", "pdf_discovery_status"];
  assert.equal(result.summary.status, "PASS");
  assert.equal(result.summary.included, 1);
  assert.equal(result.summary.catalog_ready, false);
  assert.equal(result.staging[0].schema_version, "literature-metadata-staging-v1");
  assert.equal(result.staging[0].pdf_discovery_status, "visible_url");
  for (const field of requiredProvenance) {
    assert.equal(result.staging[0].field_provenance[field].source_url, "https://dl.acm.org/doi/10.1145/1234567");
  }
  await fs.rm(root, {recursive: true, force: true});
});
