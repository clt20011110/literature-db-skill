import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import zlib from "node:zlib";


const SAFE_MISSING_REASONS = Object.freeze({
  abstract: "not_present_on_official_page",
  doi: "checked_missing",
  publication_date: "not_present_on_official_page",
});


function normalizeText(value) {
  if (value == null) return null;
  return String(value).normalize("NFKC").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
}


function titleSignature(value) {
  return (normalizeText(value) || "").toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}


function authorNames(value) {
  if (!Array.isArray(value)) return [];
  return value.map((author) => normalizeText(typeof author === "string" ? author : author?.name)).filter(Boolean);
}


function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}


async function fileSha256(filePath) {
  const handle = await fs.open(filePath, "r");
  const digest = crypto.createHash("sha256");
  try {
    for await (const chunk of handle.readableWebStream()) digest.update(Buffer.from(chunk));
  } finally {
    await handle.close();
  }
  return digest.digest("hex");
}


async function readJsonLines(filePath) {
  let data = await fs.readFile(filePath);
  if (filePath.endsWith(".gz")) data = zlib.gunzipSync(data);
  return data
    .toString("utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        throw new Error(`invalid JSONL at ${filePath}:${index + 1}: ${error.message}`);
      }
    });
}


export async function loadExpectedIeeeItems(expectedRoot) {
  const directory = path.resolve(expectedRoot);
  const names = (await fs.readdir(directory))
    .filter((name) => /^\d{4}\.jsonl(?:\.gz)?$/.test(name))
    .sort((left, right) => Number(left.slice(0, 4)) - Number(right.slice(0, 4)) || left.localeCompare(right));
  if (!names.length) throw new Error(`no yearly expected manifests under ${directory}`);
  const items = [];
  const seen = new Set();
  for (const name of names) {
    const manifestPath = path.join(directory, name);
    for (const row of await readJsonLines(manifestPath)) {
      const identity = String(row.source_native_id || row.source_item_id || row.arnumber || "").trim();
      if (!identity) throw new Error(`expected row lacks source identity in ${manifestPath}`);
      if (seen.has(identity)) throw new Error(`duplicate expected source identity: ${identity}`);
      seen.add(identity);
      items.push({...row, _expected_manifest_path: manifestPath});
    }
  }
  return items;
}


async function readCompletedIds(outputPath) {
  try {
    const rows = await readJsonLines(outputPath);
    return new Set(rows.map((row) => String(row.source_native_id || "")).filter(Boolean));
  } catch (error) {
    if (error.code === "ENOENT") return new Set();
    throw error;
  }
}


function exclusionReason(record, config = {}) {
  const title = (normalizeText(record.title) || "").toLocaleLowerCase();
  const abstract = (normalizeText(record.abstract) || "").toLocaleLowerCase();
  const publicationTitle = (normalizeText(record.venue_identity?.publication_title) || "").toLocaleLowerCase();
  const authorless = !Array.isArray(record.authors) || record.authors.length === 0;
  if (
    authorless &&
    /^\[?blank page(?:\s*[-–—]\s*(?:front|back) cover)?\]?[.!]?$/u.test(title)
  ) {
    return {reason: "blank_page", rule: "authorless_official_blank_page_or_cover_marker"};
  }
  if (
    authorless &&
    /^(?:ieee membership can help you reach your personal goals|expand your professional network with ieee|technology insight on demand on ieee\.tv|ieee access|present a world of opportunity)[.!]?$/u.test(title)
  ) {
    return {reason: "promotional_content", rule: "authorless_official_ieee_membership_or_publication_promotion"};
  }
  if (authorless && /^announcing terahertz letters[.!]?$/u.test(title)) {
    return {reason: "announcement", rule: "authorless_official_ieee_publication_announcement"};
  }
  if ((!Array.isArray(record.authors) || record.authors.length === 0) && /^advertisement\b/u.test(abstract)) {
    return {reason: "advertisement", rule: "authorless_official_abstract_begins_advertisement"};
  }
  if (
    (!Array.isArray(record.authors) || record.authors.length === 0) &&
    /^prospective authors are requested to submit\b/u.test(abstract)
  ) {
    return {reason: "call_for_papers", rule: "authorless_official_abstract_requests_manuscript_submissions"};
  }
  if ((!Array.isArray(record.authors) || record.authors.length === 0) && /^\d{4} index/u.test(title)) {
    return {reason: "index", rule: "authorless_title_begins_four_digit_year_and_index"};
  }
  if (
    (!Array.isArray(record.authors) || record.authors.length === 0) &&
    (title === "[blank page]" || /^this page or pages intentionally left blank\b/u.test(abstract))
  ) {
    return {reason: "blank_page", rule: "authorless_official_blank_page_marker"};
  }
  if (
    (!Array.isArray(record.authors) || record.authors.length === 0) &&
    /^join the ieee .+ society(?: .+)? today$/u.test(title)
  ) {
    return {reason: "promotional_content", rule: "authorless_join_ieee_society_promotion"};
  }
  if (title && title === publicationTitle && (!Array.isArray(record.authors) || record.authors.length === 0)) {
    return {reason: "masthead", rule: "authorless_title_equals_publication_title"};
  }
  // ISCAS has explicit programme/front-matter records mixed into the same
  // proceedings collection.  These rules mirror the visible listing
  // classifier and are intentionally title-scoped; authored research papers
  // are not excluded merely because the proceedings contain programme text.
  if (publicationTitle.includes("international symposium on circuits and systems")) {
    if (authorless && title === "casfest") {
      return {reason: "non_main_track", rule: "authorless_exact_iscas_casfest_activity_material"};
    }
    if (authorless && title === "tcas special") {
      return {reason: "non_main_track", rule: "authorless_exact_iscas_tcas_special_non_main_track_material"};
    }
    const exactAuthorlessRules = [
      [/^technical program co-chairs message$/u, "front_matter"],
      [/^iscas\s+\d{4}\s+about\s*&\s*welcome message$/u, "front_matter"],
      [/^iscas\s+\d{4}\s+conference patrons\s*&\s*sponsors$/u, "front_matter"],
      [/^iscas\s+\d{4}\s+awards,\s*fellows\s*&\s*leadership$/u, "non_research_content"],
      [/^about iscas\s+\d{4}$/u, "front_matter"],
      [/^about the ieee circuits and systems society$/u, "front_matter"],
      [/^welcome from the general chair\/co-chairs$/u, "front_matter"],
      [/^welcome from the technical programme chair\/co-chairs$/u, "front_matter"],
      [/^conference patrons, supporters, and sponsors$/u, "front_matter"],
      [/^paper types$/u, "front_matter"],
      [/^a message from foodcas$/u, "non_main_track"],
      [/^social programme$/u, "non_main_track"],
      [/^iscas\s+\d{4}\s+virtual$/u, "non_main_track"],
      [/^track structure$/u, "front_matter"],
      [/^welcome to iscas\s+\d{4}\s+from the general co-chairs$/u, "front_matter"],
      [/^conference sponsors and support$/u, "front_matter"],
      [/^technical papers$/u, "front_matter"],
      [/^ieee awards$/u, "non_research_content"],
      [/^copyright information$/u, "copyright_form"],
      [/^author-index$/u, "index"],
      [/^iscas\s+\d{4}\s+front matter$/u, "front_matter"],
    ];
    if (authorless) {
      for (const [pattern, reason] of exactAuthorlessRules) {
        if (pattern.test(title)) return {reason, rule: String(pattern)};
      }
    }
    const iscasRules = [
      // IEEE occasionally exposes a navigation/menu or sponsor leaf as a
      // pseudo-document in the proceedings stream.  These authorless pages
      // have no research payload and must be excluded explicitly rather than
      // stopping the detail crawl for an ambiguous authorless item.
      [/^menu$/u, "front_matter"],
      [/^conference support\s*&\s*sponsors$/u, "front_matter"],
      [/^(?:iscas\s+\d{4}\s+)?(?:table of contents|contents|toc)$/u, "table_of_contents"],
      [/^(?:iscas\s+\d{4}\s+)?(?:author|keyword|session chair) index$/u, "index"],
      [/^(?:iscas\s+\d{4}\s+)?author[- ]index$/u, "index"],
      [/^(?:iscas\s+\d{4}\s+)?(?:copyright(?: page| notice)?|content announcement page)$/u, "copyright_form"],
      [/^(?:\[?iscas\s+\d{4}\s+)?title page\]?$/u, "front_matter"],
      [/^\[?title page\]?$/u, "front_matter"],
      [/^(?:iscas\s+\d{4}\s+)?cover(?: page)?$/u, "front_matter"],
      [/^(?:iscas\s+\d{4}\s+)?(?:opinion|commentary|ad page|contributor page)$/u, "front_matter"],
      [/^(?:iscas\s+\d{4}\s+)?(?:committees?|conference committee|organizing committee|technical program committee|review committee members?|track chairs?|tracks structure)$/u, "front_matter"],
      [/^(?:iscas\s+\d{4}\s+)?(?:about iscas|about ieee circuits and systems society)$/u, "front_matter"],
      [/^(?:iscas\s+\d{4}\s+)?(?:welcome message.*|honorary chair message)$/u, "front_matter"],
      [/^(?:general|technical program(?:me)?) chair(?:s)? message$/u, "front_matter"],
      [/^.+\((?:special session|invited) paper\)$/u, "non_main_track"],
      [/^.+\btutorial review$/u, "non_main_track"],
      [/^(?:iscas\s+\d{4}\s+)?keynotes?\s*&\s*social programme$/u, "non_research_content"],
      [/^(?:iscas\s+\d{4}\s+)?(?:keynote speakers?|overview lectures?|tutorials?|mini-tutorials?|special sessions?|contests? and technical events|foodcas)$/u, "non_main_track"],
      [/^(?:session|technical session|special session)\b.*$/u, "non_main_track"],
      [/^(?:live\s+)?demonstration\s*:/u, "non_main_track"],
      [/^(?:tutorial|mini-tutorial|keynote|overview lecture|special session|contest|foodcas)\b/u, "non_main_track"],
    ];
    for (const [pattern, reason] of iscasRules) {
      if (pattern.test(title)) return {reason, rule: String(pattern)};
    }
  }
  const rules = [
    [/^open access$/u, "promotional_content"],
    [/^(?:ieee )?open access publishing$/u, "promotional_content"],
    [/^imagine a community hopeful for the future[.!]?$/u, "advertisement"],
    [/^expand your network,? get rewarded[.!]?$/u, "promotional_content"],
    [/^member get-a-member \(mgm\) program$/u, "promotional_content"],
    [/^how can you get your idea to market first[?]?[.!]?$/u, "advertisement"],
    [/^introducing ieee collabratec$/u, "advertisement"],
    [/^ieee foundation - reflecting on 50 years of impact$/u, "promotional_content"],
    [/^\[masthead\]$/u, "masthead"],
    [/^\[?front matter\]?$/u, "front_matter"],
    [/^blank page$/u, "blank_page"],
    [/^(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:the )?(?:table of contents|contents|toc)$/u, "table_of_contents"],
    [/^departments?\s*\[table of contents\]$/u, "table_of_contents"],
    [/society information$/u, "society_information"],
    [/publication information$/u, "publication_information"],
    [/^\[?publisher'?s information\]?$/u, "front_matter"],
    [/\binformation for authors\b/u, "information_for_authors"],
    [/^\[?(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:ieee\s+)?copyright(?: notice| page| form)?\]?$/u, "copyright_form"],
    [/(?:^|\s)editorial$/u, "editorial"],
    [/^guest editor(?:s|s’|s')?\s+(?:introduction|editorial|foreword)\b/u, "editorial"],
    [/^\d{4}\b.+\bdigest of technical papers$/u, "masthead"],
    [/^proceedings of the \d{4}\b.+\bconference\b/u, "masthead"],
    [/^(?:[a-z0-9&/.-]+\s+)*\d{4}\s+conference proceedings$/u, "front_matter"],
    [/^(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:foreword|preface|welcome message)$/u, "front_matter"],
    [/^(?:welcome )?message from (?:the )?(?:(?:general|program) chairs?|general chairs? and program chairs?)(?::?\s+iccd\s+\d{4})?$/u, "front_matter"],
    [/^(?:organizing|program) committee(?:\s+iccd\s+\d{4})?$/u, "front_matter"],
    [/^(?:iccd\s+\d{4}\s+)?poster session list$/u, "non_main_track"],
    [/^sponsors?\s+and\s+organizers?$/u, "front_matter"],
    [/^(?:corporate\s+)?sponsors?(?:\s+of\s+(?:the\s+)?technical program committee dinner)?$/u, "front_matter"],
    [/^(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:committees?|executive committee|organizing committee|program committee|steering committee)$/u, "front_matter"],
    [/(?:^|:)\s*from the eic\b|\[from the eic\]$/u, "editorial"],
    [/^the last byte\b|\[the last byte\]$/u, "editorial"],
    [/^best in test$/u, "editorial"],
    [/^special issue (?:on|dedicated to)\b/u, "editorial"],
    [/(?:^|\s)ceda currents$/u, "news"],
    [/^(?:test technology tc|tttc)\s+(?:news|newsletter)\b/u, "news"],
    [/^report on\b/u, "news"],
    [/^(?:an )?interview with\b/u, "news"],
    [/^corrections?(?:\s+to)?\b/u, "correction"],
    [/^errat(?:um|a)\b/u, "erratum"],
    [/^retraction\b/u, "retraction"],
    [/^(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:(?:author|subject)\s+)?index$/u, "index"],
    [/^(?:\[(?:front|back)(?: inside)? cover\]|(?:front|back) cover|cover [234]|(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?cover page)$/u, "cover"],
    [/^acknowledg(?:e)?ment(?:s)?$/u, "acknowledgment"],
    [/^announcement\b/u, "announcement"],
    [/\bcall for (?:contributions|nominations)\b/u, "announcement"],
    [/^call for papers\b/u, "call_for_papers"],
    [/\bcall for papers, workshops, tutorials$/u, "call_for_papers"],
    [/^search for editor-in-chief\b/u, "announcement"],
    [/\bseeks editor-in-chief\b/u, "announcement"],
    [/^call for applications and nominations\b/u, "announcement"],
    [/^obituary\b/u, "obituary"],
    [/^in memoriam\b/u, "obituary"],
    [/^book review\b/u, "book_review"],
    [/\[(?:ieee xplore )?advertisement\]$/u, "advertisement"],
    [/(?:^|\s)(?:foundation|dataport|recruit|access|wie|half-page) ad$/u, "advertisement"],
    [/^ieee was here(?: \[advertisement\])?$/u, "advertisement"],
    [/^(?:ieee|my ?ieee|ieee semantic|ieee coll(?:a)?bratec|ieee proceedings|proceedings of the ieee)$/u, "promotional_content"],
    [/^(?:ieee women in engineering|ieee membership|join ieee|ieee foundation|ieee\.tv|ieee dataport|ieee app)$/u, "promotional_content"],
    [/^techrxiv(?::|$)/u, "promotional_content"],
    [/^get in the conversation[!]$/u, "promotional_content"],
    [/^ieee connects you to a universe of information[!]$/u, "promotional_content"],
    [/^update your ieee profile$/u, "promotional_content"],
    [/^ieee design&test is going paperless in 2022[!]$/u, "promotional_content"],
    [/^together, we are advancing technology$/u, "promotional_content"],
    [/^educational activities$/u, "news"],
    [/^phaser data$/u, "advertisement"],
    [/^the 2016 dac art show grand prize winner\b/u, "news"],
    [/^the 2016 dac art show winner\b/u, "news"],
    [/^itc and the future of test\b/u, "news"],
    [/^ieee embedded systems letters now in emerging sources citation index$/u, "promotional_content"],
    [/\b(?:roundtable|plenary panel)\b/u, "non_research_content"],
  ];
  for (const [pattern, reason] of rules) if (pattern.test(title)) return {reason, rule: String(pattern)};
  const editorialAuthors = new Set(
    (config.singlePageEditorialAuthors || []).map((value) => (normalizeText(value) || "").toLocaleLowerCase()),
  );
  const recordAuthors = authorNames(record.authors).map((value) => value.toLocaleLowerCase());
  const uniqueRecordAuthors = [...new Set(recordAuthors)];
  const startPage = Number.parseInt(String(record.start_page || ""), 10);
  const endPage = Number.parseInt(String(record.end_page || ""), 10);
  if (
    editorialAuthors.size &&
    Number.isFinite(startPage) &&
    startPage === endPage &&
    uniqueRecordAuthors.length === 1 &&
    editorialAuthors.has(uniqueRecordAuthors[0])
  ) {
    return {reason: "editorial", rule: "venue_configured_signed_single_page_editorial_column"};
  }
  return null;
}


// Export the pure classifier so venue playbooks and tests can audit title/type
// coverage without opening publisher pages.  The crawler still makes its final
// decision only after loading and validating each official detail record.
export function classifyIeeeExclusion(record, config = {}) {
  return exclusionReason(record, config);
}


function toExclusionRecord(record, classification) {
  const observedAt = record.observed_at;
  const detailPageRevalidated = record._detail_page_revalidated !== false;
  const provenanceMethod = detailPageRevalidated
    ? "official_ieee_inline_metadata_and_title_scope_rule"
    : "official_ieee_visible_toc_listing_and_scope_rule";
  const reuseStatus = detailPageRevalidated
    ? "expected_manifest_reused_and_fresh_detail_revalidated"
    : "accepted_expected_manifest_reused_and_formal_exclusion_schema_revalidated";
  const provenance = {};
  for (const field of ["source_native_id", "title", "year", "inclusion_decision", "exclusion_reason_code"]) {
    provenance[field] = {
      source_url: record.source_url,
      observed_at: observedAt,
      method: provenanceMethod,
      reuse_status: reuseStatus,
      status: "excluded",
    };
  }
  return {
    schema_version: "literature-metadata-exclusion-v1",
    venue_id: record.venue_id,
    source_native_id: record.source_native_id,
    title: record.title,
    authors: record.authors,
    year: record.year,
    document_type: record.document_type,
    landing_url: record.landing_url,
    source_url: record.source_url,
    observed_at: observedAt,
    is_early_access: record.is_early_access,
    baseline_enumeration_kind: record.baseline_enumeration_kind,
    reuse_evidence: record.reuse_evidence,
    inclusion_decision: "exclude",
    exclusion_reason_code: classification.reason,
    exclusion_evidence: {
      matched_title_rule: classification.rule,
      official_author_count: Array.isArray(record.authors) ? record.authors.length : 0,
      official_document_type: record.document_type,
      detail_page_revalidated: detailPageRevalidated,
      official_listing_revalidated: Boolean(record._official_listing_revalidated),
      official_listing_url: record.source_page_url || null,
    },
    field_provenance: provenance,
  };
}


async function writeJson(filePath, value) {
  const target = path.resolve(filePath);
  await fs.mkdir(path.dirname(target), {recursive: true});
  const temporary = `${target}.tmp-${crypto.randomUUID()}`;
  await fs.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await fs.rename(temporary, target);
}


async function appendJsonLine(filePath, value) {
  const target = path.resolve(filePath);
  await fs.mkdir(path.dirname(target), {recursive: true});
  await fs.appendFile(target, `${JSON.stringify(value)}\n`, "utf8");
}


async function extractCurrentIeeeRecord(tab, config) {
  return await tab.playwright.evaluate((arg) => {
    const bodyText = (document.body?.innerText || "").slice(0, 10000);
    const hardStop = /captcha|verify you are human|access denied|automated access|request blocked|temporarily blocked/i.test(bodyText);
    const script = [...document.scripts].find((item) => (item.textContent || "").includes("xplGlobal.document.metadata="));
    if (!script) {
      return {
        error: hardStop ? "ACCESS_OR_CAPTCHA_BLOCK" : "METADATA_SCRIPT_MISSING",
        page_url: location.href,
        page_title: document.title,
        body_text_length: bodyText.length,
      };
    }
    const text = script.textContent || "";
    const marker = "xplGlobal.document.metadata=";
    let start = text.indexOf(marker) + marker.length;
    while (/\s/.test(text[start])) start += 1;
    let depth = 0;
    let inString = false;
    let escaped = false;
    let end = -1;
    for (let index = start; index < text.length; index += 1) {
      const character = text[index];
      if (inString) {
        if (escaped) escaped = false;
        else if (character === "\\") escaped = true;
        else if (character === '"') inString = false;
        continue;
      }
      if (character === '"') inString = true;
      else if (character === "{") depth += 1;
      else if (character === "}") {
        depth -= 1;
        if (depth === 0) {
          end = index + 1;
          break;
        }
      }
    }
    if (end < 0) return {error: "METADATA_JSON_UNTERMINATED", page_url: location.href, page_title: document.title};
    let metadata;
    try {
      metadata = JSON.parse(text.slice(start, end));
    } catch (error) {
      return {error: "METADATA_JSON_INVALID", detail: error.message, page_url: location.href, page_title: document.title};
    }
    const observedAt = new Date().toISOString();
    const nativeId = String(metadata.articleNumber || metadata.articleId || "");
    const landingUrl = `${location.origin}/document/${nativeId}`;
    const pdfUrl = metadata.pdfUrl ? new URL(metadata.pdfUrl, location.origin).href : null;
    const decodeEntities = (value) => String(value || "").replace(
      /&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi,
      (match, entity) => {
        const normalized = entity.toLowerCase();
        if (normalized === "amp") return "&";
        if (normalized === "lt") return "<";
        if (normalized === "gt") return ">";
        if (normalized === "quot") return '"';
        if (normalized === "apos") return "'";
        if (normalized === "nbsp") return " ";
        const codePoint = normalized.startsWith("#x")
          ? Number.parseInt(normalized.slice(2), 16)
          : Number.parseInt(normalized.slice(1), 10);
        return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
      },
    );
    const authors = Array.isArray(metadata.authors)
      ? metadata.authors
          .map((author) => ({
            name: author?.name ? decodeEntities(author.name).replace(/\s+/g, " ").trim() : null,
            affiliations: Array.isArray(author?.affiliation)
              ? author.affiliation.map((value) => decodeEntities(value).replace(/\s+/g, " ").trim())
              : [],
            native_id: author?.id == null ? null : String(author.id),
          }))
          .filter((author) => author.name)
      : [];
    const publicationDate = metadata.displayPublicationDate || metadata.publicationDate || null;
    const displayTitle = metadata.displayDocTitle || metadata.title || null;
    const normalizedDisplayTitle = displayTitle
      ? decodeEntities(
          String(displayTitle)
            .replace(/<tex-math\b[^>]*>([\s\S]*?)<\/tex-math>/gi, "$1")
            .replace(/<(?:br|hr)\b[^>]*>/gi, " ")
            .replace(/<[^>]+>/g, ""),
        ).replace(/\s+/g, " ").trim() || null
      : null;
    const strippedTitle = metadata.formulaStrippedArticleTitle || null;
    // displayDocTitle is the visible authoritative surface. Normalize its markup
    // instead of preferring formulaStrippedArticleTitle, which can silently drop
    // symbols such as Greek letters, k-induction notation, and inline variables.
    const title = normalizedDisplayTitle || strippedTitle;
    const missingFields = {};
    if (!metadata.abstract) missingFields.abstract = {reason_code: arg.missingReasons.abstract};
    if (!metadata.doi) missingFields.doi = {reason_code: arg.missingReasons.doi};
    if (!publicationDate) missingFields.publication_date = {reason_code: arg.missingReasons.publication_date};
    const baseProvenance = {
      source_url: landingUrl,
      observed_at: observedAt,
      method: "official_ieee_inline_metadata",
      reuse_status: "expected_manifest_reused_and_fresh_detail_revalidated",
      status: "present",
    };
    const requiredProvenance = [
      "source_native_id",
      "title",
      "authors",
      "year",
      "document_type",
      "landing_url",
      "abstract",
      "doi",
      "publication_date",
      "pdf_discovery_status",
    ];
    const fieldProvenance = Object.fromEntries(
      requiredProvenance.map((field) => [
        field,
        {
          ...baseProvenance,
          status:
            (field === "abstract" && !metadata.abstract) ||
            (field === "doi" && !metadata.doi) ||
            (field === "publication_date" && !publicationDate)
              ? "missing"
              : "present",
        },
      ]),
    );
    return {
      schema_version: "literature-metadata-staging-v1",
      venue_id: arg.venueId,
      source_native_id: nativeId,
      title,
      authors,
      abstract: metadata.abstract || null,
      document_type: metadata.isJournal ? "journal-article" : metadata.xploreDocumentType || null,
      publication_date: publicationDate,
      year: Number(metadata.publicationYear),
      volume: metadata.volume || null,
      issue: metadata.issue || null,
      pages: metadata.startPage || metadata.endPage ? [metadata.startPage, metadata.endPage].filter(Boolean).join("-") : null,
      start_page: metadata.startPage || null,
      end_page: metadata.endPage || null,
      doi: metadata.doi || null,
      doi_status: metadata.doi ? "present" : "checked_missing",
      landing_url: landingUrl,
      pdf_url: pdfUrl,
      pdf_discovery_status: pdfUrl ? "landing_page_action" : "not_visible",
      inclusion_decision: "include",
      source_url: landingUrl,
      observed_at: observedAt,
      is_early_access: Boolean(metadata.isEarlyAccess),
      venue_identity: {
        publication_number: metadata.publicationNumber == null ? null : String(metadata.publicationNumber),
        publication_title: metadata.publicationTitle || metadata.displayPublicationTitle || null,
        issn: metadata.issn || [],
        isnumber: metadata.isNumber == null ? null : String(metadata.isNumber),
      },
      missing_fields: missingFields,
      field_provenance: fieldProvenance,
    };
  }, {venueId: config.venueId, missingReasons: SAFE_MISSING_REASONS}, {timeoutMs: 15000});
}


function validateFreshRecord(record, expected, config) {
  const errors = [];
  const expectedId = String(expected.source_native_id || expected.source_item_id || expected.arnumber || "");
  if (record.source_native_id !== expectedId) errors.push("source_native_id_mismatch");
  if (record.venue_id !== config.venueId) errors.push("venue_id_mismatch");
  const expectedPublicationNumber = config.allowPerItemPublicationNumber
    ? String(expected.expected_publication_number || expected.collection_id || expected.selected_unit_isnumber || "")
    : String(config.publicationNumber);
  if (!expectedPublicationNumber || record.venue_identity?.publication_number !== expectedPublicationNumber) {
    errors.push("publication_number_mismatch");
  }
  const observedPublicationTitle = normalizeText(record.venue_identity?.publication_title) || "";
  if (config.publicationTitlePattern) {
    let pattern = null;
    try {
      pattern = new RegExp(config.publicationTitlePattern, "iu");
    } catch {
      errors.push("publication_title_pattern_invalid");
    }
    if (pattern && !pattern.test(observedPublicationTitle)) errors.push("publication_title_mismatch");
  } else if (observedPublicationTitle !== normalizeText(config.publicationTitle)) {
    errors.push("publication_title_mismatch");
  }
  const expectedUnit = String(expected.selected_unit_id || expected.source_page_url || "");
  const expectedIsNumber = new URL(expectedUnit || "https://invalid.invalid/").searchParams.get("isnumber");
  const expectedEnumerationKind = config.earlyAccessIsNumbers.includes(String(expectedIsNumber)) ? "early_access" : "issue";
  const yearMatch = Number(record.year) === Number(expected.year);
  if (!yearMatch && expectedEnumerationKind !== "early_access") errors.push("year_mismatch");
  const expectedDoi = normalizeText(expected.doi)?.toLowerCase() || null;
  const observedDoi = normalizeText(record.doi)?.toLowerCase() || null;
  if (expectedDoi && observedDoi && expectedDoi !== observedDoi) errors.push("doi_conflict");
  const titleMatch = titleSignature(record.title) === titleSignature(expected.title);
  const expectedAuthors = authorNames(expected.authors);
  const observedAuthors = authorNames(record.authors);
  const authorOrderMatch = !expectedAuthors.length || JSON.stringify(expectedAuthors) === JSON.stringify(observedAuthors);
  record.source_page_url = expected.source_page_url || expected.selected_unit_id || null;
  record.enumeration_observed_at = expected.observed_at || null;
  record.baseline_enumeration_kind = expectedEnumerationKind;
  record.reuse_evidence = {
    expected_manifest_path: expected._expected_manifest_path,
    expected_request_id: expected.request_id || null,
    expected_observed_at: expected.observed_at || null,
    expected_enumeration_kind: expectedEnumerationKind,
    baseline_year: Number(expected.year),
    detail_year: Number(record.year),
    year_match: yearMatch,
    title_match: titleMatch,
    author_order_match: authorOrderMatch,
    doi_match: !expectedDoi || !observedDoi || expectedDoi === observedDoi,
    expected_source_grade: expected.source_grade || null,
  };
  if (!yearMatch && expectedEnumerationKind === "early_access") {
    record.field_provenance.year.reuse_status = "count_baseline_year_reclassified_by_fresh_official_detail";
    record.field_provenance.year.baseline_value = Number(expected.year);
    record.field_provenance.year.reclassification_reason = "early_access_listing_was_grouped_under_crawl_waterline_year";
  }
  return {
    errors,
    warnings: [
      ...(!titleMatch ? ["title_surface_changed"] : []),
      ...(!authorOrderMatch ? ["author_surface_changed"] : []),
      ...(!yearMatch && expectedEnumerationKind === "early_access" ? ["early_access_year_reclassified"] : []),
    ],
  };
}


function applyOfficialMetadataSupplement(record, expected, identity, supplement, allowedHosts) {
  if (!supplement) return [];
  if (String(supplement.source_native_id || "") !== identity) {
    throw new Error(`official author supplement identity mismatch for ${identity}`);
  }
  const sourceUrl = new URL(String(supplement.source_url || ""));
  const approvedHosts = new Set((allowedHosts || []).map((value) => String(value).toLocaleLowerCase()));
  if (sourceUrl.protocol !== "https:" || !approvedHosts.has(sourceUrl.hostname.toLocaleLowerCase())) {
    throw new Error(`unapproved official author supplement URL for ${identity}`);
  }
  const sourceTitle = normalizeText(supplement.title);
  if (!sourceTitle || titleSignature(sourceTitle) !== titleSignature(record.title)) {
    throw new Error(`official author supplement title mismatch for ${identity}`);
  }
  const expectedTitle = normalizeText(expected.title);
  if (expectedTitle && titleSignature(sourceTitle) !== titleSignature(expectedTitle)) {
    throw new Error(`official author supplement expected-title mismatch for ${identity}`);
  }
  const observedAt = normalizeText(supplement.observed_at);
  if (!observedAt || Number.isNaN(Date.parse(observedAt))) {
    throw new Error(`official author supplement has invalid observation timestamp for ${identity}`);
  }
  const method = normalizeText(supplement.method) || "official_venue_program_visible_record";
  const appliedFields = [];
  const suppliedAuthors = Array.isArray(supplement.authors)
    ? supplement.authors.map((author) => ({
        name: normalizeText(typeof author === "string" ? author : author?.name),
        affiliations: Array.isArray(author?.affiliations)
          ? author.affiliations.map(normalizeText).filter(Boolean)
          : [],
        native_id: null,
      })).filter((author) => author.name)
    : [];
  if (!Array.isArray(record.authors) || record.authors.length === 0) {
    if (!suppliedAuthors.length) throw new Error(`official metadata supplement has no authors for ${identity}`);
    record.authors = suppliedAuthors;
    record.field_provenance.authors = {
      source_url: sourceUrl.href,
      observed_at: observedAt,
      method,
      reuse_status: "fresh_ieee_detail_missing_authors_supplemented_from_official_venue_program",
      status: "present",
    };
    appliedFields.push("authors");
  } else if (suppliedAuthors.length && JSON.stringify(authorNames(record.authors)) !== JSON.stringify(authorNames(suppliedAuthors))) {
    throw new Error(`official metadata supplement author conflict for ${identity}`);
  }
  const suppliedDoi = normalizeText(supplement.doi)?.replace(/^https?:\/\/(?:dx\.)?doi\.org\//iu, "") || null;
  if (suppliedDoi && !/^10\.\d{4,9}\/\S+$/iu.test(suppliedDoi)) {
    throw new Error(`official metadata supplement DOI is invalid for ${identity}`);
  }
  const recordDoi = normalizeText(record.doi)?.toLocaleLowerCase() || null;
  if (suppliedDoi && recordDoi && suppliedDoi.toLocaleLowerCase() !== recordDoi) {
    throw new Error(`official metadata supplement DOI conflict for ${identity}`);
  }
  if (suppliedDoi && !recordDoi) {
    record.doi = suppliedDoi;
    record.doi_status = "present";
    if (record.missing_fields) delete record.missing_fields.doi;
    record.field_provenance.doi = {
      source_url: sourceUrl.href,
      observed_at: observedAt,
      method,
      reuse_status: "fresh_ieee_detail_missing_doi_supplemented_from_official_venue_proceedings",
      status: "present",
    };
    appliedFields.push("doi");
  }
  record.reuse_evidence.official_metadata_supplement = {
    source_url: sourceUrl.href,
    observed_at: observedAt,
    method,
    source_native_id: identity,
    title_match: true,
    author_count: suppliedAuthors.length,
    applied_fields: appliedFields,
    evidence_note: normalizeText(supplement.evidence_note),
  };
  return appliedFields;
}


function safeUrlForExpected(expected, allowedHost) {
  const identity = String(expected.source_native_id || expected.source_item_id || expected.arnumber || "");
  const raw = expected.landing_url || expected.source_url;
  if (!raw) throw new Error(`expected identity ${identity} lacks a landing URL`);
  const url = new URL(raw);
  if (url.protocol !== "https:" || url.hostname !== allowedHost) throw new Error(`unapproved expected URL for ${identity}`);
  if (!new RegExp(`/document/${identity}/?$`).test(url.pathname)) throw new Error(`landing URL identity mismatch for ${identity}`);
  url.search = "";
  url.hash = "";
  return url.href;
}


async function currentTabUrl(tab) {
  try {
    return typeof tab?.url === "function" ? await tab.url() : null;
  } catch {
    return null;
  }
}


function sameCanonicalIeeeDocumentUrl(left, right) {
  try {
    const first = new URL(left);
    const second = new URL(right);
    return first.protocol === second.protocol
      && first.hostname === second.hostname
      && first.pathname.replace(/\/$/, "") === second.pathname.replace(/\/$/, "");
  } catch {
    return false;
  }
}


function listingRecordForExpected(expected, config) {
  const identity = String(expected.source_native_id || expected.source_item_id || expected.arnumber || "");
  const landingUrl = safeUrlForExpected(expected, config.allowedHost);
  const pageRange = String(expected.page_range || expected.pages || "").trim();
  const pageMatch = pageRange.match(/^(\d+)\s*[-–—]\s*(\d+)$/u);
  const enumerationKind = expected.baseline_enumeration_kind || "issue";
  return {
    schema_version: "literature-metadata-exclusion-v1",
    venue_id: config.venueId,
    source_native_id: identity,
    title: normalizeText(expected.title),
    authors: authorNames(expected.authors).map((name) => ({name, affiliations: [], native_id: null})),
    year: Number(expected.year),
    document_type: expected.document_type || "journal-article",
    landing_url: landingUrl,
    source_url: landingUrl,
    source_page_url: expected.source_page_url || expected.selected_unit_id || null,
    observed_at: expected.observed_at || new Date().toISOString(),
    is_early_access: enumerationKind === "early_access",
    baseline_enumeration_kind: enumerationKind,
    start_page: pageMatch ? pageMatch[1] : null,
    end_page: pageMatch ? pageMatch[2] : null,
    venue_identity: {
      publication_number: config.allowPerItemPublicationNumber
        ? String(expected.expected_publication_number || expected.collection_id || expected.selected_unit_isnumber || "")
        : String(config.publicationNumber),
      publication_title: expected.expected_publication_title || config.publicationTitle,
    },
    reuse_evidence: {
      expected_manifest_path: expected._expected_manifest_path,
      expected_request_id: expected.request_id || null,
      expected_observed_at: expected.observed_at || null,
      expected_enumeration_kind: enumerationKind,
      expected_source_grade: expected.source_grade || null,
      listing_title_reused: true,
      formal_exclusion_schema_revalidated: true,
    },
    _detail_page_revalidated: false,
    _official_listing_revalidated: true,
  };
}


async function wait(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}


export async function crawlIeeeMetadata(options) {
  const {
    tab,
    venueId,
    publicationNumber,
    publicationTitle,
    expectedRoot,
    outputPath,
    exclusionPath = null,
    checkpointPath,
    errorPath,
    summaryPath,
    allowedHost = "ieeexplore.ieee.org",
    allowPerItemPublicationNumber = false,
    publicationTitlePattern = null,
    earlyAccessIsNumbers = [],
    singlePageEditorialAuthors = [],
    officialMetadataSupplements = {},
    allowedSupplementalHosts = [],
    allowListingPreclassification = false,
    reuseCurrentPage = true,
    minStartIntervalMs = 6000,
    batchSize = 50,
    maxAttempts = 5,
    transientRetryCooldownMs = 15000,
    maxItems = Number.POSITIVE_INFINITY,
    // A source-level metadata blocker must not prevent unrelated expected
    // identities from being checkpointed.  Skips are opt-in, require a
    // controller-authored evidence file, and are never counted as completed
    // or excluded; finalization still requires every expected identity.
    skipSourceNativeIds = [],
    skipEvidencePath = null,
  } = options;
  if (!tab) throw new Error("a controlled in-app Browser tab is required");
  if (!venueId || !publicationNumber || !publicationTitle) throw new Error("venue identity is incomplete");
  if (publicationTitlePattern) {
    try {
      new RegExp(publicationTitlePattern, "iu");
    } catch (error) {
      throw new Error(`publicationTitlePattern is invalid: ${error.message}`);
    }
  }
  if (!outputPath || !checkpointPath || !errorPath || !summaryPath) throw new Error("explicit output/checkpoint/error/summary paths are required");
  const resolvedOutput = path.resolve(outputPath);
  const resolvedExclusions = exclusionPath ? path.resolve(exclusionPath) : null;
  const resolvedCheckpoint = path.resolve(checkpointPath);
  const resolvedErrors = path.resolve(errorPath);
  const resolvedSummary = path.resolve(summaryPath);
  const items = await loadExpectedIeeeItems(expectedRoot);
  const skipIds = new Set((Array.isArray(skipSourceNativeIds) ? skipSourceNativeIds : []).map((value) => String(value).trim()).filter(Boolean));
  const expectedIds = new Set(items.map((row) => String(row.source_native_id || row.source_item_id || row.arnumber || "").trim()));
  const unknownSkipIds = [...skipIds].filter((identity) => !expectedIds.has(identity));
  if (unknownSkipIds.length) throw new Error(`skipSourceNativeIds contain unknown expected identities: ${unknownSkipIds.join(", ")}`);
  if (skipIds.size && !skipEvidencePath) throw new Error("skipSourceNativeIds require an explicit skipEvidencePath");
  let skipEvidence = null;
  if (skipEvidencePath) {
    const resolvedSkipEvidence = path.resolve(skipEvidencePath);
    try {
      skipEvidence = JSON.parse(await fs.readFile(resolvedSkipEvidence, "utf8"));
    } catch (error) {
      throw new Error(`invalid skip evidence: ${resolvedSkipEvidence}: ${error.message}`);
    }
    if (!skipEvidence || typeof skipEvidence !== "object") throw new Error("skip evidence must be a JSON object");
    const evidenceIds = new Set((Array.isArray(skipEvidence.pending_ids) ? skipEvidence.pending_ids : []).map((value) => String(value).trim()));
    for (const identity of skipIds) if (!evidenceIds.has(identity)) throw new Error(`skip evidence does not list pending identity ${identity}`);
    if (skipEvidence.catalog_write === true || skipEvidence.catalog_ready === true) throw new Error("skip evidence cannot claim catalog readiness");
  }
  const completedIncluded = await readCompletedIds(resolvedOutput);
  const completedExcluded = resolvedExclusions ? await readCompletedIds(resolvedExclusions) : new Set();
  const completed = new Set([...completedIncluded, ...completedExcluded]);
  const startedAt = new Date().toISOString();
  let completedThisRun = 0;
  let includedThisRun = 0;
  let excludedThisRun = 0;
  let browserItemsThisRun = 0;
  let listingPreclassifiedThisRun = 0;
  let officialMetadataSupplementsThisRun = 0;
  let lastStarted = 0;
  const yearCounts = {};
  const warnings = {};
  const writeCheckpoint = async (status, current = null) => {
    const checkpoint = {
      schema_version: "ieee-browser-metadata-checkpoint-v1",
      status,
      venue_id: venueId,
      expected_total: items.length,
      completed_total: completed.size,
      completed_this_run: completedThisRun,
      included_this_run: includedThisRun,
      excluded_this_run: excludedThisRun,
      browser_items_this_run: browserItemsThisRun,
      listing_preclassified_exclusions_this_run: listingPreclassifiedThisRun,
      official_metadata_supplements_this_run: officialMetadataSupplementsThisRun,
      current_source_native_id: current,
      year_counts: yearCounts,
      warning_counts: warnings,
      output_path: resolvedOutput,
      output_sha256: await fileSha256(resolvedOutput).catch(() => null),
      exclusion_path: resolvedExclusions,
      exclusion_sha256: resolvedExclusions ? await fileSha256(resolvedExclusions).catch(() => null) : null,
      started_at: startedAt,
      updated_at: new Date().toISOString(),
      skipped_source_native_ids: [...skipIds].filter((identity) => !completed.has(identity)).sort(),
      skip_evidence_path: skipEvidencePath ? path.resolve(skipEvidencePath) : null,
    };
    await writeJson(resolvedCheckpoint, checkpoint);
    return checkpoint;
  };
  await writeCheckpoint("RUNNING");
  if (allowListingPreclassification) {
    if (!resolvedExclusions) throw new Error("listing preclassification requires an explicit exclusionPath");
    for (const expected of items) {
      const identity = String(expected.source_native_id || expected.source_item_id || expected.arnumber || "");
      if (completed.has(identity)) continue;
      const listingRecord = listingRecordForExpected(expected, {
        venueId,
        publicationNumber,
        publicationTitle,
        allowedHost,
        allowPerItemPublicationNumber,
      });
      const classification = exclusionReason(listingRecord, {singlePageEditorialAuthors});
      if (!classification) continue;
      await appendJsonLine(resolvedExclusions, toExclusionRecord(listingRecord, classification));
      completed.add(identity);
      completedThisRun += 1;
      excludedThisRun += 1;
      listingPreclassifiedThisRun += 1;
      yearCounts[String(listingRecord.year)] = (yearCounts[String(listingRecord.year)] || 0) + 1;
    }
    if (listingPreclassifiedThisRun) await writeCheckpoint("RUNNING");
  }
  for (const expected of items) {
    const identity = String(expected.source_native_id || expected.source_item_id || expected.arnumber || "");
    if (completed.has(identity)) continue;
    if (skipIds.has(identity)) continue;
    if (browserItemsThisRun >= maxItems) break;
    const waitForRate = Math.max(0, minStartIntervalMs - (Date.now() - lastStarted));
    if (waitForRate) await wait(waitForRate);
    const url = safeUrlForExpected(expected, allowedHost);
    let record = null;
    let finalError = null;
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      lastStarted = Date.now();
      try {
        if (attempt === 1) {
          const reachedUrl = reuseCurrentPage ? await currentTabUrl(tab) : null;
          if (!sameCanonicalIeeeDocumentUrl(reachedUrl, url)) await tab.goto(url);
        } else if (attempt === 2) {
          // IEEE Xplore occasionally leaves the previous document's inline
          // metadata in its single-page shell after an ordinary reload.  A
          // one-off cache-busting query forces a fresh navigation on the same
          // official article path.  The normalized record still stores the
          // canonical query-free landing URL, and exact native-ID validation
          // remains mandatory below.
          const retryUrl = new URL(url);
          retryUrl.searchParams.set("_litdb_refresh", String(Date.now()));
          await tab.goto(retryUrl.href);
        } else {
          // A short burst of IEEE document navigations can be rejected by the
          // client even though the same canonical page becomes available
          // after additional cool-downs.  Later bounded attempts return to
          // the exact official URL; it does not open another tab or change
          // sources, and all identity validation below still applies.
          await tab.goto(url);
        }
        record = await extractCurrentIeeeRecord(tab, {venueId});
        for (let patience = 0; patience < 5; patience += 1) {
          const staleIdentity = record && !record.error && String(record.source_native_id || "") !== identity;
          if (record?.error !== "METADATA_SCRIPT_MISSING" && !staleIdentity) break;
          await tab.playwright.waitForTimeout(3000);
          record = await extractCurrentIeeeRecord(tab, {venueId});
        }
        if (record?.error === "ACCESS_OR_CAPTCHA_BLOCK") {
          const blocked = new Error("ACCESS_OR_CAPTCHA_BLOCK");
          blocked.hardStop = true;
          throw blocked;
        }
        if (record?.error) throw new Error(record.error);
        const comparison = validateFreshRecord(record, expected, {
          venueId,
          publicationNumber,
          publicationTitle,
          allowPerItemPublicationNumber,
          publicationTitlePattern,
          earlyAccessIsNumbers: earlyAccessIsNumbers.map(String),
        });
        if (comparison.errors.length) throw new Error(comparison.errors.join(","));
        for (const warning of comparison.warnings) warnings[warning] = (warnings[warning] || 0) + 1;
        const supplementedFields = applyOfficialMetadataSupplement(
          record,
          expected,
          identity,
          officialMetadataSupplements?.[identity],
          allowedSupplementalHosts,
        );
        if (supplementedFields.length) {
          officialMetadataSupplementsThisRun += 1;
          warnings.official_metadata_supplemented = (warnings.official_metadata_supplemented || 0) + 1;
          for (const field of supplementedFields) {
            const warning = `${field}_surface_supplemented`;
            warnings[warning] = (warnings[warning] || 0) + 1;
          }
        }
        break;
      } catch (error) {
        finalError = error;
        record = null;
        if (error.hardStop || attempt === maxAttempts) break;
        // Publisher navigation can transiently surface a blank shell or a
        // client-side block after many consecutive document transitions.
        // Give the existing Browser session a real cool-down before the one
        // bounded retry instead of immediately churning another navigation.
        await tab.playwright.waitForTimeout(transientRetryCooldownMs);
      }
    }
    if (!record) {
      await appendJsonLine(resolvedErrors, {
        venue_id: venueId,
        source_native_id: identity,
        url,
        error: finalError?.message || "unknown Browser extraction failure",
        hard_stop: Boolean(finalError?.hardStop),
        observed_at: new Date().toISOString(),
      });
      await writeCheckpoint(finalError?.hardStop ? "BLOCKED" : "PARTIAL", identity);
      throw new Error(`Browser extraction stopped at ${identity}: ${finalError?.message || "unknown failure"}`);
    }
    const classification = exclusionReason(record, {singlePageEditorialAuthors});
    if (classification) {
      if (!resolvedExclusions) throw new Error(`clear non-research item ${identity} requires an explicit exclusionPath`);
      await appendJsonLine(resolvedExclusions, toExclusionRecord(record, classification));
      excludedThisRun += 1;
    } else if (!Array.isArray(record.authors) || record.authors.length === 0) {
      await appendJsonLine(resolvedErrors, {
        venue_id: venueId,
        source_native_id: identity,
        url,
        error: "AMBIGUOUS_AUTHORLESS_ITEM_REQUIRES_SCOPE_REVIEW",
        hard_stop: false,
        title: record.title,
        observed_at: new Date().toISOString(),
      });
      await writeCheckpoint("PARTIAL", identity);
      throw new Error(`scope review required for authorless item ${identity}`);
    } else {
      await appendJsonLine(resolvedOutput, record);
      includedThisRun += 1;
    }
    completed.add(identity);
    completedThisRun += 1;
    browserItemsThisRun += 1;
    yearCounts[String(record.year)] = (yearCounts[String(record.year)] || 0) + 1;
    if (browserItemsThisRun % batchSize === 0) await writeCheckpoint("RUNNING", identity);
  }
  const unresolvedSkipped = [...skipIds].filter((identity) => !completed.has(identity));
  const status = completed.size === items.length && unresolvedSkipped.length === 0 ? "PASS" : "CHECKPOINTED";
  const checkpoint = await writeCheckpoint(status);
  const summary = {
    schema_version: "ieee-browser-metadata-summary-v1",
    status,
    venue_id: venueId,
    expected_total: items.length,
    completed_total: completed.size,
    completed_this_run: completedThisRun,
    included_this_run: includedThisRun,
    excluded_this_run: excludedThisRun,
    browser_items_this_run: browserItemsThisRun,
    listing_preclassified_exclusions_this_run: listingPreclassifiedThisRun,
    official_metadata_supplements_this_run: officialMetadataSupplementsThisRun,
    remaining: items.length - completed.size,
    output_path: resolvedOutput,
    output_sha256: checkpoint.output_sha256,
    exclusion_path: resolvedExclusions,
    exclusion_sha256: checkpoint.exclusion_sha256,
    expected_root: path.resolve(expectedRoot),
    allow_per_item_publication_number: Boolean(allowPerItemPublicationNumber),
    publication_title_pattern: publicationTitlePattern,
    reuse_current_page: Boolean(reuseCurrentPage),
    transient_retry_cooldown_ms: transientRetryCooldownMs,
    min_start_interval_ms: minStartIntervalMs,
    early_access_isnumbers: earlyAccessIsNumbers.map(String),
    concurrency: 1,
    year_counts_this_run: yearCounts,
    warning_counts: warnings,
    skipped_source_native_ids: unresolvedSkipped.sort(),
    skip_evidence_path: skipEvidencePath ? path.resolve(skipEvidencePath) : null,
    started_at: startedAt,
    completed_at: new Date().toISOString(),
  };
  await writeJson(resolvedSummary, summary);
  return summary;
}
