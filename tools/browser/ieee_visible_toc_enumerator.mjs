import crypto from "node:crypto";


function normalizeText(value) {
  if (value == null) return null;
  return String(value).normalize("NFKC").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
}


function sha256Lines(values) {
  return crypto.createHash("sha256").update([...values].sort().join("\n")).digest("hex");
}


function canonicalTocUrl(rawUrl, pageNumber, rowsPerPage) {
  const url = new URL(rawUrl);
  if (url.protocol !== "https:" || url.hostname !== "ieeexplore.ieee.org") {
    throw new Error(`unapproved IEEE TOC URL: ${rawUrl}`);
  }
  if (url.pathname !== "/xpl/tocresult.jsp" || !url.searchParams.get("isnumber")) {
    throw new Error(`invalid IEEE TOC URL: ${rawUrl}`);
  }
  if (![25, 50].includes(Number(rowsPerPage))) {
    throw new Error(`unsupported or unverified IEEE rows-per-page contract: ${rowsPerPage}`);
  }
  // This query shape must first be observed through the corresponding visible
  // Items Per Page action. Callers opt in explicitly below.
  url.searchParams.set("sortType", "vol-only-seq");
  url.searchParams.set("rowsPerPage", String(rowsPerPage));
  url.searchParams.set("pageNumber", String(pageNumber));
  return url.href;
}


async function wait(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}


async function readVisibleTocPage(tab) {
  return await tab.playwright.evaluate(() => {
    const bodyText = document.body?.innerText || "";
    const hardStop = /captcha|verify you are human|access denied|automated access|request blocked|temporarily blocked/i.test(bodyText);
    const rangeMatch = bodyText.match(/Showing\s+(\d+)-(\d+)\s+of\s+(\d+)/i);
    const cards = [...document.querySelectorAll(".List-results-items")].map((card) => {
      const title = card.querySelector("h2")?.innerText?.trim() || null;
      const documentLink = [...card.querySelectorAll('a[href*="/document/"]')]
        .map((link) => link.getAttribute("href"))
        .find((href) => /\/document\/\d+\/?(?:[?#].*)?$/.test(href || ""));
      const stampLink = [...card.querySelectorAll('a[href*="stamp.jsp"][href*="arnumber="]')]
        .map((link) => link.getAttribute("href"))
        .find(Boolean);
      const documentMatch = (documentLink || "").match(/\/document\/(\d+)/);
      const stampMatch = (stampLink || "").match(/[?&]arnumber=(\d+)/);
      const sourceNativeId = documentMatch?.[1] || stampMatch?.[1] || null;
      const authors = [...card.querySelectorAll('a[href*="/author/"]')]
        .map((link) => link.textContent?.replace(/\s+/g, " ").trim())
        .filter(Boolean);
      const text = card.innerText || "";
      const publicationYear = Number(text.match(/Publication Year:\s*(\d{4})/i)?.[1] || 0) || null;
      const pages = text.match(/Page\(s\):\s*([^\n]+)/i)?.[1]?.trim() || null;
      return {
        source_native_id: sourceNativeId,
        title,
        authors,
        source_publication_year: publicationYear,
        pages,
        visible_document_link: documentLink || null,
        visible_pdf_link: stampLink || null,
      };
    });
    return {
      page_url: location.href,
      page_title: document.title,
      body_text_length: bodyText.length,
      hard_stop: hardStop,
      range: rangeMatch
        ? {start: Number(rangeMatch[1]), end: Number(rangeMatch[2]), total: Number(rangeMatch[3])}
        : null,
      cards,
    };
  }, undefined, {timeoutMs: 15000});
}


async function loadStableVisibleTocPage(tab, url, options) {
  await tab.goto(url);
  let result = null;
  for (let attempt = 1; attempt <= options.maxStabilityChecks; attempt += 1) {
    await wait(attempt === 1 ? options.initialWaitMs : options.retryWaitMs);
    result = await readVisibleTocPage(tab);
    if (result.hard_stop) throw new Error(`ACCESS_OR_CAPTCHA_BLOCK at ${result.page_url}`);
    const expectedPageCount = result.range ? result.range.end - result.range.start + 1 : null;
    const identities = result.cards.filter((card) => card.source_native_id);
    if (result.range && identities.length === expectedPageCount && result.cards.length === expectedPageCount) {
      return result;
    }
  }
  throw new Error(
    `VISIBLE_TOC_NOT_STABLE at ${url}: range=${JSON.stringify(result?.range)} cards=${result?.cards?.length || 0}`,
  );
}


export async function enumerateIeeeToc(tab, options) {
  const {
    venueId,
    issueUrl,
    enumerationYear,
    enumerationKind = "issue",
    requestId = null,
    verifiedRowsPerPageContract = false,
    verifiedRowsPerPage = null,
    initialWaitMs = 5000,
    retryWaitMs = 3000,
    maxStabilityChecks = 4,
    maxPages = 20,
  } = options;
  if (!tab) throw new Error("a controlled in-app Browser tab is required");
  if (!venueId || !issueUrl || !Number.isInteger(Number(enumerationYear))) {
    throw new Error("venueId, issueUrl, and enumerationYear are required");
  }
  const rowsPerPage = Number(
    verifiedRowsPerPage ?? (verifiedRowsPerPageContract === true ? 50 : verifiedRowsPerPageContract),
  );
  if (![25, 50].includes(rowsPerPage)) {
    throw new Error("visible Items Per Page -> 25 or 50 pagination contract was not explicitly verified");
  }
  if (!new Set(["issue", "early_access"]).has(enumerationKind)) {
    throw new Error(`unsupported enumeration kind: ${enumerationKind}`);
  }
  const base = canonicalTocUrl(issueUrl, 1, rowsPerPage);
  const sourceUnit = new URL(issueUrl);
  const isnumber = sourceUnit.searchParams.get("isnumber");
  const observedAt = new Date().toISOString();
  const pageEvidence = [];
  const rows = [];
  const first = await loadStableVisibleTocPage(tab, base, {
    initialWaitMs,
    retryWaitMs,
    maxStabilityChecks,
  });
  const totalPages = Math.ceil(first.range.total / rowsPerPage);
  if (totalPages > maxPages) throw new Error(`TOC pagination exceeds safety cap: ${totalPages} pages`);
  for (let pageNumber = 1; pageNumber <= totalPages; pageNumber += 1) {
    const page = pageNumber === 1
      ? first
      : await loadStableVisibleTocPage(tab, canonicalTocUrl(issueUrl, pageNumber, rowsPerPage), {
          initialWaitMs,
          retryWaitMs,
          maxStabilityChecks,
        });
    pageEvidence.push({
      page_number: pageNumber,
      page_url: page.page_url,
      range: page.range,
      visible_card_count: page.cards.length,
    });
    for (const card of page.cards) {
      const identity = String(card.source_native_id || "");
      if (!identity) throw new Error(`visible TOC card lacks native identity at ${page.page_url}`);
      const landingUrl = `https://ieeexplore.ieee.org/document/${identity}`;
      rows.push({
        schema_version: "literature-expected-source-item-v1",
        venue_id: venueId,
        source_native_id: identity,
        title: normalizeText(card.title),
        authors: card.authors.map((name) => ({name: normalizeText(name)})),
        year: Number(enumerationYear),
        source_publication_year: card.source_publication_year,
        pages: normalizeText(card.pages),
        landing_url: landingUrl,
        source_url: landingUrl,
        source_page_url: issueUrl,
        selected_unit_id: issueUrl,
        selected_unit_isnumber: String(isnumber),
        enumeration_kind: enumerationKind,
        source_document_type: enumerationKind === "early_access" ? "Early Access listing" : "Issue TOC listing",
        source_grade: "A",
        request_id: requestId,
        observed_at: observedAt,
        listing_evidence: {
          visible_document_link: card.visible_document_link,
          visible_pdf_link: card.visible_pdf_link,
          pagination_contract: `visible_items_per_page_${rowsPerPage}`,
        },
      });
    }
  }
  const seen = new Set();
  for (const row of rows) {
    if (seen.has(row.source_native_id)) throw new Error(`duplicate identity within TOC ${isnumber}: ${row.source_native_id}`);
    seen.add(row.source_native_id);
  }
  if (rows.length !== first.range.total) {
    throw new Error(`TOC total mismatch for ${isnumber}: visible=${first.range.total} extracted=${rows.length}`);
  }
  return {
    rows,
    evidence: {
      schema_version: "ieee-visible-toc-evidence-v1",
      venue_id: venueId,
      enumeration_kind: enumerationKind,
      enumeration_year: Number(enumerationYear),
      isnumber: String(isnumber),
      source_url: issueUrl,
      observed_at: observedAt,
      status: "PASS",
      enumeration_complete: true,
      visible_total: first.range.total,
      extracted_total: rows.length,
      rows_per_page: rowsPerPage,
      source_item_set_sha256: sha256Lines(seen),
      pages: pageEvidence,
    },
  };
}


export async function enumerateIeeeTocBatch(tab, units, options) {
  const results = [];
  let lastStarted = 0;
  for (const unit of units) {
    const waitForRate = Math.max(0, (options.minStartIntervalMs || 5000) - (Date.now() - lastStarted));
    if (waitForRate) await wait(waitForRate);
    lastStarted = Date.now();
    results.push(await enumerateIeeeToc(tab, {...options, ...unit}));
  }
  return results;
}
