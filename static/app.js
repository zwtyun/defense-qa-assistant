'use strict';

const $ = (id) => document.getElementById(id);
const state = {
  view: 'answers', query: '', category: '', status: 'all', favorites: false,
  results: [], selectedId: null, answers: [], documents: [], health: {},
  editId: null, editOriginal: null, importBusy: false, requestSeq: 0,
  searchController: null, timer: null, previewSeq: 0, metaSeq: 0,
  selectedDocumentIds: new Set(), documentDeleteBusy: false,
};
const statuses = { reviewed: '已审核', reference: '参考答案', draft: '草稿' };
let voiceSearch = null;
let answerGeneration = null;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#i-' + name);
  svg.append(use);
  return svg;
}
function button(label, className, iconName, handler) {
  const node = el('button', className || 'button secondary');
  node.type = 'button';
  if (iconName) node.append(icon(iconName));
  if (label) node.append(el('span', '', label));
  if (handler) node.addEventListener('click', handler);
  return node;
}
function arr(value) { return Array.isArray(value) ? value : value ? [String(value)] : []; }
function responseArray(data, key) { return Array.isArray(data) ? data : arr(data && (data[key] || data.results)); }
function filename(doc) { return doc.filename || doc.original_name || doc.name || '未命名资料'; }
function statusBadge(value) {
  const name = statuses[value] ? value : 'reference';
  const badge = el('span', 'badge ' + name);
  if (name === 'reviewed') badge.append(icon('check'));
  badge.append(document.createTextNode(statuses[name]));
  return badge;
}
function toast(message, error = false) {
  const node = el('div', 'toast' + (error ? ' error' : ''), message);
  $('toastRegion').append(node);
  window.setTimeout(() => node.remove(), error ? 6500 : 3500);
}
function errorMessage(data, fallback) {
  if (!data) return fallback;
  if (typeof data.detail === 'string') return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map(x => x.msg || String(x)).join('；');
  return data.message || fallback;
}
async function api(path, options = {}) {
  const init = { ...options };
  if (init.body && !(init.body instanceof FormData)) {
    init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) };
    init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  const isJson = (response.headers.get('content-type') || '').includes('application/json');
  const data = isJson ? await response.json() : await response.text();
  if (!response.ok) throw new Error(errorMessage(data, '操作未完成（' + response.status + '）'));
  return data;
}
function highlight(node, text, terms = []) {
  const source = String(text || '');
  const words = [...new Set(arr(terms).map(String).filter(x => x.trim()))].sort((a,b) => b.length - a.length).slice(0, 30);
  if (!words.length) { node.textContent = source; return; }
  const escaped = words.map(x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const regex = new RegExp('(' + escaped.join('|') + ')', 'gi');
  let at = 0;
  for (const match of source.matchAll(regex)) {
    node.append(document.createTextNode(source.slice(at, match.index)));
    node.append(el('mark', '', match[0]));
    at = match.index + match[0].length;
  }
  node.append(document.createTextNode(source.slice(at)));
}
function dateText(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}
function documentURL(id) { return '/api/documents/' + encodeURIComponent(id) + '/file'; }
function selectedResult() { return state.results.find(item => String(item.id) === String(state.selectedId)); }
function emptyState(title, description, iconName, action) {
  const box = el('div', 'empty-state');
  const symbol = el('span', 'empty-icon'); symbol.append(icon(iconName || 'search'));
  box.append(symbol, el('h3', '', title), el('p', '', description));
  if (action) box.append(action);
  return box;
}

async function refreshMeta({ silent = false } = {}) {
  const seq = ++state.metaSeq;
  try {
    const [health, answers, documents] = await Promise.all([
      api('/api/health'), api('/api/answers'), api('/api/documents'),
    ]);
    if (seq !== state.metaSeq) return;
    state.health = health;
    state.answers = responseArray(answers, 'answers');
    state.documents = responseArray(documents, 'documents');
    const answerCount = health.answer_count ?? state.answers.length;
    const documentCount = health.document_count ?? state.documents.length;
    $('navAnswerCount').textContent = answerCount;
    $('navDocumentCount').textContent = documentCount;
    $('favoriteCount').textContent = state.answers.filter(x => x.favorite).length;
    $('statAnswers').textContent = answerCount;
    $('statDocuments').textContent = documentCount;
    $('statReviewed').textContent = health.reviewed_count ?? state.answers.filter(x => x.status === 'reviewed').length;
    $('statChunks').textContent = health.chunk_count ?? 0;
    $('connectionStatus').replaceChildren(el('span', 'status-dot'), document.createTextNode('本地服务已连接'));
    $('connectionStatus').classList.remove('disconnected');
    fillCategories();
    renderDocuments();
  } catch (err) {
    if (seq !== state.metaSeq) return;
    $('connectionStatus').replaceChildren(el('span', 'status-dot'), document.createTextNode('本地服务未连接'));
    $('connectionStatus').classList.add('disconnected');
    if (!silent) toast('无法连接本地服务，请确认启动程序仍在运行。', true);
    throw err;
  }
}
function clearLibraryDisplay({ keepMetadata = false } = {}) {
  clearTimeout(state.timer);
  state.requestSeq += 1;
  state.previewSeq += 1;
  state.metaSeq += 1;
  if (state.searchController) state.searchController.abort();
  if (voiceSearch) voiceSearch.cancel('资料已更新，语音输入已取消。');
  if (answerGeneration) answerGeneration.cancel('资料已更新，当前生成已停止；历史对话仍保留。');
  state.results = [];
  state.selectedId = null;
  if (!keepMetadata) { state.answers = []; state.documents = []; }
  $('resultCount').textContent = '0 条';
  $('searchTiming').textContent = '';
  $('resultList').removeAttribute('aria-busy');
  renderResults();
  renderDetail();
  if (!keepMetadata) $('documentList').replaceChildren();
  renderDocumentControls();
  $('sourcePreview').replaceChildren();
  $('sourceTitle').textContent = '查看原文';
  $('sourceOpen').removeAttribute('href');
  if ($('sourceDialog').open) $('sourceDialog').close();
}
function fillCategories() {
  const categories = [...new Set(state.answers.map(x => x.category || '未分类'))].sort((a,b) => a.localeCompare(b, 'zh-CN'));
  $('categoryFilter').replaceChildren(new Option('全部分类', ''));
  $('categoryOptions').replaceChildren();
  for (const category of categories) {
    $('categoryFilter').append(new Option(category, category));
    $('categoryOptions').append(new Option(category, category));
  }
  if (state.category && !categories.includes(state.category)) state.category = '';
  $('categoryFilter').value = state.category;
}
function updateNav() {
  document.querySelectorAll('[data-view]').forEach(node => {
    node.classList.toggle('active', node.dataset.view === state.view && !state.favorites);
  });
  $('favoritesNav').classList.toggle('active', state.view === 'answers' && state.favorites);
  $('favoriteFilter').setAttribute('aria-pressed', String(state.favorites));
  const isDocs = state.view === 'documents';
  const isSource = state.view === 'sources';
  $('searchView').hidden = isDocs;
  $('documentsView').hidden = !isDocs;
  $('breadcrumbCurrent').textContent = isDocs ? '资料管理' : isSource ? '原文检索' : state.favorites ? '我的收藏' : '答案检索';
  $('answerFilters').hidden = isSource;
  $('addAnswer').hidden = isSource;
  $('searchTitle').textContent = isSource ? '回到材料，找到依据。' : state.favorites ? '重要回答，随手可查。' : '现场答辩辅助';
  $('searchSubtitle').textContent = isSource ? '搜索 Word、PDF 中的原文片段，确认数据与上下文。' : state.favorites ? '把高频问题放在手边，答辩时更快找到。' : '保留每轮问答，继续追问时承接前文。';
  $('searchInput').placeholder = isSource ? '搜索原文中的词语、指标、技术名称……' : '例如：传感器漂移和真实工况变化，怎样区分？';
  $('searchHint').replaceChildren(el('span', 'small-dot'), document.createTextNode(isSource ? '原文摘录，保留来源位置' : '下方筛选用于查看已有答案'));
  if (answerGeneration) answerGeneration.setView(state.view, state.favorites);
}
function setView(view, favorites = false) {
  if (voiceSearch) voiceSearch.cancel('已切换页面，语音输入已取消。');
  state.view = view;
  state.favorites = favorites;
  state.selectedId = null;
  updateNav();
  if (view === 'documents') {
    state.requestSeq += 1;
    if (state.searchController) state.searchController.abort();
    refreshMeta({ silent: true }).catch(() => {});
  } else {
    search();
  }
}
function scheduleSearch() {
  clearTimeout(state.timer);
  state.query = $('searchInput').value;
  $('clearSearch').hidden = !state.query;
  state.timer = setTimeout(() => search(), 150);
}
async function search({ keepSelection = false } = {}) {
  clearTimeout(state.timer);
  if (state.view === 'documents') return;
  if (answerGeneration) answerGeneration.onQuestionChanged();
  state.query = $('searchInput').value.trim();
  $('clearSearch').hidden = !state.query;
  const seq = ++state.requestSeq;
  if (state.searchController) state.searchController.abort();
  if (state.query.length > 500) {
    state.searchController = null;
    state.results = [];
    state.selectedId = null;
    $('resultLabel').textContent = '请精简问题';
    $('resultCount').textContent = '待精简';
    $('searchTiming').textContent = '';
    $('resultList').removeAttribute('aria-busy');
    const guidance = '输入文字已完整保留，请精简到500字以内，或分成几题后再检索与发送。';
    $('resultList').replaceChildren(emptyState('问题超过500字', guidance, 'search'));
    $('answerDetail').replaceChildren(emptyState('等待精简问题', guidance, 'search'));
    return;
  }
  state.searchController = new AbortController();
  const params = new URLSearchParams({ q: state.query, mode: state.view, limit: '30' });
  if (state.view === 'answers') {
    params.set('category', state.category);
    params.set('status', state.status);
    params.set('favorites', String(state.favorites));
  }
  $('searchTiming').textContent = '正在本地检索…';
  $('resultList').setAttribute('aria-busy', 'true');
  if (!state.results.length) {
    $('resultList').replaceChildren(...[1,2,3].map(() => el('div', 'skeleton')));
  }
  try {
    const data = await api('/api/search?' + params, { signal: state.searchController.signal });
    if (seq !== state.requestSeq) return;
    state.results = responseArray(data, 'results');
    const prior = keepSelection && state.results.some(x => String(x.id) === String(state.selectedId));
    if (!prior) state.selectedId = state.results[0] ? state.results[0].id : null;
    const total = data.total ?? state.results.length;
    $('resultLabel').textContent = state.query ? '相关' + (state.view === 'sources' ? '原文' : '答案') : state.view === 'sources' ? '原文片段' : state.favorites ? '收藏答案' : '全部答案';
    $('resultCount').textContent = total + ' 条' + (total > state.results.length ? ' · 显示前 ' + state.results.length + ' 条' : '');
    $('searchTiming').textContent = Number.isFinite(data.elapsed_ms) ? '本地检索 ' + Math.round(data.elapsed_ms) + ' ms' : '本地检索';
    renderResults();
    renderDetail();
  } catch (err) {
    if (err.name === 'AbortError' || seq !== state.requestSeq) return;
    state.results = [];
    state.selectedId = null;
    $('resultCount').textContent = '检索未完成';
    $('searchTiming').textContent = '';
    const errorBox = emptyState('暂时无法检索', err.message || '请检查本地服务是否正在运行。', 'search', button('重新连接', 'button secondary', null, async () => { await refreshMeta({silent:true}).catch(() => {}); search(); }));
    errorBox.classList.add('error-state');
    $('resultList').replaceChildren(errorBox);
    $('answerDetail').replaceChildren(emptyState('等待连接', '你的资料仍然保存在本机。重新启动应用后可继续使用。', 'shield'));
  } finally {
    if (seq === state.requestSeq) $('resultList').removeAttribute('aria-busy');
  }
}
function renderResults() {
  const list = $('resultList');
  list.replaceChildren();
  if (!state.results.length) {
    const filtered = state.query || state.category || state.status !== 'all' || state.favorites;
    let title = filtered ? '没有找到匹配内容' : state.view === 'sources' ? '还没有原文资料' : '从第一条答案开始';
    let text = filtered ? '试试更短的关键词、其他问法，或放宽分类和审核状态。' : '导入已有的问答资料，或手动创建一张答案卡片。';
    if (state.favorites && !state.query) { title = '还没有收藏答案'; text = '点击答案旁的星标，把常用回答收在这里。'; }
    const action = filtered ? button('清除筛选', 'button secondary', null, clearFilters) : button('导入资料', 'button secondary', 'upload', openImport);
    list.append(emptyState(title, text, state.favorites ? 'star' : 'search', action));
    return;
  }
  for (const item of state.results) {
    const card = el('article', 'result-card' + (String(item.id) === String(state.selectedId) ? ' selected' : ''));
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', state.view === 'sources' ? filename(item) + ' ' + (item.locator || '') : item.question || '查看答案');
    card.setAttribute('aria-current', String(String(item.id) === String(state.selectedId)));
    card.dataset.id = item.id;
    const top = el('div', 'card-topline');
    const category = el('span', 'card-category', state.view === 'sources' ? '原文片段' : item.category || '未分类');
    top.append(category);
    if (state.view === 'answers') {
      const status = el('span', 'card-status ' + (item.status || 'reference'));
      if (item.status === 'reviewed') status.append(icon('check'));
      status.append(document.createTextNode(statuses[item.status] || '参考答案'));
      top.append(status);
    } else { top.append(el('span', 'card-status', item.locator || (item.page ? '第 ' + item.page + ' 页' : ''))); }
    const title = el('h3', 'card-title');
    highlight(title, state.view === 'sources' ? filename(item) : item.question, item.matched_terms);
    const preview = el('p', 'card-preview');
    highlight(preview, state.view === 'sources' ? item.text : item.short_answer || item.answer, item.matched_terms);
    const bottom = el('div', 'card-bottom');
    const tags = el('div', 'card-tags');
    const keywords = state.view === 'sources' ? arr(item.matched_terms) : arr(item.keywords);
    for (const tag of keywords.slice(0, 4)) tags.append(el('span', 'tag', tag));
    bottom.append(tags);
    if (state.view === 'answers') {
      const star = button('', 'card-favorite' + (item.favorite ? ' active' : ''), 'star', event => {
        event.stopPropagation(); toggleFavorite(item);
      });
      star.title = item.favorite ? '取消收藏' : '收藏答案';
      star.setAttribute('aria-label', star.title);
      star.setAttribute('aria-pressed', String(!!item.favorite));
      bottom.append(star);
    }
    card.append(top, title, preview, bottom);
    card.addEventListener('click', () => selectResult(item.id));
    card.addEventListener('keydown', event => {
      if (event.target === card && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); selectResult(item.id); }
    });
    list.append(card);
  }
}
function selectResult(id, scroll = false) {
  if (scroll) $('referencePanel').open = true;
  state.selectedId = id;
  for (const card of $('resultList').querySelectorAll('.result-card')) {
    const active = card.dataset.id === String(id);
    card.classList.toggle('selected', active);
    card.setAttribute('aria-current', String(active));
    if (active && scroll) card.scrollIntoView({ block: 'nearest' });
  }
  renderDetail();
}
function renderDetail() {
  const root = $('answerDetail');
  root.replaceChildren();
  const item = selectedResult();
  if (!item) {
    root.append(emptyState('让回答有据可查', state.query ? '没有合适的预设答案时，可以切换到原文检索寻找相关依据。' : '选择左侧答案，查看简短回答、详细说明与原文出处。', 'book', state.query && state.view === 'answers' ? button('检索原文', 'button secondary', 'arrow', () => setView('sources')) : null));
    return;
  }
  if (state.view === 'sources') { renderSourceDetail(item); return; }
  const header = el('div', 'detail-header');
  const meta = el('div', 'detail-meta');
  meta.append(el('span', 'badge', item.category || '未分类'), statusBadge(item.status));
  const title = el('h2'); highlight(title, item.question, item.matched_terms);
  const actions = el('div', 'detail-actions');
  actions.append(
    button('复制回答', 'button secondary', 'copy', () => copyText(item.short_answer || item.answer)),
    button(item.favorite ? '已收藏' : '收藏', 'button secondary' + (item.favorite ? ' favorite-active' : ''), 'star', () => toggleFavorite(item)),
    button('编辑', 'button secondary', 'edit', () => openEditor(item)),
  );
  if (item.status !== 'reviewed') actions.append(button('标记已审核', 'button secondary', 'check', () => markReviewed(item)));
  header.append(meta, title, actions);
  const body = el('div', 'detail-body');
  if (item.short_answer) {
    const heading = el('h3', 'section-kicker'); heading.append(icon('chat'), document.createTextNode('现场速答'));
    const quick = el('div', 'short-answer'); highlight(quick, item.short_answer, item.matched_terms);
    body.append(heading, quick);
  }
  const heading = el('h3', 'section-kicker', item.short_answer ? '详细回答' : '参考回答');
  const full = el('div', 'full-answer'); highlight(full, item.answer || item.short_answer || '', item.matched_terms);
  body.append(heading, full);
  const sourceSection = el('section', 'source-section');
  const sourceHeading = el('h3', 'section-kicker'); sourceHeading.append(icon('file'), document.createTextNode('原文出处'));
  sourceSection.append(sourceHeading);
  const sources = arr(item.sources);
  if (!sources.length) sourceSection.append(el('div', 'no-source', '尚未添加出处，可在编辑答案时补充来源文件、章节和原文摘录。'));
  for (const source of sources) sourceSection.append(sourceCard(source));
  body.append(sourceSection);
  if (item.status !== 'reviewed') body.append(el('p', 'review-notice', '此回答尚未标记为已审核。答辩前请确认数据、单位与项目当前口径。'));
  const footer = el('div', 'detail-footer');
  footer.append(el('span', '', item.updated_at ? '更新于 ' + dateText(item.updated_at) : '本地答案卡片'), button('删除答案', 'text-button danger', 'trash', () => deleteAnswer(item)));
  root.append(header, body, footer);
}
function sourceCard(source) {
  const card = el('div', 'source-card');
  const top = el('div', 'source-topline');
  const doc = state.documents.find(x => String(x.id) === String(source.document_id));
  top.append(icon('file'), el('span', 'source-name', source.filename || (doc && filename(doc)) || '手工来源'));
  if (source.document_id) top.append(button('查看原文', 'source-link', 'external', () => openSource(source)));
  card.append(top);
  const locator = source.locator || (source.page ? 'PDF 第 ' + source.page + ' 页' : '');
  if (locator) card.append(el('p', 'source-locator', locator));
  if (source.quote) card.append(el('p', 'source-quote', source.quote));
  return card;
}
function renderSourceDetail(item) {
  const root = $('answerDetail');
  const header = el('div', 'detail-header');
  const meta = el('div', 'detail-meta'); meta.append(el('span', 'badge', '原文摘录'));
  if (item.locator) meta.append(el('span', 'badge', item.locator));
  const title = el('h2', '', filename(item));
  const actions = el('div', 'detail-actions');
  actions.append(button('复制片段', 'button secondary', 'copy', () => copyText(item.text || '')), button('查看原文', 'button secondary', 'external', () => openSource(item)), button('整理为答案', 'button secondary', 'plus', () => openEditor(null, item)));
  header.append(meta, title, actions);
  const body = el('div', 'detail-body');
  body.append(el('h3', 'section-kicker', '匹配片段'));
  const content = el('div', 'full-answer'); highlight(content, item.text, item.matched_terms); body.append(content);
  body.append(el('p', 'review-notice', '这是材料原文片段，可能包含部分上下文。使用数据前请打开原文确认。'));
  root.append(header, body);
}
function clearFilters() {
  state.query = ''; state.category = ''; state.status = 'all'; state.favorites = false;
  $('searchInput').value = ''; $('categoryFilter').value = ''; $('statusFilter').value = 'all';
  updateNav(); search(); $('searchInput').focus();
}
async function toggleFavorite(item) {
  try {
    const updated = await api('/api/answers/' + encodeURIComponent(item.id) + '/favorite', { method: 'POST', body: { favorite: !item.favorite } });
    Object.assign(item, updated);
    await refreshMeta({ silent: true });
    await search({ keepSelection: true });
  } catch (err) { toast(err.message, true); }
}
async function markReviewed(item) {
  try {
    await api('/api/answers/' + encodeURIComponent(item.id), { method: 'PUT', body: { ...item, status: 'reviewed' } });
    await refreshMeta({ silent: true }); await search({ keepSelection: true }); toast('已标记为审核通过');
  } catch (err) { toast(err.message, true); }
}
async function deleteAnswer(item) {
  if (!confirm('删除这条答案？\n\n' + item.question + '\n\n原始资料文件不会被删除。')) return;
  try {
    await api('/api/answers/' + encodeURIComponent(item.id), { method: 'DELETE' });
    clearLibraryDisplay();
    await refreshMeta({ silent: true }); await search(); toast('答案已删除');
  } catch (err) { toast(err.message, true); }
}
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(String(text));
    else {
      const box = el('textarea'); box.value = String(text); box.style.position = 'fixed'; box.style.opacity = '0';
      document.body.append(box); box.select(); const success = document.execCommand('copy'); box.remove();
      if (!success) throw new Error('请选中文字后使用 Ctrl+C 复制。');
    }
    toast('回答已复制');
  } catch (err) { toast(err.message || '复制失败，请选中文字后按 Ctrl+C。', true); }
}

function openEditor(item = null, fromSource = null) {
  state.editId = item ? item.id : null;
  state.editOriginal = item ? { ...item } : null;
  $('editorTitle').textContent = item ? '编辑答案' : fromSource ? '将原文整理为答案' : '新建答案';
  $('editQuestion').value = item ? item.question || '' : state.query || '';
  $('editCategory').value = item ? item.category || '' : '';
  $('editStatus').value = item ? item.status || 'reference' : 'draft';
  $('editShort').value = item ? item.short_answer || '' : '';
  $('editAnswer').value = item ? item.answer || '' : fromSource ? fromSource.text || '' : '';
  $('editKeywords').value = item ? arr(item.keywords).join('，') : '';
  $('editAliases').value = item ? arr(item.aliases).join('\n') : '';
  $('sourceFields').replaceChildren();
  const sources = item ? arr(item.sources) : fromSource ? [{ document_id: fromSource.document_id, filename: filename(fromSource), locator: fromSource.locator || '', page: fromSource.page, quote: fromSource.text || '' }] : [];
  sources.forEach(addSourceField);
  $('editorError').hidden = true;
  $('saveAnswer').disabled = false;
  $('editorDialog').showModal();
  requestAnimationFrame(() => $('editQuestion').focus());
}
function sourceInput(label, field, value, multiline = false) {
  const wrapper = el('label', 'field', label);
  const input = el(multiline ? 'textarea' : 'input');
  input.dataset.field = field;
  input.value = value == null ? '' : String(value);
  if (multiline) input.rows = 3;
  wrapper.append(input);
  return wrapper;
}
function addSourceField(source = {}) {
  const row = el('div', 'source-field-row');
  row._source = { ...source };
  const remove = button('', 'icon-button remove-source', 'close', () => row.remove());
  remove.title = '移除此出处'; remove.setAttribute('aria-label', '移除此出处');
  const grid = el('div', 'form-grid');
  grid.append(sourceInput('来源文件名', 'filename', source.filename), sourceInput('章节 / 页码位置', 'locator', source.locator || (source.page ? '第 ' + source.page + ' 页' : '')));
  row.append(remove, grid, sourceInput('原文摘录', 'quote', source.quote, true));
  if (source.document_id) row.append(el('small', 'fine-print', '已关联本地原始文件；编辑文字不会解除关联。'));
  $('sourceFields').append(row);
}
async function saveAnswer(event) {
  event.preventDefault();
  const sources = [...$('sourceFields').querySelectorAll('.source-field-row')].map(row => {
    const source = { ...row._source };
    row.querySelectorAll('[data-field]').forEach(input => { source[input.dataset.field] = input.value.trim(); });
    return source;
  }).filter(source => source.filename || source.locator || source.quote || source.document_id);
  const body = {
    question: $('editQuestion').value.trim(), category: $('editCategory').value.trim() || '未分类',
    status: $('editStatus').value, short_answer: $('editShort').value.trim(), answer: $('editAnswer').value.trim(),
    keywords: $('editKeywords').value.split(/[,，;；\n]+/).map(x => x.trim()).filter(Boolean),
    aliases: $('editAliases').value.split(/\n+/).map(x => x.trim()).filter(Boolean),
    sources, favorite: !!(state.editOriginal && state.editOriginal.favorite),
  };
  if (!body.question || !body.answer) { $('editorError').textContent = '请填写问题标题和详细回答。'; $('editorError').hidden = false; return; }
  $('saveAnswer').disabled = true; $('saveAnswer').textContent = '正在保存…'; $('editorError').hidden = true;
  try {
    const editing = state.editId !== null;
    const saved = await api('/api/answers' + (editing ? '/' + encodeURIComponent(state.editId) : ''), { method: editing ? 'PUT' : 'POST', body });
    clearLibraryDisplay();
    $('editorDialog').close();
    $('referencePanel').open = true;
    state.view = 'answers'; state.query = ''; state.category = ''; state.status = 'all'; state.favorites = false;
    $('searchInput').value = ''; $('statusFilter').value = 'all';
    updateNav(); await refreshMeta({ silent: true });
    state.selectedId = saved.id;
    await search({ keepSelection: true });
    if (!state.results.some(x => String(x.id) === String(saved.id))) {
      $('searchInput').value = saved.question;
      state.selectedId = saved.id;
      await search({ keepSelection: true });
    }
    toast(editing ? '答案已更新' : '新答案已保存');
  } catch (err) {
    $('editorError').textContent = err.message;
    $('editorError').hidden = false;
  } finally { $('saveAnswer').disabled = false; $('saveAnswer').textContent = '保存答案'; }
}

function selectedDocuments() {
  return state.documents.filter(doc => state.selectedDocumentIds.has(String(doc.id)));
}
function renderDocumentControls() {
  const count = selectedDocuments().length, total = state.documents.length;
  const busy = state.documentDeleteBusy || state.importBusy;
  $('selectAllDocuments').checked = total > 0 && count === total;
  $('selectAllDocuments').indeterminate = count > 0 && count < total;
  $('selectAllDocuments').disabled = busy || !total;
  $('documentSelectionCount').textContent = '已选 ' + count + ' / ' + total + ' 份';
  $('clearDocumentSelection').disabled = busy || !count;
  $('deleteSelectedDocuments').disabled = busy || !count;
  $('deleteSelectedDocuments').textContent = state.documentDeleteBusy ? '正在删除…' : '删除选中文件';
  $('refreshDocuments').disabled = state.documentDeleteBusy;
  $('importFromDocs').disabled = state.documentDeleteBusy;
  $('importFromSearch').disabled = state.documentDeleteBusy;
  $('documentList').setAttribute('aria-busy', String(state.documentDeleteBusy));
}
function setDocumentSelection(id, selected) {
  if (state.documentDeleteBusy || state.importBusy) return;
  if (selected) state.selectedDocumentIds.add(String(id));
  else state.selectedDocumentIds.delete(String(id));
  renderDocumentControls();
}
function selectAllDocuments(selected) {
  if (state.documentDeleteBusy || state.importBusy) return;
  state.selectedDocumentIds = new Set(selected ? state.documents.map(doc => String(doc.id)) : []);
  renderDocuments();
}
function documentActionStatus(message, error = false) {
  $('documentActionStatus').textContent = message;
  $('documentActionStatus').hidden = !message;
  $('documentActionStatus').classList.toggle('is-error', error);
}
function renderDocuments() {
  const liveIds = new Set(state.documents.map(doc => String(doc.id)));
  state.selectedDocumentIds = new Set([...state.selectedDocumentIds].filter(id => liveIds.has(id)));
  renderDocumentControls();
  const root = $('documentList'); root.replaceChildren();
  if (!state.documents.length) {
    root.append(emptyState('还没有导入资料', '选择 Word、PDF 或答案表格，建立你的本地资料库。', 'folder', button('导入资料', 'button secondary', 'upload', openImport)));
    return;
  }
  for (const doc of state.documents) {
    const row = el('div', 'document-row');
    const select = el('input', 'document-select'); select.type = 'checkbox';
    select.checked = state.selectedDocumentIds.has(String(doc.id));
    select.disabled = state.documentDeleteBusy || state.importBusy;
    select.setAttribute('aria-label', '选择资料 ' + filename(doc));
    row.classList.toggle('is-selected', select.checked);
    select.addEventListener('change', () => {
      setDocumentSelection(doc.id, select.checked);
      row.classList.toggle('is-selected', state.selectedDocumentIds.has(String(doc.id)));
    });
    const symbol = el('div', 'document-icon'); symbol.append(icon('file'));
    const info = el('div', 'document-info'); info.append(el('strong', '', filename(doc)));
    const facts = [];
    const ext = filename(doc).split('.').pop(); if (ext && ext.length < 8) facts.push(ext.toUpperCase());
    if (doc.chunk_count != null) facts.push(doc.chunk_count + ' 个原文片段');
    if (doc.answer_count != null) facts.push(doc.answer_count + ' 条答案');
    if (doc.created_at || doc.imported_at) facts.push('导入于 ' + dateText(doc.created_at || doc.imported_at));
    info.append(el('p', '', facts.join(' · ')));
    const warnings = arr(doc.warnings || doc.warning).filter(Boolean);
    if (warnings.length) info.append(el('div', 'document-warning', warnings.join('；')));
    const actions = el('div', 'document-actions');
    actions.append(button('查看', 'button secondary small', 'external', () => openSource({ document_id: doc.id, filename: filename(doc) })), button('', 'icon-button', 'trash', () => deleteDocument(doc)));
    actions.lastChild.title = '删除资料'; actions.lastChild.setAttribute('aria-label', '删除资料 ' + filename(doc));
    actions.lastChild.disabled = state.documentDeleteBusy || state.importBusy;
    row.append(select, symbol, info, actions); root.append(row);
  }
}
async function deleteDocument(doc) {
  if (state.documentDeleteBusy || state.importBusy) return;
  if (!confirm('删除这份资料及关联答案？\n\n' + filename(doc) + '\n\n将同步删除本应用内的文件副本、原文片段和属于该文件的关联答案。其他独立导入文件的副本和无来源手工答案保留。文件夹中的原始材料不受影响。')) return;
  await performDocumentDeletion([doc], false);
}
async function deleteSelectedDocuments() {
  if (state.documentDeleteBusy || state.importBusy) return;
  // Capture explicit IDs before confirmation. Newly imported files are never
  // included merely because the user previously selected all visible files.
  const docs = selectedDocuments().map(doc => ({ id: String(doc.id), filename: filename(doc) }));
  if (!docs.length) return;
  const list = docs.map((doc, index) => (index + 1) + '. ' + filename(doc)).join('\n');
  if (!confirm('删除选中的 ' + docs.length + ' 份资料及关联答案？\n\n' + list + '\n\n将删除本应用内的文件副本、原文片段和属于这些文件的关联答案。未选文件、其他独立导入副本、无来源手工答案及历史对话保留。文件夹中的原始材料不受影响。')) return;
  await performDocumentDeletion(docs, true);
}
async function performDocumentDeletion(docs, batch) {
  if (state.documentDeleteBusy) return;
  state.documentDeleteBusy = true;
  const ids = docs.map(doc => String(doc.id));
  let accepted = false, metadataRefreshed = false;
  renderDocuments();
  documentActionStatus('正在删除 ' + docs.length + ' 份资料并更新检索…');
  try {
    const result = await api(batch ? '/api/documents/delete-batch' : '/api/documents/' + encodeURIComponent(ids[0]),
      batch ? { method: 'POST', body: { document_ids: ids } } : { method: 'DELETE' });
    accepted = true;
    clearLibraryDisplay();
    await refreshMeta({ silent: true });
    metadataRefreshed = true;
    if (state.view !== 'documents') await search();
    const message = result.message || (Number.isInteger(result.removed_answers) ? '已删除 ' + (result.removed_documents ?? docs.length) + ' 份资料，同时删除 ' + result.removed_answers + ' 条关联答案' : '资料及关联答案已删除');
    const warning = Boolean(result.cleanup_warnings && result.cleanup_warnings.length);
    documentActionStatus(message, warning);
    toast(message, warning);
  } catch (err) {
    let changed = false;
    if (!accepted) {
      // A lost HTTP response does not prove the deletion was rolled back.
      // Reconcile the visible list once; retry remains an explicit user action.
      try {
        await refreshMeta({ silent: true });
        metadataRefreshed = true;
        const live = new Set(state.documents.map(doc => String(doc.id)));
        changed = ids.some(id => !live.has(id));
        if (changed) {
          clearLibraryDisplay({ keepMetadata: true });
          if (state.view !== 'documents') await search();
        }
      } catch (_) {}
    }
    const message = accepted ? '删除已执行，但列表刷新未完成，请点击“刷新列表”核对。' : changed ? '删除请求的回复未收到，已刷新列表；部分所选文件已不存在，请核对剩余资料。' : (err.message || '删除请求未完成') + '。请刷新列表核对后再重试。';
    documentActionStatus(message, true);
    toast(message, true);
  } finally {
    state.documentDeleteBusy = false;
    if (!accepted || metadataRefreshed) renderDocuments();
    else renderDocumentControls();
  }
}
function openImport() {
  if (state.documentDeleteBusy) { toast('资料正在删除，请稍候再导入。'); return; }
  if (!state.importBusy) { $('importResults').replaceChildren(); $('fileInput').value = ''; }
  $('importDialog').showModal();
}
async function importFiles(files) {
  if (state.importBusy || state.documentDeleteBusy || !files.length) return;
  state.importBusy = true;
  renderDocuments();
  $('fileInput').disabled = true;
  $('importKind').disabled = true;
  $('importDone').disabled = true;
  const kind = $('importKind').value;
  const supported = /\.(docx|pdf|xlsx|json|txt|md)$/i;
  let successes = 0;
  for (const file of files) {
    const row = el('div', 'import-result');
    row.append(icon('upload'));
    const info = el('div'); const name = el('strong', '', file.name); const message = el('p', '', '正在导入…');
    info.append(name, message); row.append(info); $('importResults').append(row);
    row.scrollIntoView({ block: 'nearest' });
    try {
      if (!supported.test(file.name)) throw new Error('暂不支持此格式。旧版 .doc 请先另存为 .docx。');
      const body = new FormData(); body.append('file', file); body.append('kind', kind);
      const result = await api('/api/import', { method: 'POST', body });
      const countText = [];
      if (result.answer_count != null) countText.push(result.answer_count + ' 条答案');
      if (result.chunk_count != null) countText.push(result.chunk_count + ' 个原文片段');
      const warnings = arr(result.warnings).filter(Boolean);
      message.textContent = (result.message || '导入完成') + (countText.length ? ' · ' + countText.join('，') : '') + (warnings.length ? '\n' + warnings.join('；') : '');
      message.style.whiteSpace = 'pre-wrap';
      row.replaceChild(icon('check'), row.firstChild);
      if (!successes) clearLibraryDisplay();
      successes += 1;
    } catch (err) {
      row.classList.add('failed'); row.replaceChild(icon('close'), row.firstChild);
      message.textContent = err.message || '导入失败';
    }
  }
  state.importBusy = false;
  $('fileInput').disabled = false; $('importKind').disabled = false; $('importDone').disabled = false;
  $('fileInput').value = '';
  await refreshMeta({ silent: true }).catch(() => renderDocuments());
  if (state.view !== 'documents') await search({ keepSelection: true });
  if (successes) toast('已完成 ' + successes + ' 个文件的导入');
}
async function openSource(source) {
  const id = source.document_id || source.id;
  if (!id) { toast('这个出处尚未关联本地文件，可在答案中查看摘录。'); return; }
  const seq = ++state.previewSeq;
  const doc = state.documents.find(item => String(item.id) === String(id));
  const name = source.filename || (doc && filename(doc)) || '查看原文';
  $('sourceTitle').textContent = name;
  const url = documentURL(id);
  $('sourceOpen').href = url + (source.page ? '#page=' + Number(source.page) : '');
  $('sourcePreview').replaceChildren(el('div', 'loading-note', '正在打开本地原文…'));
  if (!$('sourceDialog').open) $('sourceDialog').showModal();
  if (/\.pdf$/i.test(name)) {
    const frame = el('iframe'); frame.title = name;
    frame.src = url + '#page=' + (source.page || 1) + '&view=FitH';
    $('sourcePreview').replaceChildren(frame);
    return;
  }
  try {
    const data = await api('/api/documents/' + encodeURIComponent(id) + '/preview' + (source.page ? '?page=' + Number(source.page) : ''));
    if (seq !== state.previewSeq) return;
    let pages = Array.isArray(data.pages) ? data.pages : Array.isArray(data.chunks) ? data.chunks : [{ text: data.text || '' }];
    $('sourcePreview').replaceChildren();
    if (!pages.length || !pages.some(page => page.text)) {
      $('sourcePreview').append(emptyState('暂无文字预览', '可以点击右上角“打开文件”，查看原始资料。', 'file'));
      return;
    }
    let focusNode = null;
    const quote = String(source.quote || source.text || '').trim();
    const normalizedQuote = quote.replace(/\s+/g, '');
    if (quote) {
      const quoted = el('section', 'source-text-page source-focus');
      quoted.append(el('h3', '', '当前引用 · ' + (source.locator || '材料摘录')));
      const quoteText = el('pre', '', quote);
      quoted.append(quoteText);
      const jump = button('跳至对应原文', 'text-button', 'arrow', () => { if (focusNode) focusNode.scrollIntoView({ block: 'start', behavior: 'smooth' }); else toast('未找到唯一对应段落，请结合引用摘录与位置核对全文。'); });
      quoted.append(jump);
      $('sourcePreview').append(quoted);
    }
    for (const page of pages) {
      const node = el('section', 'source-text-page');
      node.append(el('h3', '', page.locator || (page.page ? '第 ' + page.page + ' 页' : name)));
      const content = el('pre'); highlight(content, page.text, source.matched_terms || []); node.append(content);
      const normalizedText = String(page.text || '').replace(/\s+/g, '');
      const matchedQuote = normalizedQuote.length >= 12 && normalizedText.includes(normalizedQuote.slice(0, Math.min(45, normalizedQuote.length)));
      if (!focusNode && ((source.locator && page.locator === source.locator) || matchedQuote)) {
        node.dataset.matched = 'true';
        node.classList.add('matched-source');
        focusNode = node;
      }
      $('sourcePreview').append(node);
    }
    if (!quote && focusNode) requestAnimationFrame(() => focusNode.scrollIntoView({ block: 'start' }));
  } catch (err) {
    if (seq === state.previewSeq) $('sourcePreview').replaceChildren(emptyState('暂时无法预览', err.message, 'file'));
  }
}

function bindEvents() {
  document.querySelectorAll('[data-view]').forEach(node => node.addEventListener('click', () => setView(node.dataset.view)));
  $('favoritesNav').addEventListener('click', () => setView('answers', true));
  $('searchInput').addEventListener('input', scheduleSearch);
  $('searchInput').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); search(); if (answerGeneration) answerGeneration.submit(); } });
  $('clearSearch').addEventListener('click', () => { $('searchInput').value = ''; state.query = ''; if (answerGeneration) answerGeneration.onQuestionChanged(); search(); $('searchInput').focus(); });
  $('categoryFilter').addEventListener('change', event => { state.category = event.target.value; search(); });
  $('statusFilter').addEventListener('change', event => { state.status = event.target.value; search(); });
  $('favoriteFilter').addEventListener('click', () => { state.favorites = !state.favorites; updateNav(); search(); });
  $('addAnswer').addEventListener('click', () => openEditor());
  $('answerForm').addEventListener('submit', saveAnswer);
  $('addSourceField').addEventListener('click', () => addSourceField());
  $('importFromSearch').addEventListener('click', openImport);
  $('importFromDocs').addEventListener('click', openImport);
  $('fileInput').addEventListener('change', event => importFiles([...event.target.files]));
  $('dropZone').addEventListener('click', () => { if (!state.importBusy) $('fileInput').click(); });
  $('dropZone').addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (!state.importBusy) $('fileInput').click(); } });
  ['dragenter', 'dragover'].forEach(name => $('dropZone').addEventListener(name, event => { event.preventDefault(); $('dropZone').classList.add('drag-over'); }));
  ['dragleave', 'drop'].forEach(name => $('dropZone').addEventListener(name, event => { event.preventDefault(); $('dropZone').classList.remove('drag-over'); }));
  $('dropZone').addEventListener('drop', event => importFiles([...event.dataTransfer.files]));
  $('refreshDocuments').addEventListener('click', () => refreshMeta().then(() => toast('资料列表已刷新')).catch(() => {}));
  $('selectAllDocuments').addEventListener('change', event => selectAllDocuments(event.target.checked));
  $('clearDocumentSelection').addEventListener('click', () => selectAllDocuments(false));
  $('deleteSelectedDocuments').addEventListener('click', deleteSelectedDocuments);
  document.querySelectorAll('[data-close]').forEach(node => node.addEventListener('click', () => $(node.dataset.close).close()));
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
  });
  $('sourceDialog').addEventListener('close', () => { state.previewSeq += 1; $('sourcePreview').replaceChildren(); });
  $('fontToggle').addEventListener('click', () => {
    const enabled = document.body.classList.toggle('large-font');
    $('fontToggle').classList.toggle('is-active', enabled);
    $('fontToggle').setAttribute('aria-pressed', String(enabled));
    try { localStorage.setItem('defense-large-font', enabled ? '1' : '0'); } catch (_) {}
  });
  $('readingToggle').addEventListener('click', () => {
    const enabled = document.body.classList.toggle('reading-mode');
    $('readingToggle').classList.toggle('is-active', enabled);
    $('readingToggle').setAttribute('aria-pressed', String(enabled));
    toast(enabled ? '已进入专注阅读，再次点击右上角可退出' : '已退出专注阅读');
  });
  document.addEventListener('keydown', event => {
    if (document.querySelector('dialog[open]')) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (state.view === 'documents') setView('answers');
      $('searchInput').focus(); $('searchInput').select();
    }
    if (event.altKey && (event.key === 'ArrowDown' || event.key === 'ArrowUp') && state.view !== 'documents' && state.results.length) {
      event.preventDefault();
      let idx = state.results.findIndex(item => String(item.id) === String(state.selectedId));
      idx = Math.max(0, Math.min(state.results.length - 1, idx + (event.key === 'ArrowDown' ? 1 : -1)));
      selectResult(state.results[idx].id, true);
    }
  });
  document.addEventListener('click', event => {
    document.querySelectorAll('details.export-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  });
}

function initConversationLayout() {
  function sidebar(collapsed) {
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    $('sidebarToggle').setAttribute('aria-expanded', String(!collapsed));
    $('sidebarToggle').setAttribute('aria-label', collapsed ? '展开侧栏' : '收起侧栏');
    $('sidebarToggle').title = collapsed ? '展开侧栏' : '收起侧栏';
  }
  try { sidebar(localStorage.getItem('defense-sidebar-collapsed') === '1'); } catch (_) { sidebar(false); }
  $('sidebarToggle').addEventListener('click', () => {
    const reading = document.body.classList.contains('reading-mode');
    if (reading) {
      document.body.classList.remove('reading-mode');
      $('readingToggle').classList.remove('is-active');
      $('readingToggle').setAttribute('aria-pressed', 'false');
    }
    const collapsed = !reading && !document.body.classList.contains('sidebar-collapsed');
    sidebar(collapsed);
    try { localStorage.setItem('defense-sidebar-collapsed', collapsed ? '1' : '0'); } catch (_) {}
  });
  const height = () => document.documentElement.style.setProperty('--composer-height', Math.ceil($('composerDock').getBoundingClientRect().height) + 'px');
  if (window.ResizeObserver) new ResizeObserver(height).observe($('composerDock'));
  window.addEventListener('resize', height);
  height();
}

function stopInputForConversationClear() {
  clearTimeout(state.timer);
  if (voiceSearch) voiceSearch.cancel('正在清空对话，语音输入已取消。');
}
async function init() {
  bindEvents();
  initConversationLayout();
  answerGeneration = window.initAnswerGeneration({ onOpenSource: openSource, onSearch: search, onClearRequested: stopInputForConversationClear });
  window.initProviderSettings({
    onChanging: () => answerGeneration.cancel('回答方式已切换，历史对话仍保留。'),
    onChanged: () => answerGeneration.refreshStatus(),
  });
  voiceSearch = window.initVoiceSearch({ onTranscript(text) {
    clearTimeout(state.timer);
    $('searchInput').value = text;
    state.query = text;
    answerGeneration.onQuestionChanged();
    if (text.length > 500) { $('clearSearch').hidden = false; return; }
    search();
    answerGeneration.submit();
  } });
  try { if (localStorage.getItem('defense-large-font') === '1') { document.body.classList.add('large-font'); $('fontToggle').classList.add('is-active'); $('fontToggle').setAttribute('aria-pressed', 'true'); } } catch (_) {}
  updateNav();
  $('answerDetail').append(emptyState('答案与依据，一起呈现', '准备好的回答直接展示。选择一条答案，随时回看材料出处。', 'book'));
  await refreshMeta({ silent: true }).catch(() => {});
  await search();
}
init();
