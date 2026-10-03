(() => {
  'use strict';

  const SEARCH_MODES = {
    hybrid: '混合检索',
    semantic: '语义检索',
    keyword: '关键词检索',
  };

  const state = {
    controller: null,
    requestSequence: 0,
    statusReady: null,
    hasRenderedResults: false,
    results: [],
    selectedIds: new Set(),
    exporting: false,
    copyTimers: new WeakMap(),
  };

  const refs = {};

  document.addEventListener('DOMContentLoaded', init);

  function init() {
    refs.form = document.querySelector('#search-form');
    refs.query = document.querySelector('#query');
    refs.queryCount = document.querySelector('#query-count');
    refs.queryError = document.querySelector('#query-error');
    refs.mode = document.querySelector('#mode');
    refs.limit = document.querySelector('#limit');
    refs.yearFrom = document.querySelector('#year-from');
    refs.yearTo = document.querySelector('#year-to');
    refs.venueOptions = document.querySelector('#venue-options');
    refs.venueCount = document.querySelector('#venue-count');
    refs.searchButton = document.querySelector('#search-button');
    refs.backendStatus = document.querySelector('#backend-status');
    refs.backendStatusTitle = document.querySelector('#backend-status-title');
    refs.backendStatusDetail = document.querySelector('#backend-status-detail');
    refs.statusModel = document.querySelector('#status-model');
    refs.statusUpdated = document.querySelector('#status-updated');
    refs.results = document.querySelector('#results');
    refs.resultsHeading = document.querySelector('#results-heading');
    refs.resultSummary = document.querySelector('#result-summary');
    refs.feedback = document.querySelector('#search-feedback');
    refs.exportToolbar = document.querySelector('#export-toolbar');
    refs.selectAllResults = document.querySelector('#select-all-results');
    refs.exportSelectionCount = document.querySelector('#export-selection-count');
    refs.exportFormat = document.querySelector('#export-format');
    refs.exportSelectedButton = document.querySelector('#export-selected-button');
    refs.exportCurrentButton = document.querySelector('#export-current-button');

    refs.form.addEventListener('submit', handleSubmit);
    refs.query.addEventListener('input', updateQueryCount);
    refs.query.addEventListener('keydown', handleQueryShortcut);
    refs.venueOptions.addEventListener('change', updateVenueCount);
    refs.results.addEventListener('change', handleResultSelection);
    refs.selectAllResults.addEventListener('change', handleSelectAllResults);
    refs.exportSelectedButton.addEventListener('click', () => exportResults(selectedResultIds()));
    refs.exportCurrentButton.addEventListener('click', () => exportResults(state.results.map((result) => result.id)));

    document.querySelectorAll('[data-example]').forEach((button) => {
      button.addEventListener('click', () => {
        refs.query.value = button.getAttribute('data-example') || '';
        updateQueryCount();
        refs.query.focus();
      });
    });

    updateQueryCount();
    updateSearchAvailability();
    loadStatus();
  }

  async function loadStatus() {
    setStatusState('loading');
    try {
      const response = await fetch('/api/status', {
        headers: { Accept: 'application/json' },
      });
      const payload = await readJson(response);
      if (!response.ok) {
        throw new Error(getBackendError(payload, `状态请求失败（${response.status}）`));
      }
      if (!payload || typeof payload !== 'object' || typeof payload.ready !== 'boolean') {
        throw new Error('状态响应格式无效');
      }
      state.statusReady = payload.ready;
      setStatusState(payload.ready ? 'ready' : 'not-ready', payload);
      populateVenues(Array.isArray(payload.venues) ? payload.venues : []);
      updateSearchAvailability();
      if (!payload.ready && ['exporting', 'indexing'].includes(payload.phase)) {
        window.setTimeout(loadStatus, 10000);
      }
    } catch (error) {
      state.statusReady = null;
      setStatusState('error', { error: error instanceof Error ? error.message : '' });
      populateVenues([]);
      updateSearchAvailability();
    }
  }

  function setStatusState(kind, payload = {}) {
    refs.backendStatus.dataset.state = kind;
    refs.statusModel.textContent = '';
    refs.statusUpdated.textContent = '';

    if (kind === 'loading') {
      refs.backendStatusTitle.textContent = '正在连接本地索引';
      refs.backendStatusDetail.textContent = '读取索引状态…';
      return;
    }

    if (kind === 'ready') {
      refs.backendStatusTitle.textContent = '索引已就绪';
      const count = Number.isFinite(Number(payload.indexed_papers))
        ? formatNumber(Number(payload.indexed_papers))
        : '—';
      refs.backendStatusDetail.textContent = `${count} 篇论文可检索`;
      if (payload.stale) refs.backendStatusDetail.textContent += ' · 数据库有更新，当前检索使用上次索引';
      if (isPresent(payload.model)) {
        refs.statusModel.textContent = `模型：${asText(payload.model)}`;
      }
      if (isPresent(payload.updated_at)) {
        refs.statusUpdated.textContent = `更新：${asText(payload.updated_at)}`;
      }
      return;
    }

    if (kind === 'not-ready') {
      refs.backendStatusTitle.textContent = '索引尚未就绪';
      refs.backendStatusDetail.textContent = isPresent(payload.error)
        ? asText(payload.error)
        : '本地文献索引正在准备，请稍后再试。';
      if (!payload.error && payload.phase === 'indexing' && payload.progress && Number.isFinite(payload.progress.filesTotal)) {
        refs.backendStatusDetail.textContent = `已索引 ${formatNumber(payload.progress.filesIndexed || 0)} / ${formatNumber(payload.progress.filesTotal)} 篇论文，完成后可开始检索。`;
      }
      return;
    }

    refs.backendStatusTitle.textContent = '索引状态不可用';
    refs.backendStatusDetail.textContent = isPresent(payload.error)
      ? asText(payload.error)
      : '暂时无法读取本地索引状态。';
  }

  function populateVenues(venues) {
    refs.venueOptions.replaceChildren();
    const seen = new Set();
    const validVenues = venues.filter((venue) => {
      if (!venue || typeof venue !== 'object') return false;
      const id = asText(venue.id).trim();
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });

    if (!validVenues.length) {
      const empty = document.createElement('span');
      empty.className = 'venue-loading';
      empty.textContent = '暂无会场列表';
      refs.venueOptions.append(empty);
      updateVenueCount();
      return;
    }

    validVenues.forEach((venue, index) => {
      const id = asText(venue.id).trim();
      const labelText = asText(venue.label).trim() || id;
      const wrapper = document.createElement('label');
      wrapper.className = 'venue-option';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = id;
      checkbox.id = `venue-option-${index}`;
      const label = document.createElement('span');
      label.textContent = labelText;
      if (Number.isFinite(Number(venue.count))) {
        const count = document.createElement('small');
        count.textContent = formatNumber(Number(venue.count));
        label.append(' ', count);
      }
      wrapper.append(checkbox, label);
      refs.venueOptions.append(wrapper);
    });
    updateVenueCount();
  }

  function updateVenueCount() {
    const selected = refs.venueOptions.querySelectorAll('input[type="checkbox"]:checked').length;
    refs.venueCount.textContent = selected ? `已选 ${selected} 个` : '全部会场';
  }

  function handleQueryShortcut(event) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      refs.form.requestSubmit();
    }
  }

  function updateQueryCount() {
    const length = refs.query.value.length;
    refs.queryCount.textContent = `${length} / 2000`;
    if (length > 0) clearQueryError();
  }

  function updateSearchAvailability() {
    const disabled = state.statusReady === false || Boolean(state.controller) || state.exporting;
    refs.searchButton.disabled = disabled;
    refs.searchButton.setAttribute('aria-disabled', String(disabled));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    clearQueryError();

    const query = refs.query.value.trim();
    if (!query) {
      showQueryError('请先输入研究主题或 idea。');
      refs.query.focus();
      return;
    }

    const yearFrom = parseYear(refs.yearFrom.value);
    const yearTo = parseYear(refs.yearTo.value);
    if (yearFrom !== null && yearTo !== null && yearFrom > yearTo) {
      showQueryError('起始年份不能晚于结束年份。');
      refs.yearFrom.focus();
      return;
    }

    if (state.statusReady === false) {
      setFeedback('索引尚未就绪，暂时无法开始检索。', 'error');
      return;
    }

    const limit = Math.min(50, Math.max(1, Number.parseInt(refs.limit.value, 10) || 20));
    const mode = SEARCH_MODES[refs.mode.value] ? refs.mode.value : 'hybrid';
    await search({
      query,
      limit,
      venues: selectedVenues(),
      year_from: yearFrom,
      year_to: yearTo,
      mode,
    });
  }

  function selectedVenues() {
    return Array.from(refs.venueOptions.querySelectorAll('input[type="checkbox"]:checked'))
      .map((input) => input.value);
  }

  async function search(requestBody) {
    const sequence = ++state.requestSequence;
    if (state.controller) state.controller.abort();
    const controller = new AbortController();
    state.controller = controller;
    setBusy(true);

    try {
      const response = await fetch('/api/search', {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestBody),
        signal: controller.signal,
      });
      const payload = await readJson(response);
      if (sequence !== state.requestSequence) return;
      if (!response.ok) {
        throw new Error(getBackendError(payload, `检索失败（${response.status}）`));
      }
      const normalized = normalizeSearchPayload(payload);
      renderResults(normalized);
      if (normalized.warnings.length) {
        setFeedback(normalized.warnings.join('；'), 'warning');
      } else {
        setFeedback('', '');
      }
    } catch (error) {
      if (sequence !== state.requestSequence || (error && error.name === 'AbortError')) return;
      setFeedback(error instanceof Error ? error.message : '检索请求失败，请稍后再试。', 'error');
      if (!state.hasRenderedResults) renderErrorState();
    } finally {
      if (sequence === state.requestSequence) {
        state.controller = null;
        setBusy(false);
      }
    }
  }

  function setBusy(isBusy) {
    refs.results.setAttribute('aria-busy', String(isBusy));
    refs.backendStatus.classList.toggle('is-searching', isBusy);
    if (isBusy) {
      setFeedback(
        state.hasRenderedResults ? '正在检索，先保留上一次结果…' : '正在检索本地索引…',
        'loading',
      );
    } else if (refs.feedback.dataset.type === 'loading') {
      setFeedback('', '');
    }
    updateSearchAvailability();
  }

  function renderResults(payload) {
    const results = payload.results;
    state.results = results;
    state.selectedIds.clear();
    refs.results.replaceChildren();
    state.hasRenderedResults = true;

    if (!results.length) {
      refs.results.append(createEmptyState());
      refs.resultsHeading.textContent = '没有找到匹配论文';
    } else {
      const fragment = document.createDocumentFragment();
      results.forEach((result, index) => fragment.append(createResultCard(result, index)));
      refs.results.append(fragment);
      refs.resultsHeading.textContent = `找到 ${formatNumber(results.length)} 篇论文`;
    }

    const candidateCount = Number.isFinite(payload.totalCandidates)
      ? formatNumber(payload.totalCandidates)
      : formatNumber(results.length);
    const elapsed = Number.isFinite(payload.elapsedMs) ? `${formatNumber(payload.elapsedMs)} ms` : '';
    const mode = SEARCH_MODES[payload.mode] || '';
    const summaryParts = [`候选 ${candidateCount}`];
    if (elapsed) summaryParts.push(elapsed);
    if (mode) summaryParts.push(mode);
    refs.resultSummary.textContent = summaryParts.join(' · ');
    refs.resultSummary.hidden = false;
    updateExportControls();
  }

  function renderErrorState() {
    refs.results.replaceChildren();
    const stateCard = document.createElement('div');
    stateCard.className = 'empty-state error-state';
    const icon = document.createElement('span');
    icon.className = 'initial-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = '!';
    const title = document.createElement('h3');
    title.textContent = '这次检索没有完成';
    const description = document.createElement('p');
    description.textContent = '请检查服务状态后重试。已有结果会在下一次成功检索前保留。';
    stateCard.append(icon, title, description);
    refs.results.append(stateCard);
    refs.resultsHeading.textContent = '检索失败';
    refs.resultSummary.hidden = true;
    state.results = [];
    state.selectedIds.clear();
    updateExportControls();
  }

  function createEmptyState() {
    const stateCard = document.createElement('div');
    stateCard.className = 'empty-state';
    const icon = document.createElement('span');
    icon.className = 'initial-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = '∅';
    const title = document.createElement('h3');
    title.textContent = '当前条件下没有返回论文';
    const description = document.createElement('p');
    description.textContent = '可以尝试扩大年份范围、减少会场筛选，或换一种描述方式。';
    stateCard.append(icon, title, description);
    return stateCard;
  }

  function createResultCard(result, index) {
    const card = document.createElement('article');
    card.className = 'result-card';
    if (result.id) card.dataset.resultId = result.id;

    const header = document.createElement('header');
    header.className = 'result-header';
    const eyebrow = document.createElement('p');
    eyebrow.className = 'result-kicker';
    const rank = document.createElement('span');
    rank.textContent = `结果 ${String(index + 1).padStart(2, '0')}`;
    eyebrow.append(rank);
    if (Number.isFinite(result.score)) {
      const score = document.createElement('span');
      score.textContent = `检索评分 ${formatScore(result.score)}`;
      eyebrow.append(score);
    }
    const kickerRow = document.createElement('div');
    kickerRow.className = 'result-header-row';
    const selectLabel = document.createElement('label');
    selectLabel.className = 'result-select-option';
    const selectCheckbox = document.createElement('input');
    selectCheckbox.className = 'result-select-checkbox';
    selectCheckbox.type = 'checkbox';
    selectCheckbox.dataset.resultId = result.id;
    selectCheckbox.setAttribute('aria-label', `选择第 ${index + 1} 篇论文`);
    const selectText = document.createElement('span');
    selectText.textContent = '选择';
    selectLabel.append(selectCheckbox, selectText);
    kickerRow.append(eyebrow, selectLabel);
    const title = document.createElement('h3');
    title.className = 'result-title';
    title.textContent = result.title || '未提供标题';
    const authors = document.createElement('p');
    authors.className = 'result-authors';
    authors.textContent = result.authors.length ? result.authors.join(' · ') : '作者信息未提供';
    header.append(kickerRow, title, authors);
    card.append(header);

    const metadata = document.createElement('div');
    metadata.className = 'result-metadata';
    if (result.venues.length > 1) {
      result.venues.forEach((edition) => appendMetadata(metadata, `${edition.id} ${edition.year}`));
    } else {
      if (result.venue) appendMetadata(metadata, result.venue);
      if (Number.isInteger(result.year)) appendMetadata(metadata, String(result.year));
    }
    if (result.doi) appendMetadata(metadata, `DOI ${result.doi}`);
    if (metadata.childElementCount) card.append(metadata);

    if (result.evidence) {
      const evidence = document.createElement('div');
      evidence.className = 'evidence-box';
      const label = document.createElement('span');
      label.className = 'detail-label';
      label.textContent = '摘要摘录';
      const text = document.createElement('p');
      text.textContent = result.evidence;
      evidence.append(label, text);
      card.append(evidence);
    }

    if (result.matchedTerms.length) {
      const matched = document.createElement('div');
      matched.className = 'matched-terms';
      const label = document.createElement('span');
      label.className = 'detail-label';
      label.textContent = '匹配词';
      matched.append(label);
      const terms = document.createElement('div');
      terms.className = 'term-list';
      result.matchedTerms.forEach((term) => {
        const chip = document.createElement('span');
        chip.className = 'term-chip';
        chip.textContent = term;
        terms.append(chip);
      });
      matched.append(terms);
      card.append(matched);
    }

    if (result.abstractText) {
      const abstractBlock = document.createElement('div');
      abstractBlock.className = 'abstract-block';
      const label = document.createElement('span');
      label.className = 'detail-label';
      label.textContent = '摘要片段';
      const snippet = document.createElement('p');
      snippet.className = 'abstract-snippet';
      snippet.textContent = truncate(result.abstractText, 360);
      abstractBlock.append(label, snippet);

      const details = document.createElement('details');
      details.className = 'abstract-details';
      const summary = document.createElement('summary');
      summary.textContent = '展开完整摘要';
      const fullAbstract = document.createElement('p');
      fullAbstract.textContent = result.abstractText;
      details.append(summary, fullAbstract);
      abstractBlock.append(details);
      card.append(abstractBlock);
    }

    const footer = document.createElement('div');
    footer.className = 'result-footer';
    const links = document.createElement('div');
    links.className = 'result-links';
    appendExternalLink(links, 'PDF', result.pdfUrl);
    appendExternalLink(links, '文章页面', result.articleUrl || result.landingUrl);
    const copyButton = document.createElement('button');
    copyButton.className = 'copy-button';
    copyButton.type = 'button';
    copyButton.textContent = '复制引用';
    copyButton.addEventListener('click', () => copyCitation(result, copyButton));
    footer.append(links, copyButton);
    card.append(footer);
    return card;
  }

  function handleResultSelection(event) {
    const checkbox = event.target;
    if (!checkbox || !checkbox.matches('.result-select-checkbox')) return;
    const identity = checkbox.dataset.resultId || '';
    if (checkbox.checked) state.selectedIds.add(identity);
    else state.selectedIds.delete(identity);
    updateExportControls();
  }

  function handleSelectAllResults() {
    const checked = refs.selectAllResults.checked;
    refs.results.querySelectorAll('.result-select-checkbox').forEach((checkbox) => {
      checkbox.checked = checked;
      const identity = checkbox.dataset.resultId || '';
      if (checked) state.selectedIds.add(identity);
      else state.selectedIds.delete(identity);
    });
    updateExportControls();
  }

  function selectedResultIds() {
    return state.results
      .map((result) => result.id)
      .filter((identity) => state.selectedIds.has(identity));
  }

  function updateExportControls() {
    if (!refs.exportToolbar) return;
    const total = state.results.length;
    const selected = state.selectedIds.size;
    refs.exportToolbar.hidden = total === 0;
    refs.exportSelectionCount.textContent = `已选 ${formatNumber(selected)} 篇 · 当前展示 ${formatNumber(total)} 篇`;
    refs.selectAllResults.checked = total > 0 && selected === total;
    refs.selectAllResults.indeterminate = selected > 0 && selected < total;
    refs.selectAllResults.disabled = state.exporting || total === 0;
    refs.exportFormat.disabled = state.exporting;
    refs.exportSelectedButton.disabled = state.exporting || selected === 0;
    refs.exportSelectedButton.textContent = selected ? `导出所选 (${formatNumber(selected)})` : '导出所选';
    refs.exportCurrentButton.disabled = state.exporting || total === 0;
    refs.exportCurrentButton.textContent = `导出当前展示 (${formatNumber(total)})`;
    refs.results.querySelectorAll('.result-select-checkbox').forEach((checkbox) => {
      checkbox.disabled = state.exporting;
    });
  }

  function exportResults(identities) {
    if (!identities.length || state.exporting) return;
    const count = identities.length;
    const exportFormat = refs.exportFormat.value;
    state.exporting = true;
    updateSearchAvailability();
    updateExportControls();
    setFeedback(`正在发起 ${formatNumber(count)} 篇论文的下载…`, 'loading');
    try {
      const params = new URLSearchParams();
      identities.forEach((identity) => params.append('id', identity));
      params.set('format', exportFormat);
      const link = document.createElement('a');
      link.href = `/api/export?${params.toString()}`;
      link.download = downloadFilename(null, exportFormat);
      link.hidden = true;
      document.body.append(link);
      link.click();
      link.remove();
      setFeedback(`已发起 ${formatNumber(count)} 篇论文的下载。`, 'success');
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : '导出失败，请稍后再试。', 'error');
    } finally {
      state.exporting = false;
      updateSearchAvailability();
      updateExportControls();
    }
  }

  function downloadFilename(disposition, exportFormat) {
    const extensions = { csv: 'csv', json: 'json', bibtex: 'bib', ris: 'ris' };
    const fallback = `litdb-search-results.${extensions[exportFormat] || 'txt'}`;
    if (typeof disposition !== 'string') return fallback;
    const match = disposition.match(/(?:^|;)\s*filename="?([^";]+)"?/i);
    return match ? match[1] : fallback;
  }

  function appendMetadata(parent, value) {
    const item = document.createElement('span');
    item.className = 'metadata-item';
    item.textContent = value;
    parent.append(item);
  }

  function appendExternalLink(parent, label, value) {
    const url = safeExternalUrl(value);
    if (!url) return;
    const link = document.createElement('a');
    link.className = 'result-link';
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = `${label} ↗`;
    parent.append(link);
  }

  async function copyCitation(result, button) {
    const citation = buildCitation(result);
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(citation);
      } else {
        copyWithTextarea(citation);
      }
      button.textContent = '已复制';
      const previousTimer = state.copyTimers.get(button);
      if (previousTimer) window.clearTimeout(previousTimer);
      state.copyTimers.set(button, window.setTimeout(() => {
        button.textContent = '复制引用';
      }, 1800));
    } catch (_error) {
      button.textContent = '复制失败';
      window.setTimeout(() => {
        button.textContent = '复制引用';
      }, 1800);
    }
  }

  function copyWithTextarea(value) {
    const textarea = document.createElement('textarea');
    textarea.value = value;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.append(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    if (!copied) throw new Error('copy failed');
  }

  function buildCitation(result) {
    const parts = [];
    if (result.title) parts.push(`${result.title}.`);
    if (result.authors.length) parts.push(`${result.authors.join(', ')}.`);
    const publication = [result.venue, Number.isInteger(result.year) ? String(result.year) : '']
      .filter(Boolean)
      .join(', ');
    if (publication) parts.push(`${publication}.`);
    if (result.doi) parts.push(`DOI: ${result.doi}.`);
    const link = safeExternalUrl(result.articleUrl || result.landingUrl || result.pdfUrl);
    if (link) parts.push(link);
    return parts.join(' ');
  }

  function normalizeSearchPayload(payload) {
    if (!payload || typeof payload !== 'object' || !Array.isArray(payload.results)) {
      throw new Error('检索响应格式无效');
    }
    return {
      results: payload.results.map(normalizeResult),
      totalCandidates: Number.isFinite(Number(payload.total_candidates))
        ? Number(payload.total_candidates)
        : null,
      elapsedMs: Number.isFinite(Number(payload.elapsed_ms)) ? Number(payload.elapsed_ms) : null,
      mode: SEARCH_MODES[payload.mode] ? payload.mode : '',
      warnings: Array.isArray(payload.warnings)
        ? payload.warnings.map(asText).map((value) => value.trim()).filter(Boolean)
        : [],
    };
  }

  function normalizeResult(raw, index) {
    const item = raw && typeof raw === 'object' ? raw : {};
    const venues = Array.isArray(item.venues)
      ? item.venues.filter((venue) => venue && typeof venue === 'object').map((venue) => ({
        id: asText(venue.id),
        year: Number.isInteger(Number(venue.year)) ? Number(venue.year) : null,
      }))
      : [];
    const score = Number(item.score);
    const year = Number(item.year);
    return {
      id: asText(item.id) || `result-${index}`,
      title: asText(item.title).trim(),
      authors: asStringArray(item.authors),
      year: Number.isInteger(year) ? year : null,
      venue: asText(item.venue).trim(),
      venues,
      abstractText: typeof item.abstract === 'string' ? item.abstract.trim() : '',
      doi: asText(item.doi).trim(),
      landingUrl: safeExternalUrl(item.landing_url),
      pdfUrl: safeExternalUrl(item.pdf_url),
      articleUrl: safeExternalUrl(item.article_url),
      score: Number.isFinite(score) ? score : null,
      matchedTerms: asStringArray(item.matched_terms),
      evidence: asText(item.evidence).trim(),
    };
  }

  async function readJson(response) {
    const text = await response.text();
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch (_error) {
      throw new Error('服务器返回了无法读取的内容');
    }
  }

  function getBackendError(payload, fallback) {
    return payload && typeof payload.error === 'string' && payload.error.trim()
      ? payload.error.trim()
      : fallback;
  }

  function safeExternalUrl(value) {
    if (typeof value !== 'string' || !value.trim()) return '';
    try {
      const url = new URL(value.trim());
      return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : '';
    } catch (_error) {
      return '';
    }
  }

  function asText(value) {
    if (value === null || value === undefined) return '';
    return typeof value === 'string' ? value : String(value);
  }

  function asStringArray(value) {
    if (!Array.isArray(value)) return [];
    return value.map(asText).map((item) => item.trim()).filter(Boolean);
  }

  function isPresent(value) {
    return value !== null && value !== undefined && String(value).trim() !== '';
  }

  function parseYear(value) {
    if (value === '' || value === null || value === undefined) return null;
    const number = Number(value);
    return Number.isInteger(number) ? number : null;
  }

  function truncate(value, maxLength) {
    if (value.length <= maxLength) return value;
    return `${value.slice(0, maxLength).trimEnd()}…`;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat('zh-CN').format(value);
  }

  function formatScore(value) {
    return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
  }

  function showQueryError(message) {
    refs.queryError.hidden = false;
    refs.queryError.textContent = message;
    refs.query.classList.add('has-error');
  }

  function clearQueryError() {
    refs.queryError.hidden = true;
    refs.queryError.textContent = '';
    refs.query.classList.remove('has-error');
  }

  function setFeedback(message, type) {
    refs.feedback.dataset.type = type || '';
    refs.feedback.textContent = message || '';
    refs.feedback.hidden = !message;
  }
})();
