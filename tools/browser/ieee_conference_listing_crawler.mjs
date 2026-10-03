import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";


function normalizeText(value) {
  if (value == null) return null;
  return String(value).normalize("NFKC").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim() || null;
}


function sha256Lines(values) {
  return crypto.createHash("sha256").update([...values].sort().join("\n")).digest("hex");
}


function sha256Text(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}


async function wait(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}


async function loadJson(pathname, fallback = null) {
  try {
    return JSON.parse(await fs.readFile(pathname, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return fallback;
    throw error;
  }
}


async function loadJsonl(pathname) {
  try {
    const data = await fs.readFile(pathname, "utf8");
    return data.split(/\r?\n/).filter((line) => line.trim()).map((line) => JSON.parse(line));
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}


async function atomicWrite(pathname, value) {
  await fs.mkdir(path.dirname(pathname), {recursive: true});
  const temporary = `${pathname}.${crypto.randomUUID()}.tmp`;
  await fs.writeFile(temporary, value, "utf8");
  await fs.rename(temporary, pathname);
}


async function writeJson(pathname, value) {
  await atomicWrite(pathname, `${JSON.stringify(value, null, 2)}\n`);
}


async function writeJsonl(pathname, rows) {
  await atomicWrite(pathname, rows.map((row) => JSON.stringify(row)).join("\n") + (rows.length ? "\n" : ""));
}


function validateCollectionUrl(rawUrl) {
  const url = new URL(rawUrl);
  if (url.protocol !== "https:" || url.hostname !== "ieeexplore.ieee.org") {
    throw new Error(`unapproved IEEE collection URL: ${rawUrl}`);
  }
  if (!/^\/xpl\/conhome\/\d+\/proceeding$/.test(url.pathname)) {
    throw new Error(`invalid IEEE collection URL: ${rawUrl}`);
  }
  return url;
}


function collectionId(rawUrl) {
  return validateCollectionUrl(rawUrl).pathname.match(/\/conhome\/(\d+)\/proceeding/)?.[1] || null;
}


function pageUrl(baseUrl, isnumber, pageNumber, rowsPerPage = 25) {
  const url = validateCollectionUrl(baseUrl);
  if (!/^\d+$/.test(String(isnumber || "")) || pageNumber < 2) {
    throw new Error(`invalid observed conference pagination contract: isnumber=${isnumber} page=${pageNumber}`);
  }
  if (![10, 25, 50, 75, 100].includes(rowsPerPage)) {
    throw new Error(`invalid observed rows-per-page contract: ${rowsPerPage}`);
  }
  url.searchParams.set("sortType", "vol-only-seq");
  url.searchParams.set("isnumber", String(isnumber));
  url.searchParams.set("rowsPerPage", String(rowsPerPage));
  url.searchParams.set("pageNumber", String(pageNumber));
  return url.href;
}


function classifyTitle(title, authors, venueId = "conference") {
  const value = (normalizeText(title) || "").toLocaleLowerCase();
  const authorCount = Array.isArray(authors) ? authors.length : 0;
  if (
    venueId === "iccd" &&
    /^(?:welcome )?message from (?:the )?(?:(?:general|program) chairs?|general chairs? and program chairs?)(?::?\s+iccd\s+\d{4})?$/u.test(value)
  ) {
    return {decision: "exclude", reason: "front_matter", rule: "iccd-front-matter-chair-message"};
  }
  // ICCD proceedings expose a handful of authorless section markers that do
  // not use the generic IEEE front-matter vocabulary (for example, "Technical
  // papers", title-page variants, reviewer lists, and chair messages).  Keep
  // these venue-specific labels out of the detail queue while retaining any
  // authored item with the same surface title for detail validation.
  if (venueId === "iccd" && authorCount === 0) {
    const iccdRules = [
      [/^technical papers$/u, "front_matter", "iccd-front-matter-technical-papers-section"],
      [/^tutorials$/u, "non_main_track", "iccd-non-main-track-tutorials"],
      [/^special panels?\b/u, "non_main_track", "iccd-non-main-track-special-panels"],
      [/^(?:iccd\s+\d{4}\s+)?poster session list$/u, "non_main_track", "iccd-non-main-track-poster-session-list"],
      [/^sub reviewers?$/u, "front_matter", "iccd-front-matter-sub-reviewers"],
      [/^(?:external )?reviewers?$/u, "front_matter", "iccd-front-matter-reviewers"],
      [/^\[?publisher'?s information\]?$/u, "front_matter", "iccd-front-matter-publisher-information"],
      [/^(?:organizing|program) committee(?:\s+iccd\s+\d{4})?$/u, "front_matter", "iccd-front-matter-committee"],
      [/^(?:title page|half title page|proceedings)$/u, "front_matter", "iccd-front-matter-title-page"],
      [/^\[?title page (?:i|ii|iii|iv)\]?$/u, "front_matter", "iccd-front-matter-title-page"],
      [/^\[?copy?pright notice\]?$/u, "copyright_form", "iccd-front-matter-copyright"],
      [/^technical program committee$/u, "front_matter", "iccd-front-matter-technical-program-committee"],
    ];
    for (const [pattern, reason, rule] of iccdRules) {
      if (pattern.test(value)) return {decision: "exclude", reason, rule};
    }
  }
  // ISCAS proceedings expose a larger set of authorless front-matter and
  // programme markers than most IEEE conferences.  Keep these visible
  // labels auditable and out of the research-detail queue, while preserving
  // an authored item unless its title is an explicit non-main-track surface
  // (for example, a live demonstration or tutorial).
  if (venueId === "iscas" && authorCount === 0) {
    const iscasRules = [
      // Pseudo-document navigation/sponsor leaves can appear in an IEEE
      // proceedings stream.  Keep them out of the detail queue with an
      // explicit, auditable front-matter classification.
      [/^menu$/u, "front_matter", "iscas-front-matter-navigation-menu"],
      [/^conference support\s*&\s*sponsors$/u, "front_matter", "iscas-front-matter-conference-support-sponsors"],
      [/^casfest$/u, "non_main_track", "iscas-non-main-track-casfest"],
      [/^tcas special$/u, "non_main_track", "iscas-non-main-track-tcas-special"],
      [/^technical program co-chairs message$/u, "front_matter", "iscas-front-matter-technical-program-co-chairs"],
      [/^iscas\s+\d{4}\s+about\s*&\s*welcome message$/u, "front_matter", "iscas-front-matter-about-welcome"],
      [/^iscas\s+\d{4}\s+conference patrons\s*&\s*sponsors$/u, "front_matter", "iscas-front-matter-patrons-sponsors"],
      [/^iscas\s+\d{4}\s+awards,\s*fellows\s*&\s*leadership$/u, "non_research_content", "iscas-non-research-awards-leadership"],
      [/^about iscas\s+\d{4}$/u, "front_matter", "iscas-front-matter-about-year"],
      [/^about the ieee circuits and systems society$/u, "front_matter", "iscas-front-matter-about-ieee-cas"],
      [/^welcome from the general chair\/co-chairs$/u, "front_matter", "iscas-front-matter-general-chair-message"],
      [/^welcome from the technical programme chair\/co-chairs$/u, "front_matter", "iscas-front-matter-technical-chair-message"],
      [/^conference patrons, supporters, and sponsors$/u, "front_matter", "iscas-front-matter-patrons-supporters-sponsors"],
      [/^paper types$/u, "front_matter", "iscas-front-matter-paper-types"],
      [/^a message from foodcas$/u, "non_main_track", "iscas-non-main-track-foodcas-message"],
      [/^social programme$/u, "non_main_track", "iscas-non-main-track-social-programme"],
      [/^iscas\s+\d{4}\s+virtual$/u, "non_main_track", "iscas-non-main-track-virtual-programme"],
      [/^track structure$/u, "front_matter", "iscas-front-matter-track-structure"],
      [/^welcome to iscas\s+\d{4}\s+from the general co-chairs$/u, "front_matter", "iscas-front-matter-general-co-chairs"],
      [/^conference sponsors and support$/u, "front_matter", "iscas-front-matter-sponsors-support"],
      [/^technical papers$/u, "front_matter", "iscas-front-matter-technical-papers"],
      [/^ieee awards$/u, "non_research_content", "iscas-non-research-ieee-awards"],
      [/^copyright information$/u, "copyright_form", "iscas-front-matter-copyright-information"],
      [/^author-index$/u, "index", "iscas-front-matter-author-index-hyphenated"],
      [/^iscas\s+\d{4}\s+front matter$/u, "front_matter", "iscas-front-matter-labeled"],
      [/^(?:iscas\s+\d{4}\s+)?(?:table of contents|contents|toc)$/u, "table_of_contents", "iscas-front-matter-table-of-contents"],
      [/^(?:iscas\s+\d{4}\s+)?author index$/u, "index", "iscas-front-matter-author-index"],
      [/^(?:iscas\s+\d{4}\s+)?(?:copyright(?: page| notice)?|content announcement page)$/u, "copyright_form", "iscas-front-matter-copyright-or-announcement"],
      [/^(?:iscas\s+\d{4}\s+)?(?:opinion|commentary|ad page|contributor page)$/u, "front_matter", "iscas-front-matter-editorial-programme-page"],
      [/^(?:iscas\s+\d{4}\s+)?(?:committees?|conference committee|organizing committee|technical program committee|review committee members?|track chairs?|tracks structure)$/u, "front_matter", "iscas-front-matter-committee-or-track"],
      [/^(?:iscas\s+\d{4}\s+)?(?:about iscas|about ieee circuits and systems society)$/u, "front_matter", "iscas-front-matter-about"],
      [/^(?:iscas\s+\d{4}\s+)?(?:welcome message.*|honorary chair message)$/u, "front_matter", "iscas-front-matter-welcome-or-chair-message"],
      [/^(?:iscas\s+\d{4}\s+)?(?:keynote speakers?|overview lectures?|tutorials?|mini-tutorials?|special sessions?|contests? and technical events|foodcas)$/u, "non_main_track", "iscas-non-main-track-programme-material"],
      [/^(?:session|technical session|special session)\b.*$/u, "non_main_track", "iscas-non-main-track-session-heading"],
    ];
    for (const [pattern, reason, rule] of iscasRules) {
      if (pattern.test(value)) return {decision: "exclude", reason, rule};
    }
  }
  if (
    venueId === "iscas" &&
    /^(?:live\s+)?demonstration\s*:/u.test(value)
  ) {
    return {decision: "exclude", reason: "non_main_track", rule: "iscas-non-main-track-live-demonstration"};
  }
  if (
    venueId === "iscas" &&
    /^(?:tutorial|mini-tutorial|keynote|overview lecture|special session|contest|foodcas)\b/u.test(value)
  ) {
    return {decision: "exclude", reason: "non_main_track", rule: "iscas-non-main-track-explicit-programme-item"};
  }
  const rules = [
    [/^(?:conference )?introduction$/u, "front_matter", "front-matter-introduction"],
    [/^\[?front matter\]?$/u, "front_matter", "front-matter-explicit-label"],
    [/^(?:general |program )?chairs?'? message$/u, "front_matter", "front-matter-chair-message"],
    [/^(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:committees?|executive committee|organizing committee|program committee|steering committee)$/u, "front_matter", "front-matter-committee"],
    [/^(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+).*(?:technical programme committee|technical program committee|technical programme topic chairs|technical program topic chairs|executive committee|organizing committee|steering committee)$/u, "front_matter", "front-matter-committee"],
    [/^(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:foreword|preface|welcome message)$/u, "front_matter", "front-matter-foreword"],
    [/^preface\s*:/u, "front_matter", "front-matter-preface"],
    [/^\d{4}\b.+\bdigest of technical papers$/u, "front_matter", "front-matter-proceedings-title-page"],
    [/^proceedings of the \d{4}\b.+\bconference\b/u, "front_matter", "front-matter-proceedings-title-page"],
    [/^proceedings of the \d{4}\b.*\b(?:design, automation|date)\b/u, "front_matter", "front-matter-proceedings-title-page"],
    [/^\d{4}\s+design,\s*automation\s*&\s*test\s+in\s+europe\b.*$/u, "front_matter", "front-matter-proceedings-title-page"],
    [/^(?:[a-z0-9&/.-]+\s+)*\d{4}\s+conference proceedings$/u, "front_matter", "front-matter-proceedings-title-page"],
    [/^\[?(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:title page|back matter)\]?$/u, "front_matter", "front-matter-title-or-back-page"],
    [/^sponsors?\s+and\s+organizers?$/u, "front_matter", "front-matter-sponsors"],
    [/^(?:corporate\s+)?sponsors?(?:\s+of\s+(?:the\s+)?technical program committee dinner)?$/u, "front_matter", "front-matter-sponsors"],
    [/^(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+).*(?:sponsor societies|sponsors? committee|sponsors and organizers)$/u, "front_matter", "front-matter-sponsors"],
    [/^awards?(?:\b|\s*\[)/u, "non_research_content", "non-research-awards"],
    [/^.*\b(?:best paper awards?|best paper award nominations)\b.*$/u, "non_research_content", "non-research-awards"],
    [/^(?:keynote|plenary)(?:\s+addresses?|\s+talks?|\s+speakers?|\b)/u, "non_research_content", "non-research-keynote"],
    [/^(?:panel|plenary panel|industrial panel)(?:\b|:)/u, "non_research_content", "non-research-panel"],
    [/^.*\b(?:opening keynotes?|lunchtime keynotes?|keynotes?)\b.*$/u, "non_research_content", "non-research-keynote"],
    [/^.*\b(?:special session summary|special session paper)\b.*$/u, "non_main_track", "non-main-track-special-session"],
    [/^(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:table of contents|contents|toc)$/u, "table_of_contents", "front-matter-table-of-contents"],
    [/^(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:author|subject) index$/u, "index", "front-matter-index"],
    [/^\[?(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:ieee\s+)?copyright(?: notice| page| form)?\]?$/u, "copyright_form", "front-matter-copyright"],
    [/^\[?(?:(?:[a-z0-9&/.-]+\s+)*\d{4}\s+)?(?:(?:front|back)\s+)?cover(?:\s+page)?\]?$/u, "front_matter", "front-matter-cover"],
    [/^copyright and reprint permissions$/u, "copyright_form", "front-matter-copyright"],
    [/^(?:workshop|tutorial|doctoral consortium|student research competition|src|cad contest|cadathlon)(?:\b|:)/u, "non_main_track", "non-main-track-labeled-item"],
    [/^(?:poster|demo)(?:\b|:)/u, "non_main_track", "non-main-track-poster-demo"],
    [/^(?:special session|invited session|focus session|focus sessions|phd forum|media partners?|special day|call for papers|about date|initiative on)(?:\b|:)/u, "non_main_track", "non-main-track-labeled-item"],
    [/^.*\b(?:focus sessions?|phd forum|media partners?|special day|call for papers|about date|initiative on|tutorial|invited paper|design session)\b.*$/u, "non_main_track", "non-main-track-labeled-item"],
    [/^.*\bintroduction to special session\b.*$/u, "non_main_track", "non-main-track-special-session"],
  ];
  for (const [pattern, reason, rule] of rules) {
    if (pattern.test(value)) return {decision: "exclude", reason, rule};
  }
  if (!value && authorCount === 0) {
    return {decision: "exclude", reason: "front_matter", rule: "authorless-untitled-front-matter"};
  }
  return {decision: "include", reason: null, rule: `official-ieee-${venueId}-proceedings-research-candidate`};
}


async function readVisiblePage(tab) {
  return await tab.playwright.evaluate(() => {
    const bodyText = document.body?.innerText || "";
    // IEEE occasionally formats four-digit totals with a thousands
    // separator (for example, "Showing 1-25 of 1,018").  Keep the
    // visible pagination contract intact while normalizing the number for
    // arithmetic and termination checks.
    const rangeMatch = bodyText.match(/Showing\s+(\d[\d,]*)-(\d[\d,]*)\s+of\s+(\d[\d,]*)/i);
    const shellOnly = /Getting results\.\.\./i.test(bodyText) && !rangeMatch;
    const hardStop = /captcha|verify you are human|access denied|automated access|request blocked|temporarily blocked/i.test(bodyText);
    const cards = [...document.querySelectorAll(".List-results-items")].map((card) => {
      const title = card.querySelector("h2")?.innerText?.trim() || null;
      const documentLinks = [...card.querySelectorAll('a[href*="/document/"]')]
        .map((link) => link.getAttribute("href"))
        .filter(Boolean);
      const documentLink = documentLinks.find((href) => /\/document\/\d+\/?(?:[?#].*)?$/.test(href)) || null;
      const pdfLink = [...card.querySelectorAll('a[href*="stamp.jsp"][href*="arnumber="]')]
        .map((link) => link.getAttribute("href"))
        .find(Boolean) || null;
      const identity = documentLink?.match(/\/document\/(\d+)/)?.[1]
        || pdfLink?.match(/[?&]arnumber=(\d+)/)?.[1]
        || null;
      const authors = [...card.querySelectorAll('a[href*="/author/"]')]
        .map((link) => link.textContent?.replace(/\s+/g, " ").trim())
        .filter(Boolean);
      // The proceedings result card exposes a short abstract excerpt behind
      // its visible "Abstract" disclosure control.  The controller expands
      // that control before calling readVisiblePage, so this value is an
      // auditable visible listing excerpt (not a guessed or third-party
      // abstract).  Keep the truncation marker when IEEE supplies one.
      const abstractExcerpt = card.querySelector(
        ".twist-container.stats-SearchResults_DocResult_ViewMore:not(.hide) span",
      )?.textContent?.replace(/\s+/g, " ").trim() || null;
      const text = card.innerText || "";
      return {
        source_native_id: identity,
        title,
        authors,
        abstract_excerpt: abstractExcerpt,
        source_publication_year: Number(text.match(/Publication Year:\s*(\d{4})/i)?.[1] || 0) || null,
        pages: text.match(/Page\(s\):\s*([^\n]+)/i)?.[1]?.trim() || null,
        visible_document_link: documentLink,
        visible_pdf_link: pdfLink,
      };
    });
    return {
      page_url: location.href,
      page_title: document.title,
      body_text_length: bodyText.length,
      shell_only: shellOnly,
      hard_stop: hardStop,
      range: rangeMatch ? {
        start: Number(rangeMatch[1].replace(/,/g, "")),
        end: Number(rangeMatch[2].replace(/,/g, "")),
        total: Number(rangeMatch[3].replace(/,/g, "")),
      } : null,
      cards,
    };
  }, undefined, {timeoutMs: 15000});
}


async function stableCurrentPage(tab, waits = [5000, 8000, 10000]) {
  let page = null;
  for (const milliseconds of waits) {
    await wait(milliseconds);
    page = await readVisiblePage(tab);
    if (page.hard_stop) throw new Error(`ACCESS_OR_CAPTCHA_BLOCK at ${page.page_url}`);
    const expected = page.range ? page.range.end - page.range.start + 1 : null;
    if (page.range && page.cards.length === expected && page.cards.every((card) => card.source_native_id)) return page;
  }
  throw new Error(
    `VISIBLE_CONFERENCE_LISTING_NOT_STABLE at ${page?.page_url}: range=${JSON.stringify(page?.range)} cards=${page?.cards?.length || 0} shell=${page?.shell_only}`,
  );
}


async function expandListingAbstracts(tab) {
  const buttons = await tab.playwright.locator("button.stats_Abstract_ShowMore").all();
  // One visible disclosure per page is enough to verify that the card's
  // abstract surface is the live IEEE control.  The remaining cards expose
  // the same short excerpt in their rendered card DOM; reading those values
  // avoids 100 separate UI round trips on a 100-row page while keeping the
  // provenance explicitly at listing-excerpt (not full-detail) level.
  for (const button of buttons.slice(0, 1)) {
    try {
      await button.click({timeoutMs: 3000});
    } catch {
      // A single detached/disabled disclosure must not invalidate the page;
      // readVisiblePage will record a missing excerpt for that card and the
      // metadata stage can queue it for detail repair.
    }
  }
}


async function navigateStable(tab, url, waits) {
  await tab.goto(url);
  const page = await stableCurrentPage(tab, waits);
  await expandListingAbstracts(tab);
  return await readVisiblePage(tab);
}


function rowsFromPage(page, unit, isnumber, requestId, controllerThreadId) {
  const sourcePageUrl = page.page_url;
  const observedAt = new Date().toISOString();
  return page.cards.map((card) => {
    const authors = card.authors.map((name) => ({name: normalizeText(name)}));
    const classification = classifyTitle(card.title, authors, unit.venue_id);
    const identity = String(card.source_native_id);
    return {
      schema_version: "ieee-conference-listing-v1",
      venue_id: unit.venue_id,
      year: Number(unit.year),
      source_native_id: identity,
      source_item_id: identity,
      arnumber: identity,
      title: normalizeText(card.title),
      authors,
      abstract_excerpt: normalizeText(card.abstract_excerpt),
      pages: normalizeText(card.pages),
      source_publication_year: card.source_publication_year,
      collection_id: collectionId(unit.proceedings_url),
      isnumber: String(isnumber),
      source_url: `https://ieeexplore.ieee.org/document/${identity}`,
      landing_url: `https://ieeexplore.ieee.org/document/${identity}`,
      source_page_url: sourcePageUrl,
      source_grade: "A",
      document_type: classification.decision === "include" ? "conference-publication" : classification.rule,
      include_decision: classification.decision,
      inclusion_rule_id: classification.decision === "include" ? classification.rule : null,
      exclusion_reason: classification.reason,
      exclusion_rule_id: classification.decision === "exclude" ? classification.rule : null,
      classification_status: "controller_visible_listing_rule_applied",
      request_id: requestId,
      controller_thread_id: controllerThreadId,
      observed_at: observedAt,
      listing_evidence: {
        event_url: unit.event_url,
        proceedings_url: unit.proceedings_url,
        visible_document_link: card.visible_document_link,
        visible_pdf_link: card.visible_pdf_link,
        visible_abstract_excerpt: normalizeText(card.abstract_excerpt),
        abstract_surface: "visible_ieee_listing_card_excerpt",
        visible_range: page.range,
        pagination_contract: "visible_default_25_and_next_button_observed",
      },
      catalog_ready: false,
      catalog_write: false,
    };
  });
}


async function savePage(root, page, unit, pageNumber, isnumber, requestId, controllerThreadId, allRows, rowsPerPage) {
  const rows = rowsFromPage(page, unit, isnumber, requestId, controllerThreadId);
  const existing = new Map(allRows.map((row) => [String(row.source_native_id), row]));
  for (const row of rows) {
    const current = existing.get(row.source_native_id);
    if (current && current.year !== row.year) throw new Error(`cross-year duplicate IEEE identity: ${row.source_native_id}`);
    existing.set(row.source_native_id, row);
  }
  allRows.splice(0, allRows.length, ...existing.values());
  await writeJsonl(path.join(root, "count_observed_manifest.jsonl"), allRows);
  const evidence = {
    schema_version: "ieee-conference-listing-page-evidence-v1",
    status: "PASS",
    venue_id: unit.venue_id,
    year: Number(unit.year),
    page_number: pageNumber,
    source_page_url: page.page_url,
    base_page_url: unit.proceedings_url,
    collection_id: collectionId(unit.proceedings_url),
    isnumber: String(isnumber),
    rows_per_page: rowsPerPage,
    visible_range: page.range,
    visible_card_count: page.cards.length,
    source_native_ids: rows.map((row) => row.source_native_id),
    source_item_set_sha256: sha256Lines(rows.map((row) => row.source_native_id)),
    page_title: page.page_title,
    body_text_length: page.body_text_length,
    observed_at: new Date().toISOString(),
    request_id: requestId,
    controller_thread_id: controllerThreadId,
    catalog_ready: false,
    catalog_write: false,
  };
  const evidencePath = path.join(root, "listing_browser_evidence", `${unit.year}-page-${String(pageNumber).padStart(3, "0")}.json`);
  await writeJson(evidencePath, evidence);
  return {rows, evidencePath};
}


export async function runIeeeConferenceListingBatch(tab, options) {
  const {
    unitsPath,
    outputRoot,
    venueId,
    requestId,
    controllerThreadId,
    maxPagesPerBatch = 3,
    stabilityWaitsMs = [5000, 8000, 10000],
    rowsPerPage = 25,
  } = options;
  if (!tab) throw new Error("a controlled in-app Browser tab is required");
  if (!venueId || !requestId || !controllerThreadId) throw new Error("venueId, requestId, and controllerThreadId are required");
  if (!Number.isInteger(maxPagesPerBatch) || maxPagesPerBatch < 2 || maxPagesPerBatch > 6) {
    throw new Error("maxPagesPerBatch must be between 2 and 6");
  }
  if (![10, 25, 50, 75, 100].includes(rowsPerPage)) {
    throw new Error("rowsPerPage must be one of 10, 25, 50, 75, or 100");
  }
  const root = path.resolve(outputRoot);
  const rawUnits = await loadJsonl(path.resolve(unitsPath));
  const units = rawUnits.map((unit) => ({...unit, venue_id: venueId})).sort((left, right) => left.year - right.year);
  if (!units.length) throw new Error("conference collection unit file is empty");
  for (const unit of units) validateCollectionUrl(unit.proceedings_url);

  const checkpointPath = path.join(root, "listing_checkpoint.json");
  const manifestPath = path.join(root, "count_observed_manifest.jsonl");
  const allRows = await loadJsonl(manifestPath);
  const checkpoint = await loadJson(checkpointPath, {
    schema_version: "ieee-conference-listing-checkpoint-v1",
    status: "IN_PROGRESS",
    venue_id: venueId,
    request_id: requestId,
    controller_thread_id: controllerThreadId,
    unit_index: 0,
    next_page: 1,
    current_isnumber: null,
    rows_per_page: rowsPerPage,
    units: [],
    created_at: new Date().toISOString(),
  });
  if (checkpoint.status === "PASS") return {status: "PASS", checkpoint, completed: true};
  if (checkpoint.rows_per_page != null && checkpoint.rows_per_page !== rowsPerPage) {
    throw new Error(`checkpoint rows-per-page drift: checkpoint=${checkpoint.rows_per_page} requested=${rowsPerPage}`);
  }
  checkpoint.rows_per_page = rowsPerPage;

  let processedPages = 0;
  while (checkpoint.unit_index < units.length && processedPages < maxPagesPerBatch) {
    const unit = units[checkpoint.unit_index];
    let unitState = checkpoint.units.find((item) => item.year === unit.year);
    if (!unitState) {
      unitState = {
        year: unit.year,
        proceedings_url: unit.proceedings_url,
        collection_id: collectionId(unit.proceedings_url),
        isnumber: null,
        visible_total: null,
        total_pages: null,
        rows_per_page: rowsPerPage,
        completed_pages: 0,
        status: "IN_PROGRESS",
      };
      checkpoint.units.push(unitState);
    }

    if (checkpoint.next_page === 1) {
      let first = await navigateStable(tab, unit.proceedings_url, stabilityWaitsMs);
      if (rowsPerPage !== 25) {
        const itemsPerPage = tab.playwright.getByRole("button", {name: "Items Per Page", exact: true});
        if (await itemsPerPage.count() !== 1) {
          throw new Error(`visible Items Per Page control is missing or ambiguous for ${unit.year}`);
        }
        await itemsPerPage.click();
        const option = tab.playwright.getByRole("button", {name: String(rowsPerPage), exact: true});
        if (await option.count() !== 1) {
          throw new Error(`visible ${rowsPerPage}-items option is missing or ambiguous for ${unit.year}`);
        }
        await option.click();
        first = await stableCurrentPage(tab, stabilityWaitsMs);
        await expandListingAbstracts(tab);
        first = await readVisiblePage(tab);
        const observedFirstUrl = new URL(first.page_url);
        if (observedFirstUrl.searchParams.get("rowsPerPage") !== String(rowsPerPage)) {
          throw new Error(`Items Per Page did not expose the expected ${rowsPerPage}-row contract for ${unit.year}: ${first.page_url}`);
        }
      }
      const totalPages = Math.ceil(first.range.total / rowsPerPage);
      if (totalPages < 1 || totalPages > 30) throw new Error(`unreasonable page count for ${unit.year}: ${totalPages}`);
      unitState.visible_total = first.range.total;
      unitState.total_pages = totalPages;
      if (totalPages === 1) {
        // Some official IEEE proceedings are genuinely smaller than the
        // selected page size.  The first page still exposes the collection's
        // isnumber after the visible Items Per Page control is applied; save
        // it as a complete one-page unit instead of treating it as an empty or
        // unverified source.
        const observedUrl = new URL(first.page_url);
        const isnumber = observedUrl.searchParams.get("isnumber");
        if (!/^\d+$/.test(String(isnumber || "")) || observedUrl.searchParams.get("pageNumber") !== "1") {
          throw new Error(`single-page collection lacks a verified isnumber/page-1 contract for ${unit.year}: ${first.page_url}`);
        }
        unitState.isnumber = String(isnumber);
        unitState.completed_pages = 1;
        unitState.status = "PASS";
        unitState.completed_at = new Date().toISOString();
        await savePage(root, first, unit, 1, isnumber, requestId, controllerThreadId, allRows, rowsPerPage);
        checkpoint.unit_index += 1;
        checkpoint.next_page = 1;
        checkpoint.current_isnumber = null;
        checkpoint.updated_at = new Date().toISOString();
        await writeJson(checkpointPath, checkpoint);
        processedPages += 1;
        continue;
      }
      const next = tab.playwright.getByRole("button", {name: /Next page of search results/i}).first();
      const nextCount = await tab.playwright.getByRole("button", {name: /Next page of search results/i}).count();
      if (nextCount !== 1) throw new Error(`visible Next button is missing or ambiguous for ${unit.year}`);
      await next.click();
      const secondStable = await stableCurrentPage(tab, stabilityWaitsMs);
      await expandListingAbstracts(tab);
      const second = await readVisiblePage(tab);
      const observedUrl = new URL(second.page_url);
      const isnumber = observedUrl.searchParams.get("isnumber");
      if (!/^\d+$/.test(String(isnumber || ""))
        || observedUrl.searchParams.get("pageNumber") !== "2"
        || observedUrl.searchParams.get("rowsPerPage") !== String(rowsPerPage)) {
        throw new Error(`Next button did not expose the expected page-2 contract for ${unit.year}: ${second.page_url}`);
      }
      unitState.isnumber = String(isnumber);
      checkpoint.current_isnumber = String(isnumber);
      await savePage(root, first, unit, 1, isnumber, requestId, controllerThreadId, allRows, rowsPerPage);
      await savePage(root, second, unit, 2, isnumber, requestId, controllerThreadId, allRows, rowsPerPage);
      unitState.completed_pages = 2;
      checkpoint.next_page = 3;
      processedPages += 2;
      checkpoint.updated_at = new Date().toISOString();
      await writeJson(checkpointPath, checkpoint);
      continue;
    }

    const pageNumber = checkpoint.next_page;
    const isnumber = checkpoint.current_isnumber || unitState.isnumber;
    const page = await navigateStable(tab, pageUrl(unit.proceedings_url, isnumber, pageNumber, rowsPerPage), stabilityWaitsMs);
    const expectedStart = (pageNumber - 1) * rowsPerPage + 1;
    if (page.range.start !== expectedStart || page.range.total !== unitState.visible_total) {
      throw new Error(`visible range drift for ${unit.year} page ${pageNumber}: ${JSON.stringify(page.range)}`);
    }
    await savePage(root, page, unit, pageNumber, isnumber, requestId, controllerThreadId, allRows, rowsPerPage);
    unitState.completed_pages = pageNumber;
    checkpoint.next_page = pageNumber + 1;
    processedPages += 1;
    if (pageNumber === unitState.total_pages) {
      unitState.status = "PASS";
      unitState.completed_at = new Date().toISOString();
      checkpoint.unit_index += 1;
      checkpoint.next_page = 1;
      checkpoint.current_isnumber = null;
    }
    checkpoint.updated_at = new Date().toISOString();
    await writeJson(checkpointPath, checkpoint);
  }

  if (checkpoint.unit_index === units.length) {
    const identities = allRows.map((row) => String(row.source_native_id));
    if (new Set(identities).size !== identities.length) throw new Error("duplicate identities remain after conference enumeration");
    const byYear = Object.fromEntries(units.map((unit) => [
      String(unit.year),
      {
        total: allRows.filter((row) => row.year === unit.year).length,
        included: allRows.filter((row) => row.year === unit.year && row.include_decision === "include").length,
        excluded: allRows.filter((row) => row.year === unit.year && row.include_decision === "exclude").length,
      },
    ]));
    const reasonCounts = {};
    for (const row of allRows.filter((item) => item.include_decision === "exclude")) {
      reasonCounts[row.exclusion_reason] = (reasonCounts[row.exclusion_reason] || 0) + 1;
    }
    const summary = {
      schema_version: "ieee-conference-listing-summary-v1",
      status: "PASS",
      venue_id: venueId,
      request_id: requestId,
      controller_thread_id: controllerThreadId,
      enumeration_complete: true,
      collection_count: units.length,
      rows_per_page: rowsPerPage,
      source_item_count: allRows.length,
      included_candidate_count: allRows.filter((row) => row.include_decision === "include").length,
      excluded_count: allRows.filter((row) => row.include_decision === "exclude").length,
      exclusion_reason_counts: reasonCounts,
      by_year: byYear,
      source_item_set_sha256: sha256Lines(identities),
      manifest_sha256: sha256Text(await fs.readFile(manifestPath, "utf8")),
      observed_at: new Date().toISOString(),
      catalog_ready: false,
      catalog_write: false,
    };
    checkpoint.status = "PASS";
    checkpoint.completed_at = summary.observed_at;
    checkpoint.source_item_count = summary.source_item_count;
    checkpoint.source_item_set_sha256 = summary.source_item_set_sha256;
    await writeJson(checkpointPath, checkpoint);
    await writeJson(path.join(root, "listing_summary.json"), summary);
    return {status: "PASS", checkpoint, summary, processedPages, completed: true};
  }

  return {
    status: "IN_PROGRESS",
    checkpoint,
    processedPages,
    completed: false,
    current_year: units[checkpoint.unit_index]?.year || null,
    next_page: checkpoint.next_page,
    source_item_count: allRows.length,
  };
}


export async function reclassifyIeeeConferenceListing(options) {
  const {
    outputRoot,
    requestId,
    controllerThreadId,
    auditFilename = "listing_classification_repair.json",
    classificationRevision = "conference-front-matter-v3",
  } = options || {};
  if (!outputRoot || !requestId || !controllerThreadId) {
    throw new Error("outputRoot, requestId, and controllerThreadId are required");
  }
  const root = path.resolve(outputRoot);
  const manifestPath = path.join(root, "count_observed_manifest.jsonl");
  const summaryPath = path.join(root, "listing_summary.json");
  const checkpointPath = path.join(root, "listing_checkpoint.json");
  const rows = await loadJsonl(manifestPath);
  const previousSummary = await loadJson(summaryPath);
  const checkpoint = await loadJson(checkpointPath);
  if (!rows.length || previousSummary?.status !== "PASS" || checkpoint?.status !== "PASS") {
    throw new Error("only a completed listing can be reclassified without refetching");
  }
  const oldManifestText = await fs.readFile(manifestPath, "utf8");
  const changed = [];
  for (const row of rows) {
    const before = {
      include_decision: row.include_decision,
      inclusion_rule_id: row.inclusion_rule_id,
      exclusion_reason: row.exclusion_reason,
      exclusion_rule_id: row.exclusion_rule_id,
      document_type: row.document_type,
    };
    const classification = classifyTitle(row.title, row.authors, row.venue_id);
    row.include_decision = classification.decision;
    row.inclusion_rule_id = classification.decision === "include" ? classification.rule : null;
    row.exclusion_reason = classification.reason;
    row.exclusion_rule_id = classification.decision === "exclude" ? classification.rule : null;
    row.document_type = classification.decision === "include" ? "conference-publication" : classification.rule;
    const after = {
      include_decision: row.include_decision,
      inclusion_rule_id: row.inclusion_rule_id,
      exclusion_reason: row.exclusion_reason,
      exclusion_rule_id: row.exclusion_rule_id,
      document_type: row.document_type,
    };
    if (JSON.stringify(before) !== JSON.stringify(after)) {
      row.classification_status = "controller_listing_rule_revalidated_without_refetch";
      row.classification_repair = {
        revision: classificationRevision,
        request_id: requestId,
        controller_thread_id: controllerThreadId,
        repaired_at: new Date().toISOString(),
      };
      changed.push({source_native_id: String(row.source_native_id), year: row.year, title: row.title, before, after});
    }
  }
  await writeJsonl(manifestPath, rows);
  const manifestText = await fs.readFile(manifestPath, "utf8");
  const years = [...new Set(rows.map((row) => Number(row.year)))].sort((left, right) => left - right);
  const excludedRows = rows.filter((row) => row.include_decision === "exclude");
  const reasonCounts = {};
  for (const row of excludedRows) reasonCounts[row.exclusion_reason] = (reasonCounts[row.exclusion_reason] || 0) + 1;
  const reclassifiedAt = new Date().toISOString();
  const summary = {
    ...previousSummary,
    source_item_count: rows.length,
    included_candidate_count: rows.length - excludedRows.length,
    excluded_count: excludedRows.length,
    exclusion_reason_counts: reasonCounts,
    by_year: Object.fromEntries(years.map((year) => [String(year), {
      total: rows.filter((row) => Number(row.year) === year).length,
      included: rows.filter((row) => Number(row.year) === year && row.include_decision === "include").length,
      excluded: rows.filter((row) => Number(row.year) === year && row.include_decision === "exclude").length,
    }])),
    source_item_set_sha256: sha256Lines(rows.map((row) => String(row.source_native_id))),
    manifest_sha256: sha256Text(manifestText),
    classification_revision: classificationRevision,
    reclassified_at: reclassifiedAt,
  };
  checkpoint.source_item_count = rows.length;
  checkpoint.source_item_set_sha256 = summary.source_item_set_sha256;
  checkpoint.classification_revision = summary.classification_revision;
  checkpoint.reclassified_at = reclassifiedAt;
  const audit = {
    schema_version: "ieee-conference-listing-classification-repair-v1",
    status: "PASS",
    request_id: requestId,
    controller_thread_id: controllerThreadId,
    input_browser_enumeration_reused: true,
    browser_refetch_performed: false,
    source_identity_set_unchanged: summary.source_item_set_sha256 === previousSummary.source_item_set_sha256,
    old_manifest_sha256: sha256Text(oldManifestText),
    new_manifest_sha256: summary.manifest_sha256,
    changed_count: changed.length,
    changed,
    reclassified_at: reclassifiedAt,
  };
  await writeJson(summaryPath, summary);
  await writeJson(checkpointPath, checkpoint);
  await writeJson(path.join(root, auditFilename), audit);
  return {summary, audit};
}
