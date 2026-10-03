import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";


export const ACM_HOST = "dl.acm.org";
export const ACM_ORIGIN = `https://${ACM_HOST}`;
export const ACM_TOC_SCHEMA_VERSION = "acm-visible-metadata-row-v1";

const JOURNAL_CODE = "[A-Za-z0-9][A-Za-z0-9._-]*";
const TOC_SEGMENT = "[A-Za-z0-9][A-Za-z0-9._-]*";
const ISSUE_PATH = new RegExp(`^/toc/(${JOURNAL_CODE})/(\\d{4})/(${TOC_SEGMENT})/(${TOC_SEGMENT})/?$`);
const JUST_ACCEPTED_PATH = new RegExp(`^/toc/(${JOURNAL_CODE})/justaccepted/?$`, "i");
const DOI_PATTERN = /10\.\d{4,9}\/[\w.!#$%&'()*+,/:;=?@\[\]~-]+/i;
const HARD_STOP_PATTERN = /captcha|verify\s+you\s+are\s+human|access\s+denied|forbidden|automated\s+access|request\s+blocked|temporarily\s+blocked|unusual\s+traffic|robot\s+check/i;
const LOADING_PATTERN = /^(?:loading|please\s+wait|just\s+a\s+moment)\W*$/i;
const MONTHS = Object.freeze([
  "january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december",
]);


export function normalizeText(value) {
  if (value == null) return null;
  const normalized = String(value)
    .normalize("NFKC")
    .replace(/\u00a0/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return normalized || null;
}


function redactAcmUrlForLog(rawUrl) {
  if (rawUrl == null) return null;
  try {
    const url = rawUrl instanceof URL ? rawUrl : new URL(String(rawUrl));
    const base = `${url.protocol}//${url.host}${url.pathname}`;
    return `${base}${url.search ? "?[redacted]" : ""}${url.hash ? "#[redacted]" : ""}`;
  } catch {
    return String(rawUrl).replace(/\?[^\s"'<>#]*/g, "?[redacted]").replace(/#[^\s"'<>]*/g, "#[redacted]");
  }
}


function redactAcmErrorText(value) {
  return String(value).replace(/https:\/\/dl\.acm\.org\/[^\s"'<>]+/gi, (match) => redactAcmUrlForLog(match));
}


function serializeError(error) {
  if (!error) return null;
  return {
    name: error.name || "Error",
    message: redactAcmErrorText(error.message || String(error)),
  };
}


function assertAllowedOrigin(url) {
  if (url.protocol !== "https:" || url.hostname !== ACM_HOST || url.port || url.username || url.password) {
    throw new Error(`unapproved ACM URL: ${redactAcmUrlForLog(url)}`);
  }
  if (url.search || url.hash) throw new Error(`ACM URL must not contain query or fragment: ${redactAcmUrlForLog(url)}`);
}


export function parseAcmTocUrl(rawUrl) {
  if (typeof rawUrl !== "string" || !rawUrl.trim()) throw new Error("ACM TOC URL is required");
  let url;
  try {
    url = new URL(rawUrl);
  } catch (error) {
    throw new Error(`invalid ACM TOC URL: ${error.message}`);
  }
  assertAllowedOrigin(url);
  const issueMatch = url.pathname.match(ISSUE_PATH);
  if (issueMatch) {
    const [, journalCode, year, volume, issue] = issueMatch;
    return {
      kind: "issue",
      journalCode,
      year: Number(year),
      volume,
      issue,
      url: `${ACM_ORIGIN}${url.pathname.replace(/\/$/, "")}`,
    };
  }
  const justAcceptedMatch = url.pathname.match(JUST_ACCEPTED_PATH);
  if (justAcceptedMatch) {
    return {
      kind: "justaccepted",
      journalCode: justAcceptedMatch[1],
      year: null,
      volume: null,
      issue: null,
      url: `${ACM_ORIGIN}${url.pathname.replace(/\/$/, "")}`,
    };
  }
  throw new Error(`ACM TOC URL is outside the allowlist: ${rawUrl}`);
}


export function canonicalAcmTocUrl(rawUrl) {
  return parseAcmTocUrl(rawUrl).url;
}


export function normalizeDoi(value) {
  let text = normalizeText(value);
  if (!text) return null;
  text = text
    .replace(/^doi\s*:\s*/i, "")
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
    .replace(/^https?:\/\/dl\.acm\.org\/doi\//i, "")
    .replace(/#.*$/g, "")
    .replace(/[.,;:#]+$/g, "")
    .trim()
    .toLowerCase();
  const match = text.match(DOI_PATTERN);
  return match ? match[0].replace(/[.,;:#]+$/g, "") : null;
}


export function canonicalAcmLandingUrl(doi) {
  const normalized = normalizeDoi(doi);
  return normalized ? `${ACM_ORIGIN}/doi/${normalized}` : null;
}


function canonicalAcmResourceUrl(rawUrl, baseUrl = ACM_ORIGIN) {
  if (!rawUrl) return null;
  let url;
  try {
    url = new URL(String(rawUrl), baseUrl);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.hostname !== ACM_HOST || url.username || url.password) return null;
  url.search = "";
  url.hash = "";
  return url.href.replace(/\/$/, "") || ACM_ORIGIN;
}


export function classifyAcmDocumentType(value) {
  const text = normalizeText(value);
  if (!text) return null;
  const lower = text.toLocaleLowerCase();
  const known = [
    [/(?:research|original)(?:\s*-\s*|\s+)article/, "Research Article"],
    [/review\s+article|survey/, "Review Article"],
    [/short\s+(?:paper|article|communication)/, "Short Paper"],
    [/^note$|technical\s+note/, "Technical Note"],
    [/tutorial/, "Tutorial"],
    [/keynote/, "Keynote"],
    [/technical\s+(?:paper|article)/, "Technical Article"],
    [/data\s+article/, "Data Article"],
    [/case\s+study/, "Case Study"],
    [/editorial/, "Editorial"],
    [/correction|erratum|corrigendum/, "Correction"],
    [/letter/, "Letter"],
  ];
  for (const [pattern, label] of known) if (pattern.test(lower)) return label;
  return text;
}


function firstNonEmpty(...values) {
  for (const value of values) {
    const normalized = normalizeText(value);
    if (normalized) return normalized;
  }
  return null;
}


function normalizeAuthors(value) {
  const values = Array.isArray(value) ? value : value == null ? [] : [value];
  const names = values
    .map((author) => normalizeText(typeof author === "string" ? author : author?.name))
    .filter(Boolean);
  const seen = new Set();
  return names.filter((name) => {
    const key = name.toLocaleLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).map((name) => ({name}));
}


function inferDoiFromText(text) {
  const match = String(text || "").match(DOI_PATTERN);
  return normalizeDoi(match?.[0] || null);
}


function parsePublicationDate(value, fallbackYear = null) {
  const text = normalizeText(value);
  if (!text) return {date: null, month: null, monthName: null, year: fallbackYear || null};
  const lower = text.toLocaleLowerCase();
  const monthIndex = MONTHS.findIndex((month) => lower.includes(month));
  const numericMonth = lower.match(/(?:^|\D)(0?[1-9]|1[0-2])\s*[/-]\s*(\d{4})(?:\D|$)/);
  const yearMatch = lower.match(/\b(19|20)\d{2}\b/);
  const year = yearMatch ? Number(yearMatch[0]) : fallbackYear || null;
  if (monthIndex >= 0) {
    return {
      date: text,
      month: monthIndex + 1,
      monthName: MONTHS[monthIndex][0].toUpperCase() + MONTHS[monthIndex].slice(1),
      year,
    };
  }
  if (numericMonth) {
    return {date: text, month: Number(numericMonth[1]), monthName: null, year: Number(numericMonth[2])};
  }
  return {date: text, month: null, monthName: null, year};
}


function labelledValue(text, labels) {
  const source = String(text || "");
  for (const label of labels) {
    const pattern = new RegExp(`${label}\\s*[:#]?\\s*([^\\n|]+)`, "i");
    const match = source.match(pattern);
    if (match?.[1]) return normalizeText(match[1]);
  }
  return null;
}


function inferArticleType(text) {
  const source = String(text || "");
  const match = source.match(/\b(?:Research|Original|Review|Survey|Short|Technical|Data|Case|Editorial|Correction|Erratum|Letter)(?:\s*-\s*|\s+)?(?:Article|Paper|Communication|Study)?\b/i);
  return classifyAcmDocumentType(match?.[0] || null);
}


export function parseAcmIssueItem(snapshot, context = {}) {
  const raw = snapshot && typeof snapshot === "object" ? snapshot : {};
  const text = normalizeText(raw.text || raw.visibleText || raw.innerText) || "";
  const pageInfo = context.pageInfo || (context.pageUrl ? parseAcmTocUrl(context.pageUrl) : {});
  const doi = normalizeDoi(raw.doi || raw.DOI || raw.dataDoi || inferDoiFromText(text));
  const landingUrl = canonicalAcmLandingUrl(doi)
    || canonicalAcmResourceUrl(raw.landingUrl || raw.articleUrl || raw.sourceUrl, context.pageUrl || ACM_ORIGIN);
  const title = firstNonEmpty(raw.title, raw.articleTitle, raw.titleText);
  const authors = normalizeAuthors(raw.authors || raw.authorNames);
  const authorsStatus = raw.authorsTruncated || raw.author_list_truncated
    ? "partial_visible_subset"
    : "visible_complete";
  const abstractTeaser = firstNonEmpty(raw.abstractTeaser, raw.visibleAbstractTeaser, raw.abstract_preview, raw.abstract);
  const publication = parsePublicationDate(
    raw.publicationDate || raw.publication_date || raw.date || labelledValue(text, ["Publication Date", "Published"]),
    raw.publicationYear || raw.publication_year || pageInfo.year || null,
  );
  const articleNumber = removeDoiTail(firstNonEmpty(
    raw.articleNumber,
    raw.articleNo,
    raw.article_number,
    labelledValue(text, ["Article No\\.?", "Article Number"]),
  ));
  const pages = removeDoiTail(firstNonEmpty(raw.pages, raw.pageRange, labelledValue(text, ["Pages?", "Page Range"])));
  const articleType = classifyAcmDocumentType(raw.articleType || raw.documentType || raw.type || inferArticleType(text));
  const ereaderUrl = canonicalAcmResourceUrl(raw.ereaderUrl || raw.eReaderUrl, context.pageUrl || ACM_ORIGIN);
  const abstractUrl = canonicalAcmResourceUrl(raw.abstractUrl, context.pageUrl || ACM_ORIGIN);
  const sourcePageUrl = context.pageInfo?.url || canonicalAcmTocUrl(context.pageUrl || raw.sourcePageUrl);
  const row = {
    schema_version: ACM_TOC_SCHEMA_VERSION,
    observation_kind: "acm_issue_item",
    venue_id: context.venueId || raw.venueId || null,
    journal_code: context.pageInfo?.journalCode || raw.journalCode || null,
    doi,
    landing_url: landingUrl,
    title,
    authors,
    authors_status: authorsStatus,
    abstract: null,
    abstract_teaser: abstractTeaser,
    abstract_status: abstractTeaser ? "partial_teaser" : "not_present",
    article_type: articleType,
    document_type: articleType,
    publication_date: publication.date,
    publication_month: publication.month,
    publication_month_name: publication.monthName,
    publication_year: publication.year,
    article_number: articleNumber,
    article_no: articleNumber,
    pages,
    ereader_url: ereaderUrl,
    abstract_url: abstractUrl,
    ereader_or_abstract_link: ereaderUrl || abstractUrl,
    section: firstNonEmpty(raw.section, context.section),
    issue: firstNonEmpty(raw.issue, context.pageInfo?.issue),
    volume: firstNonEmpty(raw.volume, context.pageInfo?.volume),
    year: Number(raw.year || context.pageInfo?.year || publication.year) || null,
    source_page_url: sourcePageUrl,
    observed_at: context.observedAt || new Date().toISOString(),
    source: "acm_visible_dom",
    source_observation: {
      doi_required: true,
      title_required: true,
      article_type_required: true,
      authors_status: authorsStatus,
      abstract_status: abstractTeaser ? "partial_teaser" : "not_present",
    },
  };
  return row;
}


export function validateAcmObservationRows(rows) {
  const errors = [];
  const seen = new Set();
  if (!Array.isArray(rows)) return {status: "FAIL", errors: ["rows must be an array"], sourceSetSha256: sha256Lines([])};
  rows.forEach((row, index) => {
    if (!row || typeof row !== "object") {
      errors.push(`row ${index + 1} is not an object`);
      return;
    }
    const doi = normalizeDoi(row.doi);
    if (!doi) errors.push(`row ${index + 1} lacks a DOI`);
    else if (seen.has(doi)) errors.push(`duplicate DOI: ${doi}`);
    else seen.add(doi);
    if (!normalizeText(row.title)) errors.push(`row ${index + 1} lacks a title`);
    if (!normalizeText(row.article_type)) errors.push(`row ${index + 1} lacks an article type`);
    if (row.abstract_status === "partial_teaser" && row.abstract != null) {
      errors.push(`row ${index + 1} treats a teaser as a full abstract`);
    }
  });
  return {
    status: errors.length ? "FAIL" : "PASS",
    errors,
    rowCount: rows.length,
    uniqueDoiCount: seen.size,
    sourceSetSha256: sha256Lines(seen),
  };
}


function acmObservationKey(row) {
  const page = normalizeText(row?.source_page_url || row?.source_url || "") || "";
  const doi = normalizeDoi(row?.doi) || "";
  return `${page}\u0000${doi}`;
}


export function validateAcmObservationBatchRows(rows) {
  const errors = [];
  const seenKeys = new Set();
  const dois = new Set();
  if (!Array.isArray(rows)) return {status: "FAIL", errors: ["rows must be an array"], observationCount: 0, uniqueDoiCount: 0, sourceSetSha256: sha256Lines([])};
  rows.forEach((row, index) => {
    if (!row || typeof row !== "object") {
      errors.push(`row ${index + 1} is not an object`);
      return;
    }
    const doi = normalizeDoi(row.doi);
    if (!doi) errors.push(`row ${index + 1} lacks a DOI`);
    else dois.add(doi);
    if (!normalizeText(row.title)) errors.push(`row ${index + 1} lacks a title`);
    if (!normalizeText(row.article_type)) errors.push(`row ${index + 1} lacks an article type`);
    if (row.abstract_status === "partial_teaser" && row.abstract != null) errors.push(`row ${index + 1} treats a teaser as a full abstract`);
    const key = acmObservationKey(row);
    if (seenKeys.has(key)) errors.push(`duplicate observation key: ${key.replace(/\u0000/g, " / ")}`);
    else seenKeys.add(key);
  });
  return {
    status: errors.length ? "FAIL" : "PASS",
    errors,
    observationCount: rows.length,
    uniqueObservationCount: seenKeys.size,
    uniqueDoiCount: dois.size,
    sourceSetSha256: sha256Lines(dois),
  };
}


export function sha256Lines(values) {
  return crypto.createHash("sha256").update([...values].map(String).sort().join("\n")).digest("hex");
}


export function sourceSetSha256(rows) {
  return sha256Lines((rows || []).map((row) => normalizeDoi(row?.doi)).filter(Boolean));
}


function isVisibleElement(element) {
  if (!element) return false;
  const style = window.getComputedStyle(element);
  const rectangle = element.getBoundingClientRect();
  return style.display !== "none"
    && style.visibility !== "hidden"
    && style.opacity !== "0"
    && rectangle.width > 0
    && rectangle.height > 0;
}


function cleanHref(rawHref, baseUrl) {
  if (!rawHref) return null;
  try {
    return new URL(rawHref, baseUrl).href;
  } catch {
    return null;
  }
}


async function readVisibleAcmPage(tab, context) {
  if (!tab?.playwright?.evaluate) throw new Error("a controlled in-app Browser tab is required");
  return await tab.playwright.evaluate((arg) => {
    const hardStopPattern = /captcha|verify\s+you\s+are\s+human|access\s+denied|forbidden|automated\s+access|request\s+blocked|temporarily\s+blocked|unusual\s+traffic|robot\s+check/i;
    const clean = (value) => {
      if (value == null) return null;
      const text = String(value).normalize("NFKC").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
      return text || null;
    };
    const visible = (element) => {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0" && rect.width > 0 && rect.height > 0;
    };
    const cleanHref = (rawHref, baseUrl = location.href) => {
      if (!rawHref) return null;
      try {
        return new URL(rawHref, baseUrl).href;
      } catch {
        return null;
      }
    };
    const textFrom = (root, selectors) => {
      for (const selector of selectors) {
        const element = root.querySelector(selector);
        const value = clean(element?.innerText || element?.textContent);
        if (value) return value;
      }
      return null;
    };
    const attrFrom = (root, selectors, attribute) => {
      for (const selector of selectors) {
        const value = clean(root.querySelector(selector)?.getAttribute(attribute));
        if (value) return value;
      }
      return null;
    };
    const allLinks = (root) => [...root.querySelectorAll("a[href]")]
      .map((link) => ({href: cleanHref(link.getAttribute("href"), location.href), text: clean(link.innerText || link.textContent)}))
      .filter((link) => link.href);
    const textMatch = (text, pattern) => text.match(pattern)?.[1]?.trim() || null;
    const inferType = (text) => {
      const match = text.match(/\b(?:Research|Original|Review|Survey|Short|Technical|Data|Case|Editorial|Correction|Erratum|Letter)(?:\s*-\s*|\s+)?(?:Article|Paper|Communication|Study)?\b/i);
      return clean(match?.[0]);
    };
    const textBeforeDoi = (value) => clean(String(value || "").replace(/\s*(?:(?:doi\s*:\s*|doi\s+)|https?:\/\/(?:dx\.)?doi\.org\/|https?:\/\/dl\.acm\.org\/doi\/)?10\.\d{4,9}\/.*$/i, ""));
    const findSection = (card) => {
      let parent = card.parentElement;
      for (let level = 0; parent && level < 5; level += 1, parent = parent.parentElement) {
        const heading = parent.querySelector("a.section__title, .section__title, [class*='section-title'], h2, h3");
        const value = clean(heading?.innerText || heading?.textContent);
        if (value && !/^view other \d+ authors$/i.test(value)) return value;
      }
      return null;
    };
    const bodyText = document.body?.innerText || "";
    const pageDateFallback = textFrom(document, [".bookPubDate", "main h2", ".issue-heading"])
      || textMatch(bodyText, /(?:publication date|published)\s*[:]?\s*([^\n|]+)/i);
    const snapshots = [...document.querySelectorAll(".issue-item-container")]
      .filter(visible)
      .map((card) => {
        const text = clean(card.innerText || card.textContent) || "";
        const links = allLinks(card);
        const doiLink = links.find((link) => /(?:doi\.org|dl\.acm\.org\/doi\/)/i.test(link.href) && /10\.\d{4,9}\//i.test(link.href));
        const title = textFrom(card, [".issue-item__title", ".article-title", ".issue-item-title", "h3", "h2"])
          || links.find((link) => /\/doi\//i.test(link.href) && link.text)?.text;
        const authorNames = [...card.querySelectorAll(".loa a, .author-name, .authors a, [class*='author'] a, a[href*='/author/'], [itemprop='author']")]
          .filter(visible)
          .map((element) => clean(element.innerText || element.textContent || element.getAttribute("content")))
          .filter((value) => value && !/^view other \d+ authors$/i.test(value));
        const authorExpander = card.querySelector('.issue-item-container button[aria-label^="View other"], button[aria-label^="View other"]');
        const publicationDate = textFrom(card, [".publication-date", ".issue-item__date", ".bookPubDate", "time", "[class*='publication-date']", "[class*='date']"])
          || textMatch(text, /(?:publication date|published)\s*[:]?\s*([^\n|]+)/i)
          || pageDateFallback;
        const abstractTeaser = textFrom(card, [".issue-item__abstract", ".abstract", ".abstract-teaser", "[class*='abstract']"]);
        const articleType = textFrom(card, [".issue-item__type", ".article-type", ".issue-heading", "[class*='article-type']", "[class*='publication-type']", "[class*='issue-heading']"])
          || attrFrom(card, ["[data-article-type]"], "data-article-type")
          || inferType(text);
        const articleNumber = textFrom(card, [".article-number", ".article-no", "[class*='article-no']"])
          || textMatch(text, /article\s+no\.?\s*[:#]?\s*([^\n|]+)/i);
        const pages = textFrom(card, [".page-range", ".pages", "[class*='page-range']"])
          || textMatch(text, /pages?\s*[:#]?\s*([^\n|]+)/i);
        const ereader = links.find((link) => /e-?reader/i.test(link.text || "") || /e-?reader/i.test(link.href));
        const abstract = links.find((link) => /abstract/i.test(link.text || "") || /abstract/i.test(link.href));
        const landing = doiLink || links.find((link) => /\/doi\//i.test(link.href));
        return {
          text,
          title,
          doi: doiLink?.href || textMatch(text, /(?:doi\s*:\s*|doi\.org\/)(10\.\d{4,9}\/[^\s|]+)/i),
          landingUrl: landing?.href || null,
          authors: authorNames,
          authorsTruncated: Boolean(authorExpander),
          authorExpansionLabel: clean(authorExpander?.getAttribute("aria-label")),
          abstractTeaser,
          articleType,
          publicationDate,
          articleNumber: textBeforeDoi(articleNumber),
          pages: textBeforeDoi(pages),
          ereaderUrl: ereader?.href || null,
          abstractUrl: abstract?.href || null,
          section: findSection(card),
          year: arg.pageInfo.year,
          volume: arg.pageInfo.volume,
          issue: arg.pageInfo.issue,
        };
      });
    const loadingNodes = [...document.querySelectorAll(".loading, .loading-indicator, [aria-busy='true'], [class*='loading']")].filter(visible);
    const loadingTextNodes = [...document.querySelectorAll("body *")].filter(visible).filter((element) => {
      if (element.children.length) return false;
      const value = clean(element.innerText || element.textContent);
      return Boolean(value && value.length < 160 && /^loading(?:\s|\.|…|$)/i.test(value));
    });
    const sectionTitles = [...document.querySelectorAll("a.section__title")].filter(visible).length;
    const hasContentMarker = /just\s+accepted|table\s+of\s+contents|contents|articles|no\s+articles/i.test(bodyText)
      || snapshots.length > 0
      || sectionTitles > 0;
    const accessSignalText = `${document.title || ""}\n${bodyText.slice(0, 4000)}`;
    return {
      page_url: `${location.origin}${location.pathname}`,
      transient_query_key_count: new Set([...new URLSearchParams(location.search).keys()]).size,
      page_title: clean(document.title),
      body_text_length: bodyText.length,
      hard_stop: !hasContentMarker && hardStopPattern.test(accessSignalText),
      loading_visible_count: loadingNodes.length,
      loading_text_visible_count: loadingTextNodes.length,
      section_title_count: sectionTitles,
      has_content_marker: hasContentMarker,
      cards: snapshots,
    };
  }, {pageInfo: context.pageInfo});
}


export function detectAcmPageCondition(state) {
  const bodyLength = Number(state?.body_text_length || 0);
  const title = normalizeText(state?.page_title) || "";
  if (state?.hard_stop) return {hardStop: true, loading: false, reason: "ACCESS_OR_CAPTCHA_BLOCK"};
  if (Number(state?.loading_visible_count || 0) > 0 || Number(state?.loading_text_visible_count || 0) > 0) {
    return {hardStop: false, loading: true, reason: "LOADING_VISIBLE"};
  }
  if (LOADING_PATTERN.test(title) || /\bloading\b|\bplease\s+wait\b/i.test(title)) return {hardStop: false, loading: true, reason: "LOADING_TITLE"};
  if (bodyLength < 80) return {hardStop: false, loading: true, reason: "BLANK_OR_EMPTY_SHELL"};
  if (!state?.has_content_marker && !Number(state?.section_title_count || 0) && !Number(state?.card_count || state?.cards?.length || 0)) {
    return {hardStop: false, loading: true, reason: "EMPTY_SHELL"};
  }
  return {hardStop: false, loading: false, reason: "READY"};
}


async function waitReal(tab, milliseconds) {
  if (!milliseconds || milliseconds <= 0) return;
  if (typeof tab?.playwright?.waitForTimeout === "function") {
    await tab.playwright.waitForTimeout(milliseconds);
    return;
  }
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}


async function currentPageUrl(tab) {
  try {
    if (typeof tab?.url === "function") return await tab.url();
    if (typeof tab?.url === "string") return tab.url;
    if (typeof tab?.currentUrl === "string") return tab.currentUrl;
    if (typeof tab?.playwright?.evaluate === "function") {
      const result = await tab.playwright.evaluate(() => location.href);
      return typeof result === "string" ? result : null;
    }
  } catch {
    return null;
  }
  return null;
}


function sameAllowedTocUrl(left, right) {
  try {
    return canonicalAcmTocUrl(left) === canonicalAcmTocUrl(right);
  } catch {
    return false;
  }
}


async function gotoAcmToc(tab, url, options) {
  const targetUrl = canonicalAcmTocUrl(url);
  let navigationError = null;
  try {
    await tab.goto(targetUrl, {timeoutMs: options.gotoTimeoutMs});
  } catch (error) {
    navigationError = error;
  }
  const reachedUrl = await currentPageUrl(tab);
  const reachedTarget = sameAllowedTocUrl(reachedUrl, targetUrl);
  if (navigationError && !reachedTarget) {
    const wrapped = new Error(`ACM navigation failed before reaching target: ${navigationError.message || navigationError}`);
    wrapped.cause = navigationError;
    wrapped.navigation = {targetUrl, reachedUrl, reachedTarget: false, error: serializeError(navigationError)};
    throw wrapped;
  }
  if (!navigationError && reachedUrl && !reachedTarget) {
    throw new Error(`ACM navigation landed outside target URL: ${reachedUrl}`);
  }
  return {
    target_url: targetUrl,
    reached_url: reachedUrl || targetUrl,
    reached_target: reachedTarget || !reachedUrl,
    goto_error: serializeError(navigationError),
    timeout_continued: Boolean(navigationError && reachedTarget),
  };
}


async function waitForAcmReady(tab, pageInfo, options) {
  let state = null;
  let rounds = 0;
  for (rounds = 1; rounds <= options.maxWaitRounds; rounds += 1) {
    await waitReal(tab, rounds === 1 ? options.initialWaitMs : options.retryWaitMs);
    state = await readVisibleAcmPage(tab, {pageInfo});
    const condition = detectAcmPageCondition(state);
    if (condition.hardStop) {
      const error = new Error(`${condition.reason} at ${state.page_url}`);
      error.hardStop = true;
      error.pageState = state;
      throw error;
    }
    if (!condition.loading) return {state, rounds};
  }
  const error = new Error(`ACM_VISIBLE_PAGE_NOT_READY: ${state?.page_url || pageInfo.url}`);
  error.pageState = state;
  throw error;
}


async function waitForLoadingToDisappear(tab, pageInfo, options) {
  let state = null;
  for (let round = 1; round <= options.maxWaitRounds; round += 1) {
    await waitReal(tab, round === 1 ? options.sectionWaitMs : options.retryWaitMs);
    state = await readVisibleAcmPage(tab, {pageInfo});
    const condition = detectAcmPageCondition(state);
    if (condition.hardStop) {
      const error = new Error(`${condition.reason} at ${state.page_url}`);
      error.hardStop = true;
      error.pageState = state;
      throw error;
    }
    if (!state.loading_visible_count && !state.loading_text_visible_count && !/loading|please wait/i.test(state.page_title || "")) return state;
  }
  const error = new Error(`ACM_LOADING_NOT_SETTLED at ${pageInfo.url}`);
  error.pageState = state;
  throw error;
}


async function expandVisibleSections(tab, pageInfo, options) {
  let clicked = 0;
  const selector = 'a.section__title[aria-expanded="false"]';
  for (let round = 0; round < options.maxSectionExpansions; round += 1) {
    const result = await tab.playwright.evaluate(() => {
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      };
      const link = [...document.querySelectorAll('a.section__title[aria-expanded="false"]')].find(visible);
      if (!link) return {clicked: false, remaining: 0};
      const label = (link.innerText || link.textContent || "").replace(/\s+/g, " ").trim();
      return {
        clicked: true,
        label,
        remaining: document.querySelectorAll('a.section__title[aria-expanded="false"]').length,
      };
    });
    if (!result?.clicked) return {clicked, remaining: Number(result?.remaining || 0)};
    await tab.playwright.locator(selector).first().click();
    clicked += 1;
    await waitForLoadingToDisappear(tab, pageInfo, options);
  }
  throw new Error(`ACM_SECTION_EXPANSION_LIMIT_EXCEEDED at ${pageInfo.url}`);
}


async function expandVisibleAuthors(tab, pageInfo, options) {
  let clicked = 0;
  const selector = '.issue-item-container button[aria-label^="View other"][aria-expanded="false"]';
  for (let round = 0; round < options.maxAuthorExpansions; round += 1) {
    const result = await tab.playwright.evaluate(() => {
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      };
      const button = [...document.querySelectorAll('.issue-item-container button[aria-label^="View other"][aria-expanded="false"]')]
        .filter(visible)
        .find((element) => /^view other \d+ authors$/i.test((element.getAttribute("aria-label") || element.innerText || element.textContent || "").replace(/\s+/g, " ").trim()));
      if (!button) return {clicked: false};
      const label = (button.innerText || button.textContent || "").replace(/\s+/g, " ").trim();
      return {clicked: true, label};
    });
    if (!result?.clicked) return {clicked};
    await tab.playwright.locator(selector).first().click();
    clicked += 1;
    await waitForLoadingToDisappear(tab, pageInfo, options);
  }
  throw new Error(`ACM_AUTHOR_EXPANSION_LIMIT_EXCEEDED at ${pageInfo.url}`);
}


function defaultOptions(options = {}) {
  return {
    initialWaitMs: Number(options.initialWaitMs ?? 12000),
    retryWaitMs: Number(options.retryWaitMs ?? 5000),
    sectionWaitMs: Number(options.sectionWaitMs ?? options.retryWaitMs ?? 5000),
    maxWaitRounds: Number(options.maxWaitRounds ?? 6),
    maxSectionExpansions: Number(options.maxSectionExpansions ?? 100),
    maxAuthorExpansions: Number(options.maxAuthorExpansions ?? 100),
    gotoTimeoutMs: Number(options.gotoTimeoutMs ?? 10000),
    minStartIntervalMs: Number(options.minStartIntervalMs ?? 1000),
  };
}


export async function enumerateAcmToc(tab, options = {}) {
  const runtime = defaultOptions(options);
  if (!tab) throw new Error("a controlled in-app Browser tab is required");
  if (!options.venueId) throw new Error("venueId is required");
  const pageInfo = parseAcmTocUrl(options.url || options.tocUrl || options.sourceUrl);
  const observedAt = options.observedAt || new Date().toISOString();
  const navigation = await gotoAcmToc(tab, pageInfo.url, runtime);
  let ready = await waitForAcmReady(tab, pageInfo, runtime);
  let sectionsExpanded = {clicked: 0, remaining: ready.state.section_title_count};
  // ACM's author-expander button can misroute to an author profile in the
  // in-app Browser.  Listing authors are therefore treated as an explicitly
  // partial surface; the DOI detail stage is authoritative for ordered authors.
  const authorsExpanded = {clicked: 0, skipped: true, completion_stage: "doi_detail"};
  if (pageInfo.kind === "issue") {
    sectionsExpanded = await expandVisibleSections(tab, pageInfo, runtime);
    ready = await waitForAcmReady(tab, pageInfo, runtime);
  } else {
    ready = await waitForAcmReady(tab, pageInfo, runtime);
  }
  if (!ready.state.cards?.length) {
    const error = new Error(`ACM_VISIBLE_PAGE_EMPTY at ${pageInfo.url}`);
    error.pageState = ready.state;
    throw error;
  }
  const rows = ready.state.cards.map((snapshot) => parseAcmIssueItem(snapshot, {
    venueId: options.venueId,
    pageInfo,
    observedAt,
  }));
  const validation = validateAcmObservationRows(rows);
  if (validation.status !== "PASS") {
    const error = new Error(`ACM_COMPLETENESS_GATE_FAILED: ${validation.errors.join("; ")}`);
    error.validation = validation;
    error.pageState = ready.state;
    throw error;
  }
  return {
    rows,
    evidence: {
      schema_version: "acm-visible-page-evidence-v1",
      venue_id: options.venueId,
      page_kind: pageInfo.kind,
      journal_code: pageInfo.journalCode,
      year: pageInfo.year,
      volume: pageInfo.volume,
      issue: pageInfo.issue,
      source_url: pageInfo.url,
      observed_at: observedAt,
      status: "PASS",
      worker_browsed: true,
      navigation,
      wait: {
        initial_wait_ms: runtime.initialWaitMs,
        retry_wait_ms: runtime.retryWaitMs,
        max_wait_rounds: runtime.maxWaitRounds,
        rounds: ready.rounds,
      },
      sections_expanded: sectionsExpanded.clicked,
      author_expanders_clicked: authorsExpanded.clicked,
      author_expansion_skipped: authorsExpanded.skipped,
      author_completion_stage: authorsExpanded.completion_stage,
      partial_author_card_count: rows.filter((row) => row.authors_status === "partial_visible_subset").length,
      visible_card_count: ready.state.cards.length,
      extracted_row_count: rows.length,
      required_fields_complete: true,
      doi_unique: validation.uniqueDoiCount === rows.length,
      source_item_set_sha256: validation.sourceSetSha256,
    },
  };
}


async function ensureJsonlFile(filePath) {
  const resolved = path.resolve(filePath);
  await fs.mkdir(path.dirname(resolved), {recursive: true});
  try {
    await fs.access(resolved);
  } catch {
    await fs.writeFile(resolved, "", "utf8");
  }
  return resolved;
}


async function writeJson(filePath, value) {
  const resolved = path.resolve(filePath);
  await fs.mkdir(path.dirname(resolved), {recursive: true});
  const temporary = `${resolved}.tmp-${crypto.randomUUID()}`;
  await fs.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await fs.rename(temporary, resolved);
}


async function appendJsonLine(filePath, value) {
  await fs.appendFile(filePath, `${JSON.stringify(value)}\n`, "utf8");
}


async function readJsonLines(filePath) {
  try {
    const text = await fs.readFile(filePath, "utf8");
    return text.split(/\r?\n/).filter((line) => line.trim()).map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        throw new Error(`invalid JSONL at ${filePath}:${index + 1}: ${error.message}`);
      }
    });
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}


async function readJsonIfPresent(filePath) {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}


async function fileSha256(filePath) {
  const data = await fs.readFile(filePath);
  return crypto.createHash("sha256").update(data).digest("hex");
}


function normalizeBatchUnit(unit) {
  const rawUrl = typeof unit === "string" ? unit : unit?.url || unit?.tocUrl || unit?.sourceUrl;
  const pageInfo = parseAcmTocUrl(rawUrl);
  const unitId = normalizeText(typeof unit === "object" ? unit.id || unit.unitId : null)
    || pageInfo.url;
  return {unitId, ...pageInfo};
}


function batchSummary({status, options, units, completedPages, rows, startedAt, completedAt, currentUnit = null, error = null}) {
  const validation = validateAcmObservationBatchRows(rows);
  return {
    schema_version: "acm-visible-metadata-summary-v1",
    status,
    venue_id: options.venueId,
    source_url_count: units.length,
    source_page_count: completedPages.size,
    completed_pages: completedPages.size,
    listing_observation_count: rows.length,
    records_observed: rows.length,
    records_emitted: rows.length,
    unique_doi_count: validation.uniqueDoiCount,
    source_item_set_sha256: validation.sourceSetSha256,
    completeness_status: validation.status === "PASS" ? "PASS" : "FAIL",
    required_fields: ["doi", "title", "article_type"],
    abstract_policy: "visible teaser only; abstract_status=partial_teaser; no full abstract claim",
    output_path: path.resolve(options.outputPath),
    evidence_path: path.resolve(options.evidencePath),
    checkpoint_path: path.resolve(options.checkpointPath),
    error_path: path.resolve(options.errorPath),
    min_start_interval_ms: options.minStartIntervalMs,
    started_at: startedAt,
    completed_at: completedAt,
    current_unit: currentUnit,
    error: error ? serializeError(error) : null,
  };
}


export async function enumerateAcmTocBatch(tab, units, options = {}) {
  if (!Array.isArray(units) || !units.length) throw new Error("at least one ACM TOC unit is required");
  if (!options.venueId) throw new Error("venueId is required");
  for (const key of ["outputPath", "evidencePath", "summaryPath", "checkpointPath", "errorPath"]) {
    if (!options[key]) throw new Error(`${key} is required for checkpointed ACM enumeration`);
  }
  const runtime = defaultOptions(options);
  const normalizedUnits = units.map(normalizeBatchUnit);
  const outputPath = await ensureJsonlFile(options.outputPath);
  const evidencePath = await ensureJsonlFile(options.evidencePath);
  const errorPath = await ensureJsonlFile(options.errorPath);
  const checkpointPath = path.resolve(options.checkpointPath);
  const summaryPath = path.resolve(options.summaryPath);
  const existingRows = await readJsonLines(outputPath);
  const existingEvidence = await readJsonLines(evidencePath);
  const existingValidation = validateAcmObservationBatchRows(existingRows);
  if (existingValidation.status !== "PASS") throw new Error(`existing ACM output failed completeness gate: ${existingValidation.errors.join("; ")}`);
  const checkpoint = options.resume === false ? null : await readJsonIfPresent(checkpointPath);
  const completedPages = new Set([
    ...(checkpoint?.completed_pages || []),
    ...existingEvidence.filter((row) => row?.status === "PASS" && row?.source_url).map((row) => canonicalAcmTocUrl(row.source_url)),
  ]);
  const startedAt = new Date().toISOString();
  let rows = [...existingRows];
  let lastStarted = 0;
  const maxPages = Number.isFinite(Number(options.maxPages))
    ? Math.max(0, Number(options.maxPages))
    : Number.POSITIVE_INFINITY;
  let pagesProcessedThisRun = 0;
  const writeCheckpoint = async (status, currentUnit = null, error = null) => {
    await writeJson(checkpointPath, {
      schema_version: "acm-visible-metadata-checkpoint-v1",
      status,
      venue_id: options.venueId,
      completed_pages: [...completedPages].sort(),
      completed_units: normalizedUnits.filter((unit) => completedPages.has(unit.url)).map((unit) => unit.unitId),
      current_unit: currentUnit,
      records_observed: rows.length,
      records_emitted: rows.length,
      source_item_set_sha256: sourceSetSha256(rows),
      output_path: outputPath,
      output_sha256: await fileSha256(outputPath),
      evidence_path: evidencePath,
      evidence_sha256: await fileSha256(evidencePath),
      error_path: errorPath,
      error: error ? serializeError(error) : null,
      updated_at: new Date().toISOString(),
    });
  };
  await writeCheckpoint("RUNNING");
  try {
    for (const unit of normalizedUnits) {
      if (completedPages.has(unit.url)) continue;
      if (pagesProcessedThisRun >= maxPages) break;
      const waitForRate = Math.max(0, runtime.minStartIntervalMs - (Date.now() - lastStarted));
      if (waitForRate) await waitReal(tab, waitForRate);
      lastStarted = Date.now();
      let result;
      try {
        result = await enumerateAcmToc(tab, {...runtime, ...options, url: unit.url});
      } catch (error) {
        await appendJsonLine(errorPath, {
          schema_version: "acm-visible-metadata-error-v1",
          venue_id: options.venueId,
          source_url: unit.url,
          unit_id: unit.unitId,
          status: "FAILED",
          error_code: error.hardStop ? "ACCESS_OR_CAPTCHA_BLOCK" : "ACM_ENUMERATION_FAILED",
          error: serializeError(error)?.message,
          observed_at: new Date().toISOString(),
        });
        await writeCheckpoint("FAILED", unit.unitId, error);
        await writeJson(summaryPath, batchSummary({
          status: "FAILED",
          options: {...options, ...runtime},
          units: normalizedUnits,
          completedPages,
          rows,
          startedAt,
          completedAt: new Date().toISOString(),
          currentUnit: unit.unitId,
          error,
        }));
        throw error;
      }
      const newValidation = validateAcmObservationRows(result.rows);
      if (newValidation.status !== "PASS") throw new Error(`page completeness gate failed at ${unit.url}`);
      const existingObservationSet = new Set(rows.map(acmObservationKey));
      const duplicateRows = result.rows.filter((row) => existingObservationSet.has(acmObservationKey(row)));
      if (duplicateRows.length && duplicateRows.length !== result.rows.length) {
        throw new Error(`resume/output DOI overlap is partial at ${unit.url}`);
      }
      if (!duplicateRows.length) {
        for (const row of result.rows) await appendJsonLine(outputPath, row);
        rows.push(...result.rows);
      }
      if (!existingEvidence.some((row) => row?.status === "PASS" && row.source_url === unit.url)) {
        await appendJsonLine(evidencePath, {...result.evidence, unit_id: unit.unitId});
        existingEvidence.push({...result.evidence, unit_id: unit.unitId});
      }
      completedPages.add(unit.url);
      pagesProcessedThisRun += 1;
      await writeCheckpoint("RUNNING", unit.unitId);
    }
  } catch (error) {
    throw error;
  }
  const finalValidation = validateAcmObservationBatchRows(rows);
  if (finalValidation.status !== "PASS") throw new Error(`final ACM completeness gate failed: ${finalValidation.errors.join("; ")}`);
  await writeCheckpoint("PASS");
  const summary = batchSummary({
    status: "PASS",
    options: {...options, ...runtime},
    units: normalizedUnits,
    completedPages,
    rows,
    startedAt,
    completedAt: new Date().toISOString(),
  });
  await writeJson(summaryPath, summary);
  return {rows, evidence: existingEvidence, summary};
}


export const ACM_DETAIL_SCHEMA_VERSION = "acm-visible-detail-row-v1";
export const ACM_DETAIL_EVIDENCE_SCHEMA_VERSION = "acm-visible-detail-evidence-v1";

const ACM_JOURNAL_CODE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const ACM_DETAIL_MISSING_REASONS = Object.freeze({
  doi: "checked_missing_on_official_detail",
  title: "not_present_on_official_detail",
  authors: "no_visible_authors_on_official_detail",
  abstract: "not_present_on_official_detail",
  article_type: "unknown_or_missing_article_type_on_official_detail",
  publication_date: "not_present_on_official_detail",
  native_id: "not_present_on_official_detail",
  volume: "not_present_on_official_detail",
  issue: "not_present_on_official_detail",
  article_number: "not_present_on_official_detail",
  pages: "not_present_on_official_detail",
});

function removeDoiTail(value) {
  const text = normalizeText(value);
  if (!text) return null;
  return normalizeText(text.replace(/\s*(?:(?:doi\s*:\s*|doi\s+)|https?:\/\/(?:dx\.)?doi\.org\/|https?:\/\/dl\.acm\.org\/doi\/)?10\.\d{4,9}\/.*$/i, ""));
}

function acmTitleSignature(value) {
  return (normalizeText(value) || "").toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}


export function parseAcmDetailUrl(rawUrl) {
  if (typeof rawUrl !== "string" || !rawUrl.trim()) throw new Error("ACM detail URL is required");
  let url;
  try {
    url = new URL(rawUrl);
  } catch (error) {
    throw new Error(`invalid ACM detail URL: ${error.message}`);
  }
  assertAllowedOrigin(url);
  const pathname = decodeURIComponent(url.pathname).replace(/\/$/, "");
  if (!pathname.startsWith("/doi/")) throw new Error(`ACM detail URL is outside the allowlist: ${rawUrl}`);
  const doi = normalizeDoi(pathname.slice("/doi/".length));
  if (!doi) throw new Error(`ACM detail URL lacks a valid DOI: ${rawUrl}`);
  return {doi, url: canonicalAcmLandingUrl(doi)};
}


const ACM_TRANSIENT_DETAIL_QUERY_KEYS = new Set(["__cf_chl_rt_tk"]);


export function inspectAcmReachedDetailUrl(rawUrl) {
  if (typeof rawUrl !== "string" || !rawUrl.trim()) throw new Error("reached ACM detail URL is required");
  let url;
  try {
    url = new URL(rawUrl);
  } catch (error) {
    throw new Error(`invalid reached ACM detail URL: ${error.message}`);
  }
  if (url.protocol !== "https:" || url.hostname !== ACM_HOST || url.port || url.username || url.password) {
    throw new Error(`unapproved reached ACM detail URL: ${redactAcmUrlForLog(url)}`);
  }
  if (url.hash) throw new Error(`reached ACM detail URL contains a fragment: ${redactAcmUrlForLog(url)}`);
  const queryKeys = [...new Set([...url.searchParams.keys()])];
  const unsupportedKeys = queryKeys.filter((key) => !ACM_TRANSIENT_DETAIL_QUERY_KEYS.has(key));
  if (unsupportedKeys.length) {
    throw new Error(`reached ACM detail URL contains an unsupported query: ${redactAcmUrlForLog(url)}`);
  }
  const pathname = decodeURIComponent(url.pathname).replace(/\/$/, "");
  if (!pathname.startsWith("/doi/")) throw new Error(`reached ACM detail URL is outside the allowlist: ${redactAcmUrlForLog(url)}`);
  const doi = normalizeDoi(pathname.slice("/doi/".length));
  if (!doi) throw new Error(`reached ACM detail URL lacks a valid DOI: ${redactAcmUrlForLog(url)}`);
  return {
    doi,
    url: canonicalAcmLandingUrl(doi),
    transient_query_observed: queryKeys.length > 0,
    transient_query_key_count: queryKeys.length,
  };
}


export function canonicalAcmDetailUrl(rawUrlOrDoi) {
  if (typeof rawUrlOrDoi === "string" && /^https?:\/\//i.test(rawUrlOrDoi.trim())) {
    return parseAcmDetailUrl(rawUrlOrDoi).url;
  }
  const doi = normalizeDoi(rawUrlOrDoi);
  if (!doi) throw new Error(`ACM detail DOI is invalid: ${rawUrlOrDoi}`);
  return canonicalAcmLandingUrl(doi);
}


export function classifyAcmInclusion(value, context = {}) {
  const raw = normalizeText(value);
  const lower = (raw || "").toLocaleLowerCase().replace(/[‐‑‒–—]/g, "-");
  const title = normalizeText(typeof context === "string" ? context : context?.title) || "";
  const titleExclusions = [
    [/^(?:guest\s+)?editorial\b/i, "editorial", "explicit_editorial_title"],
    [/^(?:foreword|preface)\b/i, "editorial", "explicit_front_matter_title"],
    [/^introduction\s+to\s+(?:the\s+)?special\s+(?:section|issue|collection)\b/i, "introduction", "explicit_special_section_introduction_title"],
    [/^(?:correction|erratum|corrigendum|retraction)\b/i, "correction", "explicit_correction_title"],
  ];
  for (const [pattern, reason_code, rule] of titleExclusions) {
    if (pattern.test(title)) return {decision: "exclude", reason_code, rule};
  }
  if (!raw) return {decision: "unknown", reason_code: "unknown_or_missing_article_type", rule: "missing_article_type"};
  const exclusions = [
    [/\bfront\s*-?\s*matter\b|\bfront\s+cover\b|\bback\s+cover\b|\bmasthead\b/, "front_matter", "explicit_front_matter_type"],
    [/\bintroduction\b/, "introduction", "explicit_introduction_type"],
    [/\b(?:editorial|foreword|preface)\b/, "editorial", "explicit_editorial_type"],
    [/\b(?:correction|erratum|corrigendum|retraction)\b/, "correction", "explicit_correction_type"],
    [/\btutorial\b/, "non_research_content", "explicit_tutorial_type"],
    [/\bkeynote\b/, "non_research_content", "explicit_keynote_type"],
  ];
  for (const [pattern, reason_code, rule] of exclusions) {
    if (pattern.test(lower)) return {decision: "exclude", reason_code, rule};
  }
  if (/\b(?:research|original)\s*-?\s*article\b/.test(lower)) {
    return {decision: "include", eligibility: "research_article", rule: "explicit_research_article_type"};
  }
  if (/\b(?:survey|review\s+article)\b/.test(lower)) {
    return {decision: "include", eligibility: "survey", rule: "explicit_survey_or_review_type"};
  }
  if (/\bshort\s*-?\s*paper\b/.test(lower)) {
    return {decision: "include", eligibility: "short_paper", rule: "explicit_short_paper_type"};
  }
  if (/^(?:technical\s+)?note$/.test(lower)) {
    return {decision: "include", eligibility: "technical_note", rule: "explicit_note_type"};
  }
  return {decision: "unknown", reason_code: "unknown_article_type", rule: "article_type_not_in_contract"};
}


export function deduplicateAcmListingRows(rows) {
  if (!Array.isArray(rows)) throw new Error("ACM listing rows must be an array");
  const byDoi = new Map();
  rows.forEach((raw, index) => {
    if (!raw || typeof raw !== "object") throw new Error(`ACM listing row ${index + 1} is not an object`);
    const doi = normalizeDoi(raw.doi || raw.DOI || raw.landing_url || raw.landingUrl);
    if (!doi) throw new Error(`ACM listing row ${index + 1} lacks a DOI`);
    const row = {...raw, doi};
    const existing = byDoi.get(doi);
    if (!existing) {
      byDoi.set(doi, row);
      return;
    }
    for (const [key, value] of Object.entries(row)) {
      const existingValue = existing[key];
      const missing = existingValue == null || existingValue === ""
        || (Array.isArray(existingValue) && existingValue.length === 0);
      const present = value != null && value !== "" && (!Array.isArray(value) || value.length > 0);
      if (missing && present) existing[key] = value;
    }
    if ((!Array.isArray(existing.authors) || !existing.authors.length) && Array.isArray(row.authors)) {
      existing.authors = row.authors;
    }
  });
  return [...byDoi.values()];
}


function detailVisibleSelector(selector) {
  return {selector, source: "official_acm_detail_visible_dom"};
}


export async function readVisibleAcmDetailPage(tab) {
  if (!tab?.playwright?.evaluate) throw new Error("a controlled in-app Browser tab is required");
  return await tab.playwright.evaluate(() => {
    const hardStopPattern = /captcha|verify\s+you\s+are\s+human|access\s+denied|forbidden|automated\s+access|request\s+blocked|temporarily\s+blocked|unusual\s+traffic|robot\s+check/i;
    const clean = (value) => {
      if (value == null) return null;
      const text = String(value).normalize("NFKC").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
      return text || null;
    };
    const visible = (element) => {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0" && rect.width > 0 && rect.height > 0;
    };
    const textOf = (element) => clean(element?.innerText || element?.textContent);
    const firstText = (selectors) => {
      for (const selector of selectors) {
        const element = document.querySelector(selector);
        const value = visible(element) ? textOf(element) : null;
        if (value) return {value, selector};
      }
      return {value: null, selector: null};
    };
    const firstAttribute = (selectors, attributes) => {
      for (const selector of selectors) {
        const element = document.querySelector(selector);
        if (!element) continue;
        for (const attribute of attributes) {
          const value = clean(element.getAttribute(attribute));
          if (value) return {value, selector: `${selector}@${attribute}`};
        }
      }
      return {value: null, selector: null};
    };
    const meta = (names) => {
      for (const name of names) {
        const selector = `meta[name="${name}"]`;
        const value = clean(document.querySelector(selector)?.getAttribute("content"));
        if (value) return {value, selector};
      }
      return {value: null, selector: null};
    };
    const dcIdentifiers = [...document.querySelectorAll('meta[name="dc.Identifier"]')]
      .map((element) => clean(element.getAttribute("content")))
      .filter(Boolean);
    const links = [...document.querySelectorAll(
      "a[href*='/doi/pdf/'], a[href*='/doi/epdf/'], a[href*='/doi/epub/'], a[href$='.pdf'], a[href*='.pdf?'], a[href*='e-reader'], a[href*='ereader']",
    )]
      .filter(visible)
      .map((link) => ({href: clean(link.href || link.getAttribute("href")), text: textOf(link)}))
      .filter((link) => link.href);
    const bodyText = document.body?.innerText || "";
    const firstPattern = (pattern, source) => {
      const match = String(source || "").match(pattern);
      return match?.[1] ? {value: clean(match[1]), selector: `visible_text:${pattern}`}
        : {value: null, selector: null};
    };
    const titleMeta = meta(["dc.Title", "citation_title"]);
    const titleField = firstText(["h1", "[data-title]", ".citation__title", ".article-title"]);
    const title = titleField.value && (!titleMeta.value || titleField.value.length > titleMeta.value.length)
      ? titleField
      : titleMeta;
    const authorRoot = document.querySelector(".loa, section#authors, article, header") || document;
    const authorElements = [...authorRoot.querySelectorAll(
      ".loa a, .loa [itemprop='name'], .author-name, .authors a, a[href*='/profile/'], a[href*='/author/'], [itemprop='author']",
    )].filter(visible);
    const visibleAuthors = authorElements
      .map((element) => textOf(element) || clean(element.getAttribute("content")))
      .filter((value) => value && !/^view other \d+ authors$/i.test(value));
    const authorMeta = [...document.querySelectorAll('meta[name="citation_author"], meta[name="dc.Creator"]')]
      .map((element) => clean(element.getAttribute("content")))
      .filter(Boolean);
    const authors = visibleAuthors.length ? visibleAuthors : authorMeta;
    const authorSource = visibleAuthors.length ? ".loa/.authors visible DOM" : authorMeta.length ? "meta[name=citation_author]/meta[name=dc.Creator]" : null;
    const abstractNodes = [...document.querySelectorAll("section#abstract [role='paragraph']")]
      .filter(visible)
      .map(textOf)
      .filter(Boolean);
    const abstract = abstractNodes.length ? {value: abstractNodes.join("\n\n"), selector: "section#abstract [role=paragraph]"} : {value: null, selector: null};
    const typeField = firstText([".article-type", ".publication-type", ".issue-heading", "[class*='article-type']", "[class*='publication-type']"]);
    const typeMeta = meta(["dc.Type", "citation_article_type"]);
    const typePattern = firstPattern(/\b(research\s*-\s*article|original\s*-?\s*article|survey|review\s+article|short\s*-?\s*paper|technical\s+note|note|tutorial|keynote|introduction|editorial|correction|erratum|corrigendum|front\s*-?\s*matter)\b/i, bodyText);
    const typeLooksExplicit = typeField.value && /\b(?:research\s*-?\s*article|original\s*-?\s*article|survey|review\s+article|short\s*-?\s*paper|technical\s+note|note|tutorial|keynote|introduction|editorial|correction|erratum|corrigendum|front\s*-?\s*matter)\b/i.test(typeField.value);
    const typeMetaLooksExplicit = typeMeta.value && /\b(?:research\s*-?\s*article|original\s*-?\s*article|survey|review\s+article|short\s*-?\s*paper|technical\s+note|note|tutorial|keynote|introduction|editorial|correction|erratum|corrigendum|front\s*-?\s*matter)\b/i.test(typeMeta.value);
    const articleType = typeMetaLooksExplicit ? typeMeta : typeLooksExplicit ? typeField : typePattern;
    const doiLink = links.find((link) => /(?:doi\.org|dl\.acm\.org\/doi\/)/i.test(link.href) && /10\.\d{4,9}\//i.test(link.href));
    const doiMeta = meta(["citation_doi"]);
    const doiIdentifier = dcIdentifiers.find((value) => /10\.\d{4,9}\//i.test(value));
    const doiPattern = firstPattern(/\b(10\.\d{4,9}\/[\w.!#$%&'()*+,/:;=?@\[\]~-]+)/i, bodyText);
    const doi = doiLink ? {value: doiLink.href, selector: "visible DOI link"} : doiMeta.value ? doiMeta : doiIdentifier ? {value: doiIdentifier, selector: 'meta[name="dc.Identifier"] DOI'} : doiPattern;
    const publishedDate = firstPattern(/\bPublished\s*:\s*(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})/i, bodyText);
    const published = publishedDate.value ? publishedDate : firstPattern(/\bPublished\s*:\s*([^\n|]+)/i, bodyText);
    const dateMeta = meta(["dc.Date", "citation_publication_date"]);
    const publicationDate = published.value ? published : dateMeta;
    const nativeId = firstAttribute(
      ["[data-article-id]", "[data-article-number]", "[data-item-id]", "[data-citation-id]"],
      ["data-article-id", "data-article-number", "data-item-id", "data-citation-id"],
    );
    const nativeIdMeta = meta(["citation_article_id", "citation_article_number"]);
    const nativeIdentifier = dcIdentifiers.find((value) => /^\d+$/.test(value));
    const volumeMeta = meta(["citation_volume"]);
    const issueMeta = meta(["citation_issue"]);
    const firstPageMeta = meta(["citation_firstpage"]);
    const lastPageMeta = meta(["citation_lastpage"]);
    const volumeIssue = bodyText.match(/\bVolume\s+([A-Za-z0-9.-]+)\s*,\s*Issue\s+([A-Za-z0-9.-]+)/i);
    const volume = volumeMeta.value ? volumeMeta : volumeIssue?.[1] ? {value: clean(volumeIssue[1]), selector: "visible_text:Volume, Issue"} : firstPattern(/\bVolume\s*[:#]?\s*([A-Za-z0-9.-]+)/i, bodyText);
    const issue = /^\d/.test(issueMeta.value || "") ? issueMeta : volumeIssue?.[2] ? {value: clean(volumeIssue[2]), selector: "visible_text:Volume, Issue"} : firstPattern(/\bIssue\s*[:#]?\s*(\d[\w.-]*)/i, bodyText);
    const articleNumber = firstPattern(/\bArticle\s+(?:No\.?|Number)\s*[:#]?\s*([^,\n|]+)/i, bodyText);
    const pageLabel = firstPattern(/\bPage(?:s| range)?\s*[:#]?\s*(\d+(?:\s*[-–—]\s*\d+)?)/i, bodyText);
    const pages = firstPageMeta.value || lastPageMeta.value
      ? {value: [firstPageMeta.value, lastPageMeta.value].filter(Boolean).join("-"), selector: `${firstPageMeta.selector},${lastPageMeta.selector}`}
      : pageLabel;
    const pdfLink = links.find((link) => /(?:\.pdf(?:$|[?#])|\/doi\/(?:epdf|pdf)|e-?reader)/i.test(`${link.href} ${link.text || ""}`));
    const loadingNodes = [...document.querySelectorAll(".loading, .loading-indicator, [aria-busy='true'], [class*='loading']")].filter(visible);
    const normalizedBodyText = clean(bodyText) || "";
    const loadingTextVisibleCount = normalizedBodyText.length < 1000 && /^loading(?:\s|\.|…|$)/i.test(normalizedBodyText) ? 1 : 0;
    const detailMarker = Boolean(title.value || doi.value || abstract.value || document.querySelector("meta[name='dc.Title']"));
    const accessSignalText = `${document.title || ""}\n${bodyText.slice(0, 4000)}`;
    return {
      page_url: `${location.origin}${location.pathname}`,
      transient_query_key_count: new Set([...new URLSearchParams(location.search).keys()]).size,
      page_title: clean(document.title),
      body_text_length: bodyText.length,
      body_text: bodyText.slice(0, 12000),
      hard_stop: !detailMarker && hardStopPattern.test(accessSignalText),
      loading_visible_count: loadingNodes.length,
      loading_text_visible_count: loadingTextVisibleCount,
      has_detail_marker: detailMarker,
      title: title.value,
      authors,
      abstract: abstract.value,
      abstract_selector: abstract.selector,
      abstract_status: abstract.value ? "full" : "missing",
      article_type: articleType.value,
      doi: doi.value,
      publication_date: publicationDate.value,
      native_id: nativeId.value || nativeIdMeta.value || nativeIdentifier || null,
      volume: volume.value,
      issue: issue.value,
      article_number: articleNumber.value,
      pages: pages.value,
      pdf_url: pdfLink?.href || null,
      pdf_selector: pdfLink ? "visible PDF/eReader link" : null,
      field_sources: {
        title: title.selector,
        authors: authorSource,
        abstract: abstract.selector,
        article_type: articleType.selector,
        doi: doi.selector,
        publication_date: publicationDate.selector,
        native_id: nativeId.selector || nativeIdMeta.selector,
        volume: volume.selector,
        issue: issue.selector,
        article_number: articleNumber.selector,
        pages: pages.selector,
        pdf_discovery_status: pdfLink ? "visible PDF/eReader link" : null,
      },
    };
  });
}


export function detectAcmDetailPageCondition(state) {
  const bodyLength = Number(state?.body_text_length || 0);
  const title = normalizeText(state?.page_title) || "";
  if (state?.hard_stop) return {hardStop: true, loading: false, reason: "ACCESS_OR_CAPTCHA_BLOCK"};
  if (Number(state?.loading_visible_count || 0) > 0 || Number(state?.loading_text_visible_count || 0) > 0) {
    return {hardStop: false, loading: true, reason: "LOADING_VISIBLE"};
  }
  if (LOADING_PATTERN.test(title) || /\bloading\b|\bplease\s+wait\b/i.test(title)) return {hardStop: false, loading: true, reason: "LOADING_TITLE"};
  if (bodyLength < 80) return {hardStop: false, loading: true, reason: "BLANK_OR_EMPTY_SHELL"};
  if (!state?.has_detail_marker) return {hardStop: false, loading: true, reason: "DETAIL_MARKER_MISSING"};
  return {hardStop: false, loading: false, reason: "READY"};
}


function sameAllowedDetailUrl(left, right) {
  try {
    return inspectAcmReachedDetailUrl(left).url === parseAcmDetailUrl(right).url;
  } catch {
    return false;
  }
}


async function gotoAcmDetail(tab, url, options) {
  const targetUrl = canonicalAcmDetailUrl(url);
  const initialUrl = await currentPageUrl(tab);
  let initialInfo = null;
  try {
    initialInfo = initialUrl ? inspectAcmReachedDetailUrl(initialUrl) : null;
  } catch {
    initialInfo = null;
  }
  if (initialInfo?.url === targetUrl) {
    return {
      target_url: targetUrl,
      reached_url: targetUrl,
      reached_target: true,
      goto_error: null,
      timeout_continued: false,
      current_target_reused: true,
      transient_query_observed: Boolean(initialInfo.transient_query_observed),
      transient_query_key_count: initialInfo.transient_query_key_count || 0,
    };
  }
  let navigationError = null;
  try {
    await tab.goto(targetUrl, {timeoutMs: options.gotoTimeoutMs});
  } catch (error) {
    navigationError = error;
  }
  const reachedUrl = await currentPageUrl(tab);
  let reachedInfo = null;
  try {
    reachedInfo = reachedUrl ? inspectAcmReachedDetailUrl(reachedUrl) : null;
  } catch {
    reachedInfo = null;
  }
  const reachedTarget = Boolean(reachedInfo && reachedInfo.url === targetUrl);
  const reachedUrlForLog = reachedInfo?.url || redactAcmUrlForLog(reachedUrl);
  if (navigationError && !reachedTarget) {
    const wrapped = new Error(`ACM detail navigation failed before reaching target: ${redactAcmErrorText(navigationError.message || navigationError)}`);
    wrapped.cause = navigationError;
    wrapped.navigation = {targetUrl, reachedUrl: reachedUrlForLog, reachedTarget: false, error: serializeError(navigationError)};
    throw wrapped;
  }
  if (!navigationError && reachedUrl && !reachedTarget) {
    throw new Error(`ACM detail navigation landed outside target URL: ${reachedUrlForLog}`);
  }
  return {
    target_url: targetUrl,
    reached_url: reachedInfo?.url || targetUrl,
    reached_target: reachedTarget || !reachedUrl,
    goto_error: serializeError(navigationError),
    timeout_continued: Boolean(navigationError && reachedTarget),
    current_target_reused: false,
    transient_query_observed: Boolean(reachedInfo?.transient_query_observed),
    transient_query_key_count: reachedInfo?.transient_query_key_count || 0,
  };
}


async function waitForAcmDetailReady(tab, options) {
  let state = null;
  let rounds = 0;
  for (rounds = 1; rounds <= options.maxWaitRounds; rounds += 1) {
    await waitReal(tab, rounds === 1 ? options.initialWaitMs : options.retryWaitMs);
    state = await readVisibleAcmDetailPage(tab);
    const condition = detectAcmDetailPageCondition(state);
    if (condition.hardStop) {
      const error = new Error(`${condition.reason} at ${state.page_url}`);
      error.hardStop = true;
      error.pageState = state;
      throw error;
    }
    if (!condition.loading) return {state, rounds};
  }
  const error = new Error(`ACM_DETAIL_PAGE_NOT_READY: ${state?.page_url || "unknown"}`);
  error.pageState = state;
  throw error;
}


function fieldProvenanceFor(field, value, source, missingReason, observedAt, sourceUrl) {
  const present = value != null && value !== "" && (!Array.isArray(value) || value.length > 0);
  const sourceValue = typeof source === "string" ? source : source?.selector || source?.source || null;
  return {
    source_url: sourceUrl || null,
    selector: sourceValue,
    method: sourceValue ? "official_acm_visible_dom" : "official_acm_detail_checked",
    observed_at: observedAt,
    status: present ? "present" : "missing",
    ...(present ? {} : {missing_reason: missingReason || "not_present_on_official_detail"}),
  };
}


export function parseAcmDetailSnapshot(snapshot, context = {}) {
  const raw = snapshot && typeof snapshot === "object" ? snapshot : {};
  const listing = context.listingRow || {};
  const observedAt = context.observedAt || raw.observed_at || new Date().toISOString();
  const detailDoi = normalizeDoi(raw.doi || raw.DOI);
  const expectedDoi = normalizeDoi(context.expectedDoi || listing.doi);
  const doi = detailDoi;
  const title = firstNonEmpty(raw.title, raw.articleTitle);
  const authors = normalizeAuthors(raw.authors || raw.authorNames);
  const abstract = raw.abstract_status === "full" || raw.abstractStatus === "full" ? firstNonEmpty(raw.abstract) : null;
  const publication = parsePublicationDate(
    raw.publication_date || raw.publicationDate,
    raw.publication_year || raw.publicationYear || listing.publication_year || listing.year || null,
  );
  const rawType = firstNonEmpty(raw.article_type_raw, raw.article_type, raw.articleType, raw.document_type_raw, raw.documentType, raw.type);
  const articleType = classifyAcmDocumentType(rawType);
  const nativeId = firstNonEmpty(raw.native_id, raw.nativeId, raw.source_native_id, raw.sourceNativeId)
    || (doi?.match(/\/([0-9]+)$/)?.[1] || null);
  const detailLanding = canonicalAcmLandingUrl(doi);
  const sourcePageUrl = firstNonEmpty(raw.source_page_url, raw.sourcePageUrl, listing.source_page_url, listing.sourcePageUrl);
  const section = firstNonEmpty(raw.section, listing.section);
  const volume = firstNonEmpty(raw.volume, listing.volume);
  const issue = firstNonEmpty(raw.issue, listing.issue);
  const year = Number(listing.year || listing.publication_year || raw.year || publication.year) || null;
  const articleNumber = removeDoiTail(firstNonEmpty(raw.article_number, raw.articleNumber, raw.article_no));
  const pages = removeDoiTail(firstNonEmpty(raw.pages, raw.pageRange));
  const pdfUrl = canonicalAcmResourceUrl(
    firstNonEmpty(raw.pdf_url, raw.pdfUrl, raw.ereader_url, raw.ereaderUrl),
    detailLanding || ACM_ORIGIN,
  );
  const pdfDiscoveryStatus = pdfUrl ? "visible_url" : "not_visible";
  const fieldSources = raw.field_sources || raw.fieldSources || {};
  const provenanceUrl = detailLanding
    || (expectedDoi ? canonicalAcmLandingUrl(expectedDoi) : null)
    || firstNonEmpty(listing.landing_url, listing.landingUrl, listing.source_url, listing.sourceUrl);
  const missingFieldReasons = {};
  const markMissing = (field, value) => {
    if (value == null || value === "" || (Array.isArray(value) && !value.length)) missingFieldReasons[field] = ACM_DETAIL_MISSING_REASONS[field] || "not_present_on_official_detail";
  };
  for (const [field, value] of Object.entries({doi, title, authors, abstract, articleType, publicationDate: publication.date, nativeId, volume, issue, articleNumber, pages})) {
    markMissing(field === "articleType" ? "article_type" : field === "publicationDate" ? "publication_date" : field, value);
  }
  const listingFallbackFields = new Set(["section", "source_page_url", "year", "volume", "issue"]);
  const provenance = {};
  const values = {
    doi,
    title,
    authors,
    abstract,
    article_type: articleType,
    publication_date: publication.date,
    publication_year: publication.year,
    native_id: nativeId,
    volume,
    issue,
    article_number: articleNumber,
    pages,
    section,
    source_page_url: sourcePageUrl,
    year,
    document_type: articleType,
    landing_url: detailLanding || (expectedDoi ? canonicalAcmLandingUrl(expectedDoi) : null),
    pdf_discovery_status: pdfDiscoveryStatus,
  };
  for (const [field, value] of Object.entries(values)) {
    const fallback = listingFallbackFields.has(field) && value != null && !fieldSources[field];
    provenance[field] = fieldProvenanceFor(
      field,
      value,
      fieldSources[field] || (fallback ? "accepted_listing_metadata_reused_after_detail_navigation" : null),
      ACM_DETAIL_MISSING_REASONS[field],
      observedAt,
      provenanceUrl,
    );
    if (fallback) {
      provenance[field].method = "accepted_acm_toc_listing_metadata_reused";
      provenance[field].reuse_status = "listing_value_retained_detail_field_not_observed";
    }
  }
  provenance.abstract_status = fieldProvenanceFor(
    "abstract_status",
    raw.abstract_status === "full" || raw.abstractStatus === "full" ? "full" : null,
    raw.abstract_selector || fieldSources.abstract,
    ACM_DETAIL_MISSING_REASONS.abstract,
    observedAt,
    provenanceUrl,
  );
  provenance.landing_url = fieldProvenanceFor("landing_url", detailLanding || (expectedDoi ? canonicalAcmLandingUrl(expectedDoi) : null), detailLanding ? fieldSources.doi || "official DOI detail URL" : "expected DOI listing fallback", ACM_DETAIL_MISSING_REASONS.doi, observedAt, provenanceUrl);
  provenance.pdf_discovery_status = fieldProvenanceFor("pdf_discovery_status", pdfDiscoveryStatus, fieldSources.pdf_discovery_status || raw.pdf_selector || null, null, observedAt, provenanceUrl);
  provenance.pdf_url = fieldProvenanceFor("pdf_url", pdfUrl, raw.pdf_selector || fieldSources.pdf_url || null, "not_visible", observedAt, provenanceUrl);
  provenance.source_native_id = provenance.native_id;
  const missingFields = {};
  if (!abstract) missingFields.abstract = {reason_code: "not_present_on_official_page"};
  if (!publication.date) missingFields.publication_date = {reason_code: "not_present_on_official_page"};
  if (!doi) missingFields.doi = {reason_code: "checked_missing"};
  if (!pdfUrl) missingFields.pdf_url = {reason_code: "not_visible"};
  if (expectedDoi && detailDoi && expectedDoi !== detailDoi) {
    provenance.doi.conflict = {expected_doi: expectedDoi, observed_doi: detailDoi};
  }
  const record = {
    schema_version: ACM_DETAIL_SCHEMA_VERSION,
    observation_kind: "acm_detail_item",
    venue_id: context.venueId || listing.venue_id || null,
    journal_code: context.journalCode || listing.journal_code || null,
    source_native_id: nativeId,
    native_id: nativeId,
    doi,
    title,
    authors,
    abstract,
    abstract_status: abstract ? "full" : "missing",
    article_type: articleType,
    document_type: articleType,
    document_type_raw: rawType,
    doi_status: doi ? "present" : "checked_missing",
    publication_date: publication.date,
    publication_month: publication.month,
    publication_month_name: publication.monthName,
    publication_year: publication.year,
    year,
    volume,
    issue,
    article_number: articleNumber,
    article_no: articleNumber,
    pages,
    pdf_url: pdfUrl,
    pdf_discovery_status: pdfDiscoveryStatus,
    section,
    landing_url: detailLanding || (expectedDoi ? canonicalAcmLandingUrl(expectedDoi) : null),
    source_url: detailLanding || (expectedDoi ? canonicalAcmLandingUrl(expectedDoi) : null),
    source_page_url: sourcePageUrl,
    observed_at: observedAt,
    source: "acm_visible_dom_detail",
    detail_page_revalidated: true,
    expected_listing_doi: expectedDoi,
    missing_fields: missingFields,
    missing_field_reasons: missingFieldReasons,
    field_provenance: provenance,
    source_reuse_policy: {
      listing_identity_reused: Boolean(expectedDoi),
      detail_fields_rechecked: true,
      teaser_not_promoted_to_abstract: true,
      catalog_write: false,
    },
  };
  return record;
}


function validateAcmDetailRecord(record, expectedDoi) {
  const errors = [];
  const observedDoi = normalizeDoi(record?.doi);
  if (!observedDoi) errors.push("doi_missing_on_detail");
  if (expectedDoi && observedDoi && expectedDoi !== observedDoi) errors.push("doi_mismatch");
  if (!normalizeText(record?.title)) errors.push("title_missing_on_detail");
  if (!normalizeText(record?.article_type)) errors.push("article_type_missing_on_detail");
  if (record?.abstract_status === "partial_teaser") errors.push("detail_abstract_must_not_be_teaser");
  return {status: errors.length ? "FAIL" : "PASS", errors};
}


export async function extractCurrentAcmDetail(tab, options = {}) {
  const runtime = defaultOptions(options);
  if (!tab) throw new Error("a controlled in-app Browser tab is required");
  if (!options.venueId) throw new Error("venueId is required");
  if (!options.journalCode || !ACM_JOURNAL_CODE_PATTERN.test(String(options.journalCode))) throw new Error("journalCode is required and must be safe");
  const listingRow = options.listingRow || null;
  let rawTarget = options.detailUrl || options.landingUrl || options.url || null;
  if (!rawTarget) rawTarget = canonicalAcmDetailUrl(options.doi || listingRow?.doi || await currentPageUrl(tab));
  const targetInfo = parseAcmDetailUrl(rawTarget);
  const expectedDoi = normalizeDoi(options.doi || listingRow?.doi || targetInfo.doi);
  const observedAt = options.observedAt || new Date().toISOString();
  const navigation = await gotoAcmDetail(tab, targetInfo.url, runtime);
  const ready = await waitForAcmDetailReady(tab, runtime);
  const readyLocation = inspectAcmReachedDetailUrl(ready.state.page_url);
  if (readyLocation.url !== targetInfo.url) {
    throw new Error(`ACM detail page changed away from target URL: ${redactAcmUrlForLog(ready.state.page_url)}`);
  }
  ready.state.page_url = readyLocation.url;
  const record = parseAcmDetailSnapshot(ready.state, {
    venueId: options.venueId,
    journalCode: options.journalCode,
    listingRow,
    expectedDoi,
    observedAt,
  });
  const validation = validateAcmDetailRecord(record, expectedDoi);
  const expectedTitle = normalizeText(listingRow?.title);
  if (expectedTitle && record.title && acmTitleSignature(expectedTitle) !== acmTitleSignature(record.title)) {
    validation.errors.push("title_mismatch");
    validation.status = "FAIL";
  }
  const evidence = {
    schema_version: ACM_DETAIL_EVIDENCE_SCHEMA_VERSION,
    venue_id: options.venueId,
    journal_code: options.journalCode,
    doi: expectedDoi,
    observed_doi: record.doi,
    source_url: targetInfo.url,
    page_url: ready.state.page_url,
    observed_at: observedAt,
    status: validation.status,
    worker_browsed: true,
    navigation,
    wait: {
      initial_wait_ms: runtime.initialWaitMs,
      retry_wait_ms: runtime.retryWaitMs,
      max_wait_rounds: runtime.maxWaitRounds,
      rounds: ready.rounds,
    },
    full_abstract_selector: "section#abstract [role=paragraph]",
    abstract_status: record.abstract_status,
    required_fields_complete: validation.status === "PASS",
    validation_errors: validation.errors,
    field_statuses: Object.fromEntries(Object.entries(record.field_provenance).map(([field, value]) => [field, value.status])),
    source_item_set_sha256: sourceSetSha256([{doi: record.doi || expectedDoi}]),
  };
  if (record.doi && expectedDoi && record.doi !== expectedDoi) {
    const error = new Error(`ACM detail DOI mismatch: expected ${expectedDoi}, observed ${record.doi}`);
    error.validation = validation;
    error.evidence = evidence;
    throw error;
  }
  if (validation.status !== "PASS") {
    const error = new Error(`ACM detail completeness validation failed: ${validation.errors.join(",")}`);
    error.validation = validation;
    error.evidence = evidence;
    throw error;
  }
  return {record, evidence, snapshot: ready.state};
}


async function readAcmListingInput(options) {
  if (Array.isArray(options.listingRows)) return options.listingRows;
  const listingPath = options.listingPath || options.manifestPath;
  if (!listingPath) throw new Error("listingRows or listingPath/manifestPath is required");
  const resolved = path.resolve(listingPath);
  const data = await fs.readFile(resolved, "utf8");
  if (resolved.endsWith(".json")) {
    const parsed = JSON.parse(data);
    if (Array.isArray(parsed)) return parsed;
    if (Array.isArray(parsed.rows)) return parsed.rows;
    throw new Error(`ACM listing JSON must be an array or {rows}: ${resolved}`);
  }
  return data.split(/\r?\n/).filter((line) => line.trim()).map((line, index) => {
    try {
      return JSON.parse(line);
    } catch (error) {
      throw new Error(`invalid ACM listing JSONL at ${resolved}:${index + 1}: ${error.message}`);
    }
  });
}


function withAcmDecision(record, classification, listingRow) {
  const decisionProvenance = {
    source_url: record.landing_url,
    observed_at: record.observed_at,
    method: "official_acm_visible_dom_detail_classification",
    status: "present",
  };
  return {
    ...record,
    inclusion_decision: classification.decision,
    eligibility: classification.eligibility || null,
    source_listing_row: {
      doi: normalizeDoi(listingRow?.doi),
      source_page_url: listingRow?.source_page_url || listingRow?.sourcePageUrl || null,
      enumeration_year: Number(listingRow?.year || listingRow?.publication_year) || null,
      enumeration_kind: /\/justaccepted(?:\/|$)/i.test(listingRow?.source_page_url || listingRow?.sourcePageUrl || "")
        ? "justaccepted"
        : "issue",
      listing_abstract_status: listingRow?.abstract_status || null,
      listing_metadata_reused: true,
    },
    source_reuse_policy: {
      ...(record.source_reuse_policy || {}),
      listing_identity_reused: true,
      listing_metadata_not_promoted_to_detail_abstract: true,
      detail_page_revalidated: true,
      catalog_write: false,
    },
    field_provenance: {
      ...(record.field_provenance || {}),
      inclusion_decision: decisionProvenance,
    },
  };
}


function toAcmExclusionRecord(record, classification, listingRow) {
  const exclusionReasonCode = classification.reason_code === "introduction" ? "editorial" : classification.reason_code;
  const exclusionProvenance = {
    source_url: record.landing_url,
    observed_at: record.observed_at,
    method: "official_acm_visible_dom_detail_classification",
    status: "excluded",
  };
  return {
    ...withAcmDecision(record, classification, listingRow),
    schema_version: "literature-metadata-exclusion-v1",
    inclusion_decision: "exclude",
    exclusion_reason_code: exclusionReasonCode,
    field_provenance: {
      ...(record.field_provenance || {}),
      inclusion_decision: exclusionProvenance,
      exclusion_reason_code: exclusionProvenance,
    },
    exclusion_evidence: {
      rule: classification.rule,
      raw_reason_code: classification.reason_code,
      explicit_article_type: record.document_type_raw || record.article_type,
      detail_page_revalidated: true,
      official_detail_url: record.landing_url,
      source_listing_url: listingRow?.source_page_url || listingRow?.sourcePageUrl || null,
    },
  };
}


function acmCounts(stagingRows, exclusionRows) {
  const all = [...stagingRows, ...exclusionRows];
  const missing = {};
  for (const row of all) {
    for (const reason of Object.values(row.missing_field_reasons || {})) missing[reason] = (missing[reason] || 0) + 1;
  }
  return {
    records_emitted: all.length,
    included: stagingRows.length,
    excluded: exclusionRows.length,
    missing_field_reason_counts: missing,
  };
}


export async function crawlAcmMetadata(options = {}) {
  const runtime = defaultOptions(options);
  const {
    tab,
    venueId,
    journalCode,
    checkpointPath,
    errorPath,
    summaryPath,
    evidencePath,
    exclusionPath,
    listingPath,
    manifestPath,
    listingRows,
    resume = true,
    maxItems = Number.POSITIVE_INFINITY,
  } = options;
  const stagingPath = options.stagingPath || options.outputPath;
  if (!tab) throw new Error("a controlled in-app Browser tab is required");
  if (!venueId || !journalCode || !ACM_JOURNAL_CODE_PATTERN.test(String(journalCode))) throw new Error("venueId and safe journalCode are required");
  for (const [name, value] of Object.entries({stagingPath, exclusionPath, evidencePath, checkpointPath, errorPath, summaryPath})) {
    if (!value) throw new Error(`${name} is required for checkpointed ACM detail crawl`);
  }
  const resolved = Object.fromEntries(Object.entries({stagingPath, exclusionPath, evidencePath, checkpointPath, errorPath, summaryPath}).map(([key, value]) => [key, path.resolve(value)]));
  for (const filePath of [resolved.stagingPath, resolved.exclusionPath, resolved.evidencePath, resolved.errorPath]) await ensureJsonlFile(filePath);
  const sourceRows = listingRows || await readAcmListingInput({listingRows, listingPath, manifestPath});
  const listing = deduplicateAcmListingRows(sourceRows);
  const sourceHash = sourceSetSha256(listing);
  const stagingRows = await readJsonLines(resolved.stagingPath);
  const exclusionRows = await readJsonLines(resolved.exclusionPath);
  const evidenceRows = await readJsonLines(resolved.evidencePath);
  const existingIds = new Set();
  for (const row of [...stagingRows, ...exclusionRows]) {
    const doi = normalizeDoi(row.doi);
    if (!doi) throw new Error("existing ACM staging/exclusion row lacks DOI");
    if (existingIds.has(doi)) throw new Error(`existing ACM output has duplicate DOI: ${doi}`);
    existingIds.add(doi);
  }
  const checkpoint = resume ? await readJsonIfPresent(resolved.checkpointPath) : null;
  if (checkpoint?.source_set_sha256 && checkpoint.source_set_sha256 !== sourceHash) {
    throw new Error("ACM listing source set changed during resume");
  }
  if (!resume && existingIds.size) throw new Error("resume=false requires empty ACM staging/exclusion outputs");
  const completed = new Set(existingIds);
  const startedAt = new Date().toISOString();
  let lastStarted = 0;
  let completedThisRun = 0;
  let currentDoi = null;
  const writeCheckpoint = async (status, error = null) => {
    await writeJson(resolved.checkpointPath, {
      schema_version: "acm-browser-detail-checkpoint-v1",
      status,
      venue_id: venueId,
      journal_code: journalCode,
      listing_count: sourceRows.length,
      deduplicated_listing_count: listing.length,
      completed_dois: [...completed].sort(),
      completed_total: completed.size,
      source_set_sha256: sourceHash,
      staging_path: resolved.stagingPath,
      staging_sha256: await fileSha256(resolved.stagingPath),
      exclusion_path: resolved.exclusionPath,
      exclusion_sha256: await fileSha256(resolved.exclusionPath),
      evidence_path: resolved.evidencePath,
      evidence_sha256: await fileSha256(resolved.evidencePath),
      error_path: resolved.errorPath,
      current_doi: currentDoi,
      error: error ? serializeError(error) : null,
      updated_at: new Date().toISOString(),
    });
  };
  const writeSummary = async (status, error = null) => {
    const counts = acmCounts(stagingRows, exclusionRows);
    const summary = {
      schema_version: "acm-browser-metadata-summary-v1",
      status,
      venue_id: venueId,
      journal_code: journalCode,
      listing_count: sourceRows.length,
      deduplicated_listing_count: listing.length,
      completed_count: completed.size,
      remaining_count: listing.length - completed.size,
      completed_this_run: completedThisRun,
      ...counts,
      records_emitted: counts.records_emitted,
      catalog_ready: false,
      catalog_write: false,
      controller_ingest_status: "PENDING",
      source_set_sha256: sourceHash,
      staging_path: resolved.stagingPath,
      staging_sha256: await fileSha256(resolved.stagingPath),
      exclusion_path: resolved.exclusionPath,
      exclusion_sha256: await fileSha256(resolved.exclusionPath),
      evidence_path: resolved.evidencePath,
      evidence_sha256: await fileSha256(resolved.evidencePath),
      error_path: resolved.errorPath,
      min_start_interval_ms: runtime.minStartIntervalMs,
      started_at: startedAt,
      completed_at: new Date().toISOString(),
      current_doi: currentDoi,
      error: error ? serializeError(error) : null,
    };
    await writeJson(resolved.summaryPath, summary);
    return summary;
  };
  await writeCheckpoint("RUNNING");
  try {
    for (const listingRow of listing) {
      const listingDoi = normalizeDoi(listingRow.doi);
      if (completed.has(listingDoi)) continue;
      if (completedThisRun >= maxItems) break;
      currentDoi = listingDoi;
      const waitForRate = Math.max(0, runtime.minStartIntervalMs - (Date.now() - lastStarted));
      if (waitForRate) await waitReal(tab, waitForRate);
      lastStarted = Date.now();
      let result;
      try {
        result = await extractCurrentAcmDetail(tab, {
          ...options,
          ...runtime,
          venueId,
          journalCode,
          listingRow,
          doi: listingDoi,
        });
      } catch (error) {
        await appendJsonLine(resolved.errorPath, {
          schema_version: "acm-browser-detail-error-v1",
          venue_id: venueId,
          journal_code: journalCode,
          doi: listingDoi,
          source_url: canonicalAcmLandingUrl(listingDoi),
          error_code: error.hardStop ? "ACCESS_OR_CAPTCHA_BLOCK" : "ACM_DETAIL_EXTRACTION_FAILED",
          error: serializeError(error)?.message,
          hard_stop: Boolean(error.hardStop),
          worker_browsed: true,
          observed_at: new Date().toISOString(),
        });
        await writeCheckpoint(error.hardStop ? "BLOCKED" : "FAILED", error);
        await writeSummary(error.hardStop ? "BLOCKED" : "FAILED", error);
        throw error;
      }
      const record = result.record;
      const detailValidation = validateAcmDetailRecord(record, listingDoi);
      if (detailValidation.status !== "PASS") {
        const error = new Error(`ACM detail completeness gate failed for ${listingDoi}: ${detailValidation.errors.join(",")}`);
        await appendJsonLine(resolved.errorPath, {
          schema_version: "acm-browser-detail-error-v1",
          venue_id: venueId,
          journal_code: journalCode,
          doi: listingDoi,
          source_url: record.landing_url,
          error_code: "DETAIL_COMPLETENESS_GATE_FAILED",
          error: serializeError(error)?.message,
          validation_errors: detailValidation.errors,
          worker_browsed: true,
          observed_at: new Date().toISOString(),
        });
        await writeCheckpoint("FAILED", error);
        await writeSummary("FAILED", error);
        throw error;
      }
      const classification = classifyAcmInclusion(record.document_type_raw || record.article_type, {title: record.title});
      if (classification.decision === "unknown") {
        const error = new Error(`unknown ACM article type for ${listingDoi}: ${record.document_type_raw || record.article_type || "missing"}`);
        await appendJsonLine(resolved.errorPath, {
          schema_version: "acm-browser-detail-error-v1",
          venue_id: venueId,
          journal_code: journalCode,
          doi: listingDoi,
          source_url: record.landing_url,
          error_code: "UNKNOWN_ARTICLE_TYPE",
          error: serializeError(error)?.message,
          article_type: record.document_type_raw || record.article_type || null,
          worker_browsed: true,
          observed_at: new Date().toISOString(),
        });
        await writeCheckpoint("FAILED", error);
        await writeSummary("FAILED", error);
        throw error;
      }
      if (classification.decision === "include" && !record.authors.length) {
        const error = new Error(`authorless eligible ACM item requires review: ${listingDoi}`);
        await appendJsonLine(resolved.errorPath, {
          schema_version: "acm-browser-detail-error-v1",
          venue_id: venueId,
          journal_code: journalCode,
          doi: listingDoi,
          source_url: record.landing_url,
          error_code: "AUTHORLESS_ELIGIBLE_ITEM",
          error: serializeError(error)?.message,
          article_type: record.document_type_raw || record.article_type,
          worker_browsed: true,
          observed_at: new Date().toISOString(),
        });
        await writeCheckpoint("FAILED", error);
        await writeSummary("FAILED", error);
        throw error;
      }
      const outputRow = classification.decision === "exclude"
        ? toAcmExclusionRecord(record, classification, listingRow)
        : {
            ...withAcmDecision(record, classification, listingRow),
            schema_version: "literature-metadata-staging-v1",
            readiness: "METADATA_STAGING_ONLY",
            catalog_ready: false,
            catalog_write: false,
          };
      if (existingIds.has(listingDoi)) throw new Error(`ACM output DOI became duplicated: ${listingDoi}`);
      if (classification.decision === "exclude") {
        await appendJsonLine(resolved.exclusionPath, outputRow);
        exclusionRows.push(outputRow);
      } else {
        await appendJsonLine(resolved.stagingPath, outputRow);
        stagingRows.push(outputRow);
      }
      await appendJsonLine(resolved.evidencePath, {...result.evidence, listing_doi: listingDoi, inclusion_decision: classification.decision});
      evidenceRows.push({...result.evidence, listing_doi: listingDoi, inclusion_decision: classification.decision});
      existingIds.add(listingDoi);
      completed.add(listingDoi);
      completedThisRun += 1;
      await writeCheckpoint("RUNNING");
    }
  } catch (error) {
    throw error;
  }
  const status = completed.size === listing.length ? "PASS" : "CHECKPOINTED";
  await writeCheckpoint(status);
  const summary = await writeSummary(status);
  return {
    staging: stagingRows,
    exclusions: exclusionRows,
    evidence: evidenceRows,
    summary,
  };
}
