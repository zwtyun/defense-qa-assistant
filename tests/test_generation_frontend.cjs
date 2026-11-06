'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { consumeNDJSON, initAnswerGeneration, renderAnswer } = require('../static/generation.js');
const renderer = require('../static/defense-renderer.js');
const fs = require('node:fs');
const path = require('node:path');
const tick = () => new Promise(resolve => setImmediate(resolve)), encoder = new TextEncoder();
test('same filename and locator in distinct documents retains both original links', async () => {
  const sources = ['draft', 'final'].map(document_id => ({ document_id, filename: '方案.docx', locator: '第5段' }));
  const h = harness(async () => response([{ type: 'sources', sources: [{ sources }] }, { type: 'done', text: answer }]));
  await tick(); h.element('searchInput').value = '两个版本有何不同？'; await h.controller.submit();
  const links = [...h.elements.values()].filter(item => item.tagName === 'BUTTON' && item.textContent === '查看原文');
  assert.equal(links.length, 2); links.forEach(link => link.listeners.click());
  assert.deepEqual(h.opens, sources); h.controller.destroy();
});
function response(events, step = 13) { const bytes = encoder.encode(events.map(item => JSON.stringify(item)).join('\n')); return { ok: true, body: new ReadableStream({ start(controller) { for (let i = 0; i < bytes.length; i += step) controller.enqueue(bytes.slice(i, i + step)); controller.close(); } }) }; }
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
function harness(generate = async () => response([]), initial = { ready: true, status: 'ready' }, history = []) {
  const elements = new Map(), requests = [], copies = [], opens = [], timers = new Map(), searches = [], clearRequests = [], conversationRequests = [];
  let count = 0, timer = 0, status = initial, deletion = { ok: true, status: 200 }, storedHistory = history;
  function element(id) {
    if (!elements.has(id)) { let text = ''; const classes = new Set(); elements.set(id, {
      id, value: '', checked: true, hidden: false, disabled: false, open: false, dataset: {}, children: [], listeners: {}, attributes: {},
      get textContent() { return this.children.length ? this.children.map(child => child.textContent).join('') : text; }, set textContent(value) { text = String(value); this.children = []; },
      set innerHTML(_) { throw new Error('Untrusted output must not become HTML'); },
      classList: { toggle(name, value) { if (value) classes.add(name); else classes.delete(name); }, contains: name => classes.has(name) },
      setAttribute(name, value) { this.attributes[name] = value; }, focus() {}, scrollIntoView() { this.scrolled = true; },
      append(...nodes) { this.children.push(...nodes); }, replaceChildren(...nodes) { text = ''; this.children = nodes; }, addEventListener(name, handler) { this.listeners[name] = handler; },
    }); }
    return elements.get(id);
  }
  const doc = { getElementById: element, createElement(tag) { const item = element('created-' + (++count)); item.tagName = tag.toUpperCase(); return item; }, createTextNode: text => ({ nodeType: 3, textContent: text }), addEventListener() {} };
  const environment = { AbortController, addEventListener() {}, confirm: () => true, setTimeout(fn) { timers.set(++timer, fn); return timer; }, clearTimeout(id) { timers.delete(id); }, navigator: { clipboard: { async writeText(text) { copies.push(text); } } },
    async fetch(url, options = {}) {
      if (url.endsWith('/status')) return { ok: true, json: async () => status };
      if (url === '/api/conversation') {
        conversationRequests.push(options.method || 'GET');
        if (options.method === 'DELETE') { const result = await deletion; if (result.ok) storedHistory = []; return result; }
        const snapshot = storedHistory;
        return { ok: true, json: async () => ({ turns: await snapshot, busy: false }) };
      }
      requests.push(options); return generate(options);
    },
  };
  const controller = initAnswerGeneration({ document: doc, environment, onOpenSource: source => opens.push(source), onSearch: () => searches.push(element('searchInput').value), onClearRequested: () => clearRequests.push(true) });
  return { controller, doc, element, elements, environment, requests, copies, opens, timers, searches, clearRequests, conversationRequests, setStatus(value) { status = value; }, setDelete(value) { deletion = value; }, setHistory(value) { storedHistory = value; } };
}
const answer = '问题1：如何验证误差？\n【一眼看懂】\n直接结论：我们先统一**误差口径**。\n回答顺序：①统一口径 ②独立验证\n【答辩思维逻辑图】\n```mermaid\nflowchart TD\nA["统一误差口径"] --> B["① 相对误差"]\nA --> C["② 独立验证"]\n```\n【现场回答】\n第一，统一口径。\n我们拟先核对**参考值**与**相对误差**。\n第二，独立验证。\n我们会使用独立样本。\n【一句话收束】\n我们以可复核的结果回应精度担忧。\n【依据与提醒｜不朗读】\n材料.docx 第5段；这是计划，不是已有结果。';
const events = (id, question, text = answer) => [{ type: 'turn', turn: { id, question, status: 'generating', sources: [] } }, { type: 'delta', text }, { type: 'done', text, layout: 'defense', model: 'test-model', elapsed_ms: 1000 }];
const opening = { kind: 'local_answer', text: '我们首先明确误差口径，再使用独立样本与参比方法验证；目前这是计划验证的目标，不能表述为已经实现。', sources: [{ document_id: 'opening-doc', filename: '速答材料.docx', locator: '正文第8段' }], elapsed_ms: 30 };

test('a complete local opening is readable and copyable before the first model token, and remains after the full answer', async () => {
  let writer, now = 100; const frames = [];
  const h = harness(async () => ({ ok: true, body: new ReadableStream({ start(controller) { writer = controller; } }) }));
  h.environment.performance = { now: () => now };
  h.environment.requestAnimationFrame = fn => frames.push(fn);
  await tick(); h.element('searchInput').value = '误差如何验证？'; const work = h.controller.submit(); await tick();
  writer.enqueue(encoder.encode(JSON.stringify({ type: 'turn', turn: { id: 'fast', question: '误差如何验证？', opening } }) + '\n'));
  await tick();
  const box = [...h.elements.values()].find(item => item.className === 'conversation-opening');
  const copy = [...h.elements.values()].find(item => item.tagName === 'BUTTON' && item.textContent === '复制开场回答');
  assert.match(box.textContent, /目前这是计划验证的目标/);
  assert.match(box.textContent, /本地答案库提炼/);
  assert.match(h.element('conversationFeed').textContent, /完整回答正在生成/);
  assert.equal(copy.disabled, false);
  await copy.listeners.click(); assert.equal(h.copies[0], opening.text);
  assert.equal(h.element('generationCopy').disabled, true);
  now = 160; frames.shift()(); now = 180; frames.shift()();
  assert.equal(box.dataset.openingDisplayMs, '80.0');
  assert.equal(box.dataset.openingKind, 'local_answer');
  writer.enqueue(encoder.encode(JSON.stringify({ type: 'opening', opening }) + '\n' + JSON.stringify({ type: 'delta', text: answer }) + '\n' + JSON.stringify({ type: 'done', text: answer, model: 'gpt-5.6-luna' }) + '\n'));
  await work;
  assert.match(box.textContent, /目前这是计划验证的目标/);
  assert.equal(box.dataset.openingDisplayMs, '80.0');
  await h.element('generationCopy').listeners.click();
  assert.doesNotMatch(h.copies[1], /目前这是计划验证的目标|速答材料/);
  assert.match(h.copies[1], /我们会使用独立样本/);
  h.controller.destroy();
});

test('opening events persist through an upstream failure and restore without replaying a request', async () => {
  const h = harness(async () => response([{ type: 'turn', turn: { id: 'failed', question: '误差？' } }, { type: 'opening', opening }, { type: 'error', message: '上游服务暂不可用' }]));
  await tick(); h.element('searchInput').value = '误差？'; await h.controller.submit();
  const copy = [...h.elements.values()].find(item => item.tagName === 'BUTTON' && item.textContent === '复制开场回答');
  assert.match(h.element('conversationFeed').textContent, /上游服务暂不可用/);
  assert.match(h.element('conversationFeed').textContent, /速答材料.docx · 正文第8段/);
  assert.equal(copy.disabled, false); await copy.listeners.click(); assert.equal(h.copies[0], opening.text); h.controller.destroy();
  const restored = harness(undefined, undefined, [{ id: 'failed', question: '误差？', opening, text: '', status: 'error' }]);
  await tick(); assert.match(restored.element('conversationFeed').textContent, /目前这是计划验证的目标/);
  const restoredCopy = [...restored.elements.values()].find(item => item.tagName === 'BUTTON' && item.textContent === '复制开场回答');
  await restoredCopy.listeners.click(); assert.equal(restored.copies[0], opening.text); assert.equal(restored.requests.length, 0); restored.controller.destroy();
});

test('stopping while the API is silent retains the opening and its separate copy action', async () => {
  let writer; const h = harness(async () => ({ ok: true, body: new ReadableStream({ start(controller) { writer = controller; } }) }));
  await tick(); h.element('searchInput').value = '误差？'; const work = h.controller.submit(); await tick();
  writer.enqueue(encoder.encode(JSON.stringify({ type: 'opening', opening }) + '\n')); await tick(); h.controller.cancel();
  assert.match(h.element('conversationFeed').textContent, /已停止/);
  const copy = [...h.elements.values()].find(item => item.tagName === 'BUTTON' && item.textContent === '复制开场回答');
  assert.equal(copy.disabled, false); await copy.listeners.click(); assert.equal(h.copies[0], opening.text);
  writer.close(); await work; h.controller.destroy();
});

test('insufficient local evidence is shown as a notice rather than a copyable opening answer', async () => {
  const h = harness(async () => response([{ type: 'opening', opening: { kind: 'insufficient', text: '未找到可直接回答的可靠资料。', sources: [] } }, { type: 'done', text: answer }]));
  await tick(); h.element('searchInput').value = '未知问题'; await h.controller.submit();
  const box = [...h.elements.values()].find(item => item.className === 'conversation-opening');
  assert.match(box.textContent, /暂无可靠速答/); assert.equal(box.classList.contains('opening-notice'), true);
  const copy = [...h.elements.values()].find(item => item.tagName === 'BUTTON' && item.textContent === '复制开场回答');
  assert.equal(copy.disabled, true); await copy.listeners.click(); assert.equal(h.copies.length, 0); h.controller.destroy();
});

test('NDJSON preserves split Chinese UTF-8 and terminal line without newline', async () => {
  const source = [{ type: 'delta', text: '热应力🌡️' }, { type: 'done', text: '完成' }], received = [];
  await consumeNDJSON(response(source, 1), item => received.push(item)); assert.deepEqual(received, source);
});
test('editing retains the previous answer and a second question appends another turn', async () => {
  let n = 0; const h = harness(async options => response(events('turn' + (++n), JSON.parse(options.body).question))); await tick();
  h.element('searchInput').value = '误差怎么验证？'; await h.controller.submit(); const first = h.element('conversationFeed').children[0];
  h.element('searchInput').value = '追问参考值'; h.controller.onQuestionChanged(); assert.equal(h.element('conversationFeed').children[0], first); assert.match(first.textContent, /独立样本/);
  await h.controller.submit(); assert.equal(h.element('conversationFeed').children.length, 2); assert.equal(h.element('conversationFeed').children[0], first); assert.equal(h.element('generationCard').dataset.phase, 'done'); h.controller.destroy();
});
test('refresh restores complete and interrupted records without submitting a new request', async () => {
  const h = harness(undefined, undefined, [{ id: 'a', question: '旧问题', text: answer, status: 'complete' }, { id: 'b', question: '中断问题', text: '部分正文', status: 'interrupted' }]); await tick();
  assert.equal(h.element('conversationFeed').children.length, 2); assert.match(h.element('conversationFeed').textContent, /运行中断.*保留片段/); assert.equal(h.requests.length, 0); h.controller.destroy();
});
test('typing the next question does not abort a stream or erase partial content', async () => {
  let writer; const h = harness(async () => ({ ok: true, body: new ReadableStream({ start(controller) { writer = controller; } }) })); await tick(); h.element('searchInput').value = '原问题'; const work = h.controller.submit(); await tick();
  writer.enqueue(encoder.encode(JSON.stringify({ type: 'delta', text: '问题1：原问题\n【现场回答】\n已显示的回答' }) + '\n')); await tick(); h.element('searchInput').value = '准备下一问'; h.controller.onQuestionChanged();
  assert.equal(h.requests[0].signal.aborted, false); assert.match(h.element('conversationFeed').textContent, /已显示的回答/); writer.enqueue(encoder.encode(JSON.stringify({ type: 'done', text: answer }) + '\n')); await work; assert.equal(h.element('searchInput').value, '准备下一问'); h.controller.destroy();
});
test('copy excludes the diagram, glance outline and nonspoken evidence', async () => {
  const h = harness(async () => response(events('a', '误差如何验证？'))); await tick(); h.element('searchInput').value = '误差如何验证？'; await h.controller.submit(); await h.element('generationCopy').listeners.click();
  assert.match(h.copies[0], /我们拟先核对参考值与相对误差/); assert.match(h.copies[0], /回应精度担忧/); assert.doesNotMatch(h.copies[0], /mermaid|材料.docx|直接结论|\*\*|依据与提醒/); h.controller.destroy();
});

test('spoken copy preserves numbered paragraphs and cannot copy a diagram-only historical result', async () => {
  const text = '问题1：精度\n【现场回答】\n### 第一，说明目标\n我们拟按独立样本验证。\n\n尚未完成现场验收。\n\n### 第二，明确边界\n不能把计划当作已经完成。\n【一句话收束】\n最终以实际验收结果判断。\n【依据与提醒｜不朗读】\n材料第3段。';
  assert.equal(renderer.spokenText(text), '第一，说明目标\n我们拟按独立样本验证。\n\n尚未完成现场验收。\n\n第二，明确边界\n不能把计划当作已经完成。\n\n最终以实际验收结果判断。');
  const graphOnly = '问题1：历史图\n【一眼看懂】\n仅有提纲。\n【答辩思维逻辑图】\n```mermaid\nflowchart TD\nA["验证"] --> B["边界"]\n```\n【依据与提醒｜不朗读】\n尚未生成口述稿。';
  const h = harness(undefined, undefined, [{ id: 'diagram-only', question: '历史图', text: graphOnly, status: 'complete' }]);
  await tick(); assert.equal(h.element('generationCopy').disabled, true); assert.equal(renderer.spokenText(graphOnly), '');
  assert.doesNotMatch(h.element('conversationFeed').textContent, /答辩思维逻辑图|flowchart TD/); h.controller.destroy();
});
test('manual clear keeps records on error and removes them only after server success', async () => {
  const h = harness(undefined, undefined, [{ id: 'a', question: '保留问题', text: answer, status: 'complete' }]); await tick(); h.setDelete({ ok: false, status: 500, json: async () => ({ detail: '保存失败' }) });
  h.element('searchInput').value = '保留草稿';
  await h.controller.clearHistory(); assert.equal(h.element('conversationFeed').children.length, 1); assert.match(h.element('generationProgress').textContent, /保存失败/);
  assert.equal(h.element('searchInput').value, '保留草稿');
  h.setDelete({ ok: true, status: 200 }); await h.controller.clearHistory(); assert.equal(h.element('conversationFeed').children.length, 0); assert.equal(h.element('generationCopy').disabled, true); h.controller.destroy();
});

test('clear removes all saved turns, openings, draft and queued question while leaving library display intact', async () => {
  const h = harness(undefined, { ready: false, status: 'loading' }, [
    { id: 'complete', question: '旧问题', text: answer, opening, status: 'complete' },
    { id: 'partial', question: '停止的旧问题', text: '【现场回答】未完成正文', opening, status: 'cancelled' },
  ]);
  await tick();
  h.element('documentList').textContent = '保留的导入文件.docx';
  h.element('searchInput').value = '排队等待的新问题';
  h.element('clearSearch').hidden = false;
  await h.controller.submit();
  await h.controller.clearHistory();
  assert.equal(h.element('conversationFeed').children.length, 0);
  assert.equal(h.element('searchInput').value, '');
  assert.equal(h.element('clearSearch').hidden, true);
  assert.equal(h.element('generationEmpty').hidden, false);
  assert.equal(h.element('conversationCount').textContent, '现场答辩辅助模式');
  assert.equal(h.element('generationCopy').disabled, true);
  assert.equal(h.element('generationAgain').disabled, true);
  assert.equal(h.element('generationStop').hidden, true);
  assert.equal(h.element('questionSummaryList').textContent, '等待专家提问');
  assert.equal(h.element('documentList').textContent, '保留的导入文件.docx');
  assert.deepEqual(h.searches, ['']);
  assert.equal(h.clearRequests.length, 1);
  h.setStatus({ ready: true }); await h.controller.refreshStatus(); await tick();
  await h.controller.loadHistory();
  assert.equal(h.requests.length, 0);
  assert.equal(h.element('conversationFeed').children.length, 0);
  assert.deepEqual(h.conversationRequests, ['GET', 'DELETE', 'GET']);
  h.controller.destroy();
});

test('clear rejects a stale history recovery that returns after successful deletion', async () => {
  const old = [{ id: 'old', question: '不得复活的问题', text: answer, opening, status: 'complete' }];
  const h = harness(undefined, undefined, old); await tick();
  const waiting = deferred(); h.setHistory(waiting.promise);
  const reading = h.controller.loadHistory(); await tick();
  await h.controller.clearHistory();
  waiting.resolve(old); await reading;
  assert.equal(h.element('conversationFeed').children.length, 0);
  assert.doesNotMatch(h.element('questionSummaryList').textContent, /不得复活/);
  assert.match(h.element('generationProgress').textContent, /对话已清空/);
  h.controller.destroy();
});

test('a submit awaiting initial history cannot send its old question after clear', async () => {
  const waiting = deferred(); const h = harness(undefined, undefined, waiting.promise);
  h.element('searchInput').value = '清空前已点击发送的问题';
  const work = h.controller.submit(); await tick();
  await h.controller.clearHistory();
  waiting.resolve([{ id: 'old', question: '旧历史', text: answer, opening, status: 'complete' }]);
  await work;
  assert.equal(h.requests.length, 0);
  assert.equal(h.element('conversationFeed').children.length, 0);
  assert.equal(h.element('searchInput').value, '');
  assert.equal(h.element('generationCard').dataset.phase, 'idle');
  h.controller.destroy();
});

test('clearing during a streamed answer removes its opening and ignores late deltas and scheduled paints', async () => {
  let writer; const frames = [];
  const h = harness(async () => ({ ok: true, body: new ReadableStream({ start(controller) { writer = controller; } }) }));
  h.environment.requestAnimationFrame = fn => frames.push(fn);
  await tick(); h.element('searchInput').value = '正在回答的问题';
  const work = h.controller.submit(); await tick();
  writer.enqueue(encoder.encode(JSON.stringify({ type: 'turn', turn: { id: 'live', question: '正在回答的问题', opening } }) + '\n' + JSON.stringify({ type: 'delta', text: '【现场回答】未完成片段' }) + '\n'));
  await tick(); assert.match(h.element('conversationFeed').textContent, /目前这是计划/);
  h.element('searchInput').value = '另一个未发送草稿';
  await h.controller.clearHistory();
  assert.equal(h.requests[0].signal.aborted, true);
  writer.enqueue(encoder.encode(JSON.stringify({ type: 'delta', text: '不得迟到复活' }) + '\n'));
  writer.close(); await work;
  while (frames.length) frames.shift()();
  assert.equal(h.element('conversationFeed').children.length, 0);
  assert.equal(h.element('searchInput').value, '');
  assert.match(h.element('generationProgress').textContent, /对话已清空/);
  h.controller.destroy();
});

test('cancelled clear confirmation leaves draft, opening and saved turns unchanged', async () => {
  const h = harness(undefined, undefined, [{ id: 'old', question: '保留的对话', text: answer, opening, status: 'complete' }]);
  await tick(); h.element('searchInput').value = '保留草稿'; h.environment.confirm = () => false;
  await h.controller.clearHistory();
  assert.equal(h.element('conversationFeed').children.length, 1);
  assert.match(h.element('conversationFeed').textContent, /目前这是计划/);
  assert.equal(h.element('searchInput').value, '保留草稿');
  assert.equal(h.clearRequests.length, 0);
  assert.deepEqual(h.conversationRequests, ['GET']);
  h.controller.destroy();
});
test('stopping retains the partial answer and prevents copying it as complete', async () => {
  let writer; const h = harness(async () => ({ ok: true, body: new ReadableStream({ start(controller) { writer = controller; } }) })); await tick(); h.element('searchInput').value = '待停止问题'; const work = h.controller.submit(); await tick();
  writer.enqueue(encoder.encode(JSON.stringify({ type: 'delta', text: '问题1：测试\n【现场回答】\n部分内容' }) + '\n')); await tick(); h.controller.cancel(); assert.equal(h.requests[0].signal.aborted, true); assert.match(h.element('conversationFeed').textContent, /部分内容/); assert.match(h.element('conversationFeed').textContent, /已停止/); assert.equal(h.element('generationCopy').disabled, true);
  writer.close(); await work; h.controller.destroy();
});
test('truncated streams retain incomplete content without claiming completion', async () => {
  const h = harness(async () => response([{ type: 'delta', text: '片段内容' }])); await tick(); h.element('searchInput').value = '测试'; await h.controller.submit(); assert.equal(h.element('generationCard').dataset.phase, 'error'); assert.match(h.element('conversationFeed').textContent, /片段内容/); assert.equal(h.element('generationCopy').disabled, true); h.controller.destroy();
});
test('API selection accompanies the request and destination mismatch is not silently retried', async () => {
  const selection = { provider: 'api', base_url: 'https://example.test/v1', model: 'gpt-5.6-luna', api_format: 'responses', reasoning_effort: 'low' };
  const h = harness(async () => ({ ok: false, json: async () => ({ detail: '配置已变更，请刷新' }) }), { ready: true, provider: 'api', selection }); await tick(); h.element('searchInput').value = '问题'; await h.controller.submit();
  assert.deepEqual(JSON.parse(h.requests[0].body).selection, selection); assert.equal(h.requests.length, 1); assert.match(h.element('generationProgress').textContent, /配置已变更/); h.controller.destroy();
});
test('stale readiness cannot overwrite the newer API selection', async () => {
  const h = harness(); await tick(); const old = deferred(); h.setStatus(old.promise); const reading = h.controller.refreshStatus(); await tick(); h.setStatus({ ready: true, provider: 'api', model: 'chosen-model' }); await h.controller.refreshStatus(); old.resolve({ ready: true, model: 'old-local' }); await reading;
  assert.match(h.element('generationModelStatus').textContent, /chosen-model/); h.controller.destroy();
});
test('a queued voice/text question submits after readiness but editing cancels the queued question', async () => {
  const h = harness(async () => response(events('a', '排队问题')), { ready: false, status: 'loading' }); await tick(); h.element('searchInput').value = '排队问题'; await h.controller.submit(); assert.equal(h.requests.length, 0); h.setStatus({ ready: true }); await h.controller.refreshStatus(); await tick(); assert.equal(h.requests.length, 1); h.controller.destroy();
  const other = harness(undefined, { ready: false, status: 'loading' }); await tick(); other.element('searchInput').value = '旧排队问题'; await other.controller.submit(); other.element('searchInput').value = '改了问题'; other.controller.onQuestionChanged(); other.setStatus({ ready: true }); await other.controller.refreshStatus(); assert.equal(other.requests.length, 0); other.controller.destroy();
});
test('double submit starts only one request', async () => {
  const pending = deferred(); const h = harness(() => pending.promise); await tick(); h.element('searchInput').value = '同一问题'; const one = h.controller.submit(), two = h.controller.submit(); await tick(); assert.equal(h.requests.length, 1); pending.resolve(response(events('a', '同一问题'))); await Promise.all([one, two]); h.controller.destroy();
});
test('changing views or library keeps the full conversation', async () => {
  const h = harness(undefined, undefined, [{ id: 'a', question: '历史', text: answer, status: 'complete' }]); await tick(); h.controller.setView('sources'); h.controller.cancel('资料已更新', true);
  assert.equal(h.element('conversationFeed').children.length, 1); assert.equal(h.element('generationCard').hidden, true); assert.equal(h.element('referencePanel').open, true); h.controller.setView('answers'); assert.equal(h.element('generationCard').hidden, false); h.controller.destroy();
});
test('actual document locations remain available below the response', async () => {
  const source = { document_id: 'doc1', filename: '材料.docx', locator: '第5段' }; const h = harness(async () => response([{ type: 'sources', sources: [{ sources: [source] }] }, { type: 'done', text: answer }])); await tick(); h.element('searchInput').value = '依据？'; await h.controller.submit();
  [...h.elements.values()].find(item => item.tagName === 'BUTTON' && item.textContent === '查看原文').listeners.click(); assert.deepEqual(h.opens, [source]); assert.match(h.element('conversationFeed').textContent, /材料.docx · 第5段/); h.controller.destroy();
});
test('independent questions have separate fixed sections and concise navigation', async () => {
  const both = answer + '\n\n' + answer.replace('问题1：如何验证误差？', '问题2：如何安排预算？'); const h = harness(async () => response(events('a', '精度和预算两个问题', both))); await tick(); h.element('searchInput').value = '精度和预算两个问题'; await h.controller.submit(); assert.equal(h.element('questionSummaryList').children.length, 2); assert.match(h.element('questionSummaryList').textContent, /如何验证误差/); assert.match(h.element('questionSummaryList').textContent, /如何安排预算/); h.controller.destroy();
});
test('HTML remains text and the old three-highlights cap no longer applies', () => {
  const h = harness(), target = h.element('render-test'); renderAnswer(target, '### **标题**\n**一**、**二**、**三**、**四**。 <img src=x onerror=alert(1)>', h.doc);
  assert.equal(target.children[1].children.filter(item => item.tagName === 'STRONG').length, 4); assert.match(target.textContent, /<img src=x onerror=alert\(1\)>/); assert.equal([...h.elements.values()].some(item => ['IMG', 'SCRIPT'].includes(item.tagName)), false); h.controller.destroy();
});
test('question keywords get red-bold markup even without model emphasis', () => {
  const h = harness(), target = h.element('render-test'); renderer.render(target, '问题1：如何验证误差？\n【现场回答】\nLIBS和NIRS需要独立验证。', { document: h.doc, environment: h.environment, question: 'LIBS和NIRS如何验证？' }); const strong = [...h.elements.values()].filter(item => item.tagName === 'STRONG').map(item => item.textContent); assert.ok(strong.includes('LIBS')); assert.ok(strong.includes('NIRS')); h.controller.destroy();
});
test('only compact Mermaid flowcharts are accepted; config, links and HTML are rejected', () => {
  const good = '```mermaid\nflowchart TD\nA["结论"] --> B["① 指标"]\nA --> C["② 验证"]\n```'; assert.equal(renderer.diagramData(good).safe, true); assert.equal(renderer.diagramData(good).labels.length, 3);
  for (const bad of [good.replace('flowchart TD', '%%{init:{securityLevel:"loose"}}%%\nflowchart TD'), good.replace('flowchart TD', 'flowchart TD\nclick A "https://example.test"'), good.replace('结论', '<img src=x>')]) assert.equal(renderer.diagramData(bad).safe, false);
});
test('the fast reading view hides historical diagrams and does not load Mermaid', async () => {
  const h = harness(), target = h.element('render-test'); renderer.render(target, answer, { document: h.doc, environment: h.environment, question: '误差' }); await tick();
  assert.match(target.textContent, /我们会使用独立样本/); assert.doesNotMatch(target.textContent, /答辩思维逻辑图|① 相对误差|flowchart TD|```|A -->/);
  assert.equal([...h.elements.values()].some(item => item.className === 'defense-diagram'), false);
  const page = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
  assert.doesNotMatch(page, /<script[^>]*mermaid/i); assert.doesNotMatch(page, /先看结论和逻辑图/); h.controller.destroy();
});
test('ambiguous transcription is a confirmation card and cannot be copied as a spoken answer', async () => {
  const text = '【需要确认】\n您指的是2%的相对误差，还是2个百分点？';
  const h = harness(async () => response(events('a', '两点误差', text))); await tick(); h.element('searchInput').value = '两点误差'; await h.controller.submit();
  assert.equal(h.element('generationCopy').disabled, true); assert.match(h.element('conversationFeed').textContent, /需要确认问题/); await h.element('generationCopy').listeners.click(); assert.equal(h.copies.length, 0); assert.equal(renderer.spokenText(text), ''); h.controller.destroy();
});
test('an occasional preface does not create an extra numbered question', () => {
  const h = harness(), target = h.element('render-test'); renderer.render(target, '先说明资料范围。\n' + answer, { document: h.doc, environment: h.environment, question: '原始输入' });
  assert.match(target.textContent, /问题1：如何验证误差/); assert.doesNotMatch(target.textContent, /问题2：/); assert.equal(renderer.conciseQuestions('先说明资料范围。\n' + answer, '原始输入').length, 1); h.controller.destroy();
});
test('safe line breaks are normalized and long labels wrap without dropping numbers or units', () => {
  const graph = renderer.diagramData('```mermaid\nflowchart TD\nA["精度目标保持2%并严格区分已经完成与计划开展<br/>预算为24万元"] --> B["独立验证"]\n```');
  assert.equal(graph.safe, true); assert.match(graph.source, /<br\/>/); assert.match(graph.source, /2%/); assert.match(graph.source, /24万元/); assert.equal(renderer.diagramData(graph.source.replace('预算为24万元', '<script>')).safe, false);
});
test('an unexpected Mermaid block inside the spoken section is neither displayed nor copied', async () => {
  const text = '问题1：关系\n【现场回答】\n先说明验证条件。\n```mermaid\nflowchart TD\nA["核心"] --> B["① 验证"]\n```\n再说明结论边界。';
  const h = harness(), target = h.element('render-test'); renderer.render(target, text, { document: h.doc, environment: h.environment }); await tick();
  assert.match(target.textContent, /先说明验证条件/); assert.match(target.textContent, /再说明结论边界/);
  assert.doesNotMatch(target.textContent, /flowchart|核心|```/); assert.doesNotMatch(renderer.spokenText(text), /flowchart|核心|```/); h.controller.destroy();
});
test('a narrow diagram retains a 12px label scale through local overflow instead of page shrinking', () => {
  const size = renderer.diagramMinimumSize('0 0 715 300');
  assert.deepEqual(size, { width: 572, height: 240 });
  assert.ok(15 * size.width / 715 >= 12);
  assert.equal(renderer.diagramMinimumSize('0 0 0 200'), null);
  assert.equal(renderer.diagramMinimumSize('invalid'), null);
});
test('the compact question navigator expands and collapses without removing any questions', async () => {
  const h = harness(undefined, undefined, [{ id: 'a', question: '两个问题', text: answer + '\n' + answer.replace('问题1：如何验证误差？', '问题2：预算如何分工？'), status: 'complete' }]);
  await tick(); const list = h.element('questionSummaryList').textContent;
  h.element('questionSummaryToggle').listeners.click();
  assert.equal(h.element('questionSummary').classList.contains('is-expanded'), true);
  assert.equal(h.element('questionSummaryToggle').attributes['aria-expanded'], 'true');
  h.element('questionSummaryToggle').listeners.click();
  assert.equal(h.element('questionSummary').classList.contains('is-expanded'), false);
  assert.equal(h.element('questionSummaryList').textContent, list);
  h.controller.destroy();
});
test('a later independent question can request confirmation while the earlier answer remains readable and copyable', async () => {
  const text = answer + '\n\n## 问题2：2%误差的含义\n【需要确认】这里的2%是相对误差，还是2个百分点？';
  const h = harness(async () => response([
    { type: 'turn', turn: { id: 'mixed', question: '先讲验证，再问2%是什么意思', status: 'generating' } },
    { type: 'delta', text: answer }, { type: 'delta', text: '\n\n## 问题2：2%误差的含义\n【需要' },
    { type: 'delta', text: '确认】这里的2%是相对误差，还是2个百分点？' }, { type: 'done', text, layout: 'defense' },
  ]));
  await tick(); h.element('searchInput').value = '先讲验证，再问2%是什么意思'; await h.controller.submit();
  assert.equal(h.element('conversationFeed').children.length, 1);
  assert.match(h.element('conversationFeed').textContent, /我们会使用独立样本/);
  assert.match(h.element('conversationFeed').textContent, /问题2：2%误差的含义/);
  assert.equal([...h.elements.values()].filter(item => item.className && item.className.includes('clarification-card')).some(item => item.textContent.includes('还是2个百分点')), true);
  assert.match(h.element('generationProgress').textContent, /部分问题待确认/);
  assert.equal(h.element('generationCopy').disabled, false);
  assert.equal(h.element('generationCopy').textContent, '复制已答口述稿');
  await h.element('generationCopy').listeners.click();
  assert.match(h.copies[0], /我们会使用独立样本/);
  assert.doesNotMatch(h.copies[0], /需要确认|2个百分点|问题2/);
  h.controller.destroy();
  const restored = harness(undefined, undefined, [{ id: 'mixed', question: '先讲验证，再问2%是什么意思', text, status: 'complete' }]);
  await tick(); assert.match(restored.element('conversationFeed').textContent, /部分问题待确认/); assert.equal(restored.element('generationCopy').textContent, '复制已答口述稿'); restored.controller.destroy();
});
