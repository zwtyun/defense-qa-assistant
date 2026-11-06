'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
function harness(route, { confirmResult = true, onConfirm } = {}) {
  const nodes = new Map(), confirmations = [], cancellations = [], voiceCancellations = [];
  function node(id = '') {
    const classes = new Set();
    let text = '';
    return {
      id, children: [], listeners: {}, attributes: {}, style: {}, value: '', open: false, hidden: false, dataset: {},
      get textContent() { return this.children.length ? this.children.map(item => item.textContent || '').join('') : text; },
      set textContent(value) { text = String(value); this.children = []; },
      get lastChild() { return this.children.at(-1); }, get firstChild() { return this.children[0]; },
      append(...items) { this.children.push(...items); }, replaceChildren(...items) { text = ''; this.children = items; },
      replaceChild(next, prior) { this.children[this.children.indexOf(prior)] = next; },
      setAttribute(key, value) { this.attributes[key] = value; },
      removeAttribute(key) { delete this.attributes[key]; if (key === 'href') delete this.href; },
      addEventListener(name, fn) { this.listeners[name] = fn; },
      close() { this.open = false; if (this.listeners.close) this.listeners.close(); },
      showModal() { this.open = true; }, scrollIntoView() {}, focus() {}, remove() {},
      querySelectorAll() { return []; },
      classList: { add(name) { classes.add(name); }, remove(name) { classes.delete(name); }, toggle(name, value) { if (value) classes.add(name); else classes.delete(name); } },
    };
  }
  const element = id => { if (!nodes.has(id)) nodes.set(id, node(id)); return nodes.get(id); };
  const context = vm.createContext({
    document: { getElementById: element, createElement: () => node(), createElementNS: () => node(), createTextNode: text => ({ textContent: text }), querySelectorAll: () => [] },
    window: { setTimeout() {} }, navigator: {}, URLSearchParams, AbortController,
    FormData: class { append() {} }, Option: function (text, value) { return { textContent: text, value }; },
    setTimeout, clearTimeout, requestAnimationFrame: fn => fn(),
    confirm(message) { confirmations.push(message); if (onConfirm) onConfirm(message); return confirmResult; },
    async fetch(url, options) {
      const data = await route(url, options || {});
      return { ok: true, headers: { get: () => 'application/json' }, json: async () => data };
    },
    generation: { cancel: (...args) => cancellations.push(args), onQuestionChanged() {} },
    voice: { cancel: message => voiceCancellations.push(message) },
  });
  // Load the real application functions without starting its browser bootstrap.
  const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8').replace(/\ninit\(\);\s*$/, '');
  vm.runInContext(source + '\nanswerGeneration = generation; voiceSearch = voice; globalThis.app = {state, deleteDocument, deleteSelectedDocuments, renderDocuments, selectedDocuments, setDocumentSelection, selectAllDocuments, search, openSource, refreshMeta, importFiles};', context);
  return { app: context.app, element, nodes, confirmations, cancellations, voiceCancellations };
}

test('overlong draft stays intact without a search request or a false connection error', async () => {
  let requests = 0;
  const h = harness(async () => { requests++; throw new Error('must not request'); });
  const text = '测'.repeat(501);
  h.element('searchInput').value = text;
  h.element('conversationFeed').textContent = '已有问答仍可阅读';
  await h.app.search();
  assert.equal(requests, 0);
  assert.equal(h.element('searchInput').value, text);
  assert.equal(h.element('conversationFeed').textContent, '已有问答仍可阅读');
  assert.match(h.element('resultList').textContent, /超过500字.*完整保留.*精简/);
  assert.doesNotMatch(h.element('answerDetail').textContent, /等待连接|重新启动|无法检索/);
  assert.equal(h.cancellations.length, 0);
  assert.equal(h.element('resultList').attributes['aria-busy'], undefined);
});

test('overlong draft cancels an old search and ignores its late reply, then a shorter query searches normally', async () => {
  const old = deferred();
  let requests = 0;
  const h = harness(async () => ++requests === 1 ? old.promise : { results: [], total: 0 });
  h.element('searchInput').value = '旧问题';
  const searching = h.app.search();
  await tick();
  const oldSignal = h.app.state.searchController.signal;
  h.element('searchInput').value = '新的长问题'.repeat(110);
  await h.app.search();
  const guidance = h.element('resultList').textContent;
  assert.equal(oldSignal.aborted, true);
  assert.equal(requests, 1);
  old.resolve({ results: [{ id: 'stale', question: '迟到的旧结果' }], total: 1 });
  await searching;
  assert.equal(h.element('resultList').textContent, guidance);
  assert.equal(h.element('resultCount').textContent, '待精简');
  h.element('searchInput').value = '新问题';
  await h.app.search();
  assert.equal(requests, 2);
  assert.equal(h.element('resultCount').textContent, '0 条');
  assert.doesNotMatch(h.element('resultList').textContent, /超过500字|迟到的旧结果/);
});

test('deleting the last document clears search/preview, preserves conversation, ignores late replies, then accepts a new import', async () => {
  const oldSearch = deferred(), oldPreview = deferred();
  let deleted = false, imported = false, searchCount = 0;
  const fresh = { id: 'new-answer', question: '新主题', answer: '新材料内容', sources: [] };
  const h = harness(async (url, options) => {
    if (options.method === 'DELETE') { deleted = true; return { removed_answers: 2, removed_chunks: 3 }; }
    if (url === '/api/import') { imported = true; return { answer_count: 1, chunk_count: 1, message: '导入完成' }; }
    if (url.startsWith('/api/documents/old/preview')) return oldPreview.promise;
    if (url.startsWith('/api/search?')) { searchCount++; return searchCount === 1 ? oldSearch.promise : { results: imported ? [fresh] : [], total: imported ? 1 : 0 }; }
    if (url === '/api/health') return { answer_count: imported ? 1 : 0, document_count: imported ? 1 : 0, chunk_count: imported ? 1 : 0 };
    if (url === '/api/answers') return imported ? [fresh] : [];
    if (url === '/api/documents') return imported ? [{ id: 'new', filename: '新答案库.docx', answer_count: 1 }] : [];
    throw new Error('Unexpected ' + url);
  });
  Object.assign(h.app.state, { results: [{ id: 'old-answer', question: '旧答案' }], selectedId: 'old-answer', documents: [{ id: 'old', filename: '旧资料.docx' }] });
  h.element('searchInput').value = '旧主题';
  const searching = h.app.search();
  const previewing = h.app.openSource({ document_id: 'old', filename: '旧资料.docx' });
  await tick();
  const oldSignal = h.app.state.searchController.signal;
  await h.app.deleteDocument({ id: 'old', filename: '旧资料.docx' });
  assert.equal(deleted, true);
  assert.match(h.confirmations[0], /属于该文件的关联答案/);
  assert.match(h.cancellations[0][0], /历史对话仍保留/);
  assert.equal(h.voiceCancellations.length, 1);
  assert.equal(oldSignal.aborted, true);
  assert.equal(h.app.state.results.length, 0);
  assert.equal(h.app.state.answers.length, 0);
  assert.equal(h.app.state.documents.length, 0);
  assert.equal(h.element('sourceDialog').open, false);
  assert.equal(h.element('sourcePreview').textContent, '');
  assert.equal(h.element('sourceOpen').href, undefined);
  oldSearch.resolve({ results: [{ id: 'old-answer', question: '不得回来' }] });
  oldPreview.resolve({ chunks: [{ text: '不得回来' }] });
  await Promise.all([searching, previewing]);
  assert.equal(h.app.state.results.length, 0);
  assert.equal(h.element('sourcePreview').textContent, '');
  assert.equal(h.element('statAnswers').textContent, '0');
  h.element('searchInput').value = '新主题';
  h.element('importKind').value = 'auto';
  await h.app.importFiles([{ name: '新答案库.docx' }]);
  assert.equal(h.app.state.results[0].id, 'new-answer');
  assert.equal(h.app.state.documents[0].id, 'new');
  assert.equal(h.element('statAnswers').textContent, '1');
  assert.equal(h.cancellations.length, 2);
});

test('an earlier metadata refresh cannot restore a deleted library', async () => {
  const old = deferred();
  let stale = true;
  const h = harness(async (url, options) => {
    if (options.method === 'DELETE') return { removed_answers: 1 };
    if (url.startsWith('/api/search?')) return { results: [] };
    if (stale) { await old.promise; return url === '/api/health' ? { answer_count: 1 } : [{ id: 'old', filename: '旧材料', question: '旧答案' }]; }
    return url === '/api/health' ? { answer_count: 0, document_count: 0, chunk_count: 0 } : [];
  });
  const refresh = h.app.refreshMeta();
  await tick();
  stale = false;
  await h.app.deleteDocument({ id: 'old', filename: '旧材料.docx' });
  old.resolve();
  await refresh;
  assert.equal(h.app.state.answers.length, 0);
  assert.equal(h.app.state.documents.length, 0);
  assert.equal(h.element('statAnswers').textContent, '0');
});

test('failed document deletion keeps the currently displayed library intact', async () => {
  const h = harness(async () => { throw new Error('删除未完成'); });
  h.app.state.results = [{ id: 'kept', question: '仍有效' }];
  h.element('sourcePreview').textContent = '仍有效的预览';
  await h.app.deleteDocument({ id: 'kept', filename: '仍有效.docx' });
  assert.equal(h.app.state.results[0].id, 'kept');
  assert.equal(h.element('sourcePreview').textContent, '仍有效的预览');
  assert.equal(h.cancellations.length, 0);
});

test('checkboxes support partial selection, select all, cancel, and metadata refresh removes only stale selections', async () => {
  let docs = [{ id: 'a', filename: '甲.docx' }, { id: 'b', filename: '乙.pdf' }];
  const h = harness(async url => url === '/api/health' ? { document_count: docs.length } : url === '/api/documents' ? docs : []);
  h.app.state.documents = docs;
  h.app.renderDocuments();
  const firstCheckbox = h.element('documentList').children[0].children[0];
  assert.equal(firstCheckbox.type, 'checkbox');
  assert.equal(firstCheckbox.attributes['aria-label'], '选择资料 甲.docx');
  firstCheckbox.checked = true;
  firstCheckbox.listeners.change();
  assert.equal(h.element('documentSelectionCount').textContent, '已选 1 / 2 份');
  assert.equal(h.element('selectAllDocuments').indeterminate, true);
  assert.equal(h.element('deleteSelectedDocuments').disabled, false);
  h.app.selectAllDocuments(true);
  assert.equal(h.element('selectAllDocuments').checked, true);
  assert.equal(h.element('selectAllDocuments').indeterminate, false);
  assert.equal(h.app.selectedDocuments().length, 2);
  docs = [{ id: 'b', filename: '乙.pdf' }, { id: 'c', filename: '新文件.docx' }];
  await h.app.refreshMeta();
  assert.equal(JSON.stringify([...h.app.state.selectedDocumentIds]), '["b"]');
  assert.equal(h.element('documentList').children[1].children[0].checked, false);
  h.app.selectAllDocuments(false);
  assert.equal(h.element('documentSelectionCount').textContent, '已选 0 / 2 份');
  assert.equal(h.element('deleteSelectedDocuments').disabled, true);
});

test('batch deletion confirms once, freezes explicit selected IDs, rejects duplicate submits and preserves newer files', async () => {
  const pending = deferred(), requests = [];
  let docs = [{ id: 'a', filename: '甲.docx' }, { id: 'b', filename: '乙.pdf' }, { id: 'c', filename: '保留.docx' }];
  const h = harness(async (url, options) => {
    requests.push({ url, options });
    if (url === '/api/documents/delete-batch') {
      await pending.promise;
      docs = [{ id: 'c', filename: '保留.docx' }, { id: 'new', filename: '刚导入.docx' }];
      return { removed_documents: 2, removed_answers: 4, message: '已删除2份资料及4条关联答案。' };
    }
    if (url === '/api/health') return { document_count: docs.length, answer_count: 0 };
    if (url === '/api/documents') return docs;
    if (url === '/api/answers') return [];
    throw new Error('Unexpected ' + url);
  });
  Object.assign(h.app.state, { view: 'documents', documents: docs });
  h.app.setDocumentSelection('a', true); h.app.setDocumentSelection('b', true);
  const deleting = h.app.deleteSelectedDocuments();
  await tick();
  assert.equal(h.confirmations.length, 1);
  assert.match(h.confirmations[0], /2 份资料/);
  assert.match(h.confirmations[0], /甲\.docx\n2\. 乙\.pdf/);
  assert.doesNotMatch(h.confirmations[0], /保留\.docx/);
  assert.match(h.confirmations[0], /历史对话保留/);
  assert.deepEqual(JSON.parse(requests[0].options.body), { document_ids: ['a', 'b'] });
  assert.equal(requests[0].options.method, 'POST');
  assert.equal(h.element('selectAllDocuments').disabled, true);
  assert.equal(h.element('deleteSelectedDocuments').disabled, true);
  await h.app.deleteSelectedDocuments();
  await h.app.deleteDocument(docs[2]);
  assert.equal(h.confirmations.length, 1);
  assert.equal(requests.length, 1);
  pending.resolve(); await deleting;
  assert.equal(JSON.stringify(h.app.state.documents.map(doc => doc.id)), '["c","new"]');
  assert.equal(h.app.state.selectedDocumentIds.size, 0);
  assert.equal(h.element('documentSelectionCount').textContent, '已选 0 / 2 份');
  assert.equal(h.element('documentActionStatus').textContent, '已删除2份资料及4条关联答案。');
  assert.equal(requests.some(item => item.url === '/api/conversation'), false);
  assert.equal(h.cancellations.length, 1);
});

test('cancelled batch confirmation keeps selection and performs no requests', async () => {
  let requestCount = 0;
  const h = harness(async () => { requestCount++; }, { confirmResult: false });
  h.app.state.documents = [{ id: 'a', filename: '甲.docx' }];
  h.app.selectAllDocuments(true);
  await h.app.deleteSelectedDocuments();
  assert.equal(requestCount, 0);
  assert.equal(h.app.state.documentDeleteBusy, false);
  assert.equal(h.app.selectedDocuments().length, 1);
  assert.equal(h.element('deleteSelectedDocuments').disabled, false);
});

test('all selected files can be deleted and leave an empty library without stale selected IDs', async () => {
  const requests = [];
  const h = harness(async (url, options) => {
    requests.push({ url, options });
    if (url === '/api/documents/delete-batch') return { removed_documents: 2, removed_answers: 3, message: '已删除所选全部资料。' };
    return url === '/api/health' ? { document_count: 0, answer_count: 0, chunk_count: 0 } : [];
  });
  Object.assign(h.app.state, { view: 'documents', documents: [{ id: 'a', filename: '甲.docx' }, { id: 'b', filename: '乙.pdf' }] });
  h.app.selectAllDocuments(true);
  await h.app.deleteSelectedDocuments();
  assert.deepEqual(JSON.parse(requests[0].options.body), { document_ids: ['a', 'b'] });
  assert.equal(h.app.state.documents.length, 0);
  assert.equal(h.app.state.selectedDocumentIds.size, 0);
  assert.equal(h.element('selectAllDocuments').checked, false);
  assert.equal(h.element('selectAllDocuments').disabled, true);
  assert.equal(h.element('deleteSelectedDocuments').disabled, true);
  assert.match(h.element('documentList').textContent, /还没有导入资料/);
});

test('lost batch response reconciles committed deletion and clears stale preview without retrying mutation', async () => {
  let deletes = 0;
  const h = harness(async url => {
    if (url === '/api/documents/delete-batch') { deletes++; throw new Error('网络中断'); }
    if (url === '/api/health') return { document_count: 1, answer_count: 0 };
    if (url === '/api/documents') return [{ id: 'b', filename: '保留.docx' }];
    if (url === '/api/answers') return [];
    throw new Error('Unexpected ' + url);
  });
  Object.assign(h.app.state, { view: 'documents', documents: [{ id: 'a', filename: '已删.docx' }, { id: 'b', filename: '保留.docx' }], results: [{ id: 'old', question: '旧答案' }] });
  h.element('sourcePreview').textContent = '已删文件的预览'; h.element('sourceDialog').open = true;
  h.app.setDocumentSelection('a', true);
  await h.app.deleteSelectedDocuments();
  assert.equal(deletes, 1);
  assert.equal(h.app.state.documents[0].id, 'b');
  assert.equal(h.app.state.results.length, 0);
  assert.equal(h.element('sourcePreview').textContent, '');
  assert.equal(h.element('sourceDialog').open, false);
  assert.equal(h.app.state.selectedDocumentIds.size, 0);
  assert.match(h.element('documentActionStatus').textContent, /已刷新列表/);
  assert.equal(h.cancellations.length, 1);
});

test('rejected batch deletion keeps live selections and displays backend error after reconciliation', async () => {
  const docs = [{ id: 'a', filename: '保留.docx' }];
  const h = harness(async url => {
    if (url === '/api/documents/delete-batch') throw new Error('资料暂时不可写');
    return url === '/api/health' ? { document_count: 1 } : url === '/api/documents' ? docs : [];
  });
  Object.assign(h.app.state, { view: 'documents', documents: docs, results: [{ id: 'still-live' }] });
  h.app.selectAllDocuments(true);
  await h.app.deleteSelectedDocuments();
  assert.equal(h.app.selectedDocuments().length, 1);
  assert.equal(h.app.state.results[0].id, 'still-live');
  assert.equal(h.cancellations.length, 0);
  assert.equal(h.element('deleteSelectedDocuments').disabled, false);
  assert.match(h.element('documentActionStatus').textContent, /资料暂时不可写/);
});

test('a successful deletion with an occupied-copy warning remains visibly qualified', async () => {
  const warning = '已删除1份资料的检索记录；1份应用副本被占用，未清理成功。';
  const h = harness(async url => {
    if (url === '/api/documents/delete-batch') return { removed_documents: 1, removed_answers: 1, cleanup_warnings: ['被占用.docx'], message: warning };
    return url === '/api/health' ? { document_count: 0 } : [];
  });
  Object.assign(h.app.state, { view: 'documents', documents: [{ id: 'a', filename: '被占用.docx' }] });
  h.app.selectAllDocuments(true);
  await h.app.deleteSelectedDocuments();
  assert.equal(h.element('documentActionStatus').textContent, warning);
  assert.equal(h.element('documentActionStatus').hidden, false);
  assert.equal(h.element('toastRegion').lastChild.textContent, warning);
  assert.equal(h.app.state.documents.length, 0);
});

test('metadata failure after accepted deletion reports refresh failure and never restores deleted previews', async () => {
  let deletes = 0;
  const h = harness(async url => {
    if (url === '/api/documents/delete-batch') { deletes++; return { removed_documents: 1 }; }
    throw new Error('无法刷新');
  });
  Object.assign(h.app.state, { view: 'documents', documents: [{ id: 'a', filename: '已删.docx' }], results: [{ id: 'old' }] });
  h.element('sourcePreview').textContent = '旧预览';
  h.app.selectAllDocuments(true);
  await h.app.deleteSelectedDocuments();
  assert.equal(deletes, 1);
  assert.equal(h.app.state.results.length, 0);
  assert.equal(h.element('sourcePreview').textContent, '');
  assert.match(h.element('documentActionStatus').textContent, /删除已执行.*列表刷新未完成/);
  assert.equal(h.element('refreshDocuments').disabled, false);
  assert.equal(h.element('deleteSelectedDocuments').disabled, true);
  assert.equal(h.element('documentList').textContent, '');
});
