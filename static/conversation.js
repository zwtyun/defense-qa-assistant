'use strict';
(function (root) {
  const renderer = typeof module !== 'undefined' && module.exports ? require('./defense-renderer.js') : root.DefenseRenderer;
  function initAnswerGeneration({ onOpenSource = () => {}, onSearch = () => {}, onClearRequested = () => {}, document: doc = root.document, environment = root, consumeNDJSON } = {}) {
    const $ = id => doc.getElementById(id), input = $('searchInput'), card = $('generationCard'), enabled = $('generationEnabled');
    const feed = $('conversationFeed'), progress = $('generationProgress');
    let current = null, selected = null, pendingQuestion = '', pendingStartedAt = null, readiness = null, statusTimer = null, statusController = null, statusSequence = 0;
    let phase = 'idle', view = 'answers', favorites = false, destroyed = false, clearing = false, serial = 0, historyLoaded = false, historyEpoch = 0;
    const records = new Map();
    const question = () => String(input.value || '').trim();
    const active = () => view === 'answers' && !favorites && enabled.checked;
    const isCurrent = job => !destroyed && current === job && !job.cancelled;
    const clock = () => environment.performance && typeof environment.performance.now === 'function' ? environment.performance.now() : Date.now();
    const node = (tag, className, text) => { const result = doc.createElement(tag); if (className) result.className = className; if (text != null) result.textContent = text; return result; };
    function setProgress(text, error = false) { progress.textContent = text; progress.classList.toggle('generation-error', error); }
    function selectedRecord() { return selected && records.get(selected); }
    function setPhase(next) {
      phase = next; card.dataset.phase = next;
      const busy = ['waiting', 'generating'].includes(next);
      card.setAttribute('aria-busy', String(busy));
      $('generationStop').hidden = !busy;
      $('generateAnswer').disabled = busy || !enabled.checked || clearing;
      $('generateAnswer').textContent = next === 'waiting' ? '等待模型…' : next === 'generating' ? '正在回答…' : '发送问题';
      $('generationAgain').disabled = busy || clearing || !selectedRecord() || !enabled.checked;
      $('generationCopy').disabled = !selectedRecord() || selectedRecord().data.status !== 'complete' || !renderer.spokenText(selectedRecord().data.text);
      $('generationCopy').textContent = selectedRecord() && renderer.hasClarification(selectedRecord().data.text) && renderer.spokenText(selectedRecord().data.text) ? '复制已答口述稿' : '复制口述稿';
      $('clearConversation').disabled = clearing || (!records.size && !busy);
      $('conversationCount').textContent = records.size ? records.size + ' 轮对话' : '现场答辩辅助模式';
      $('generationEmpty').hidden = records.size > 0;
    }
    function renderQuestions(record) {
      const titles = renderer.conciseQuestions(record ? record.data.text : '', record ? record.data.question : question());
      $('questionSummaryList').replaceChildren(...titles.map((title, index) => {
        const item = node('li', 'question-summary-item');
        const button = node('button', '', (titles.length > 1 ? (index + 1) + '. ' : '') + title); button.type = 'button';
        button.addEventListener('click', () => {
          if (!record) return;
          record.element.open = true;
          const section = doc.getElementById(record.element.id + '-question-' + index) || record.element;
          if (section.scrollIntoView) section.scrollIntoView({ block: 'start', behavior: 'smooth' });
        });
        item.append(button); return item;
      }));
      $('questionSummaryTitle').textContent = record ? '本轮要回答的问题' : '问题提要';
    }
    function select(record) {
      if (!record) return;
      selected = record.data.id;
      for (const item of records.values()) item.element.classList.toggle('is-selected', item === record);
      renderQuestions(record); setPhase(phase);
    }
    function sourceRows(target, sources) {
      target.replaceChildren();
      const seen = new Set();
      for (const item of Array.isArray(sources) ? sources : []) {
        for (const source of Array.isArray(item.sources) ? item.sources : item.filename ? [item] : []) {
          const label = [source.filename, source.locator].filter(Boolean).join(' · ');
          const key = [source.document_id || source.id || '', label].join('|');
          if (!label || seen.has(key)) continue;
          seen.add(key);
          const row = node('div', 'conversation-source-location'); row.append(node('span', '', label));
          if (source.document_id || source.id) {
            const button = node('button', 'text-button', '查看原文'); button.type = 'button';
            button.addEventListener('click', () => onOpenSource(source)); row.append(button);
          }
          target.append(row);
        }
      }
      if (!seen.size) target.append(node('p', 'muted', '本轮未检索到直接相关原文。资料未找到不等于项目没有。'));
    }
    function paintOpening(record) {
      const opening = record.data.opening || {}, text = String(opening.text || '').trim();
      record.opening.hidden = !text;
      if (!text) return;
      record.opening.dataset.openingKind = opening.kind || '';
      const notice = opening.kind === 'insufficient' || opening.kind === 'clarification';
      record.opening.classList.toggle('opening-notice', notice);
      record.openingTitle.textContent = opening.kind === 'insufficient' ? '暂无可靠速答' : opening.kind === 'clarification' ? '先确认问题' : '先这样回答';
      record.openingOrigin.textContent = { local_answer: '本地答案库提炼', source_excerpt: '本地原文摘录', insufficient: '本地检索结果', clarification: '需要确认口径' }[opening.kind] || '本地资料速答';
      record.openingText.replaceChildren();
      renderer.inline(record.openingText, text, doc, renderer.questionKeywords(record.data.question));
      record.openingCopy.disabled = opening.kind === 'insufficient';
      record.openingCopy.textContent = opening.kind === 'clarification' ? '复制确认问句' : '复制开场回答';
      record.openingEvidence.hidden = !Array.isArray(opening.sources) || !opening.sources.length;
      if (!record.openingEvidence.hidden) sourceRows(record.openingSources, opening.sources);
    }
    function markOpeningDisplayed(record, job) {
      if (job.openingMeasured || !record.data.opening || !record.data.opening.text) return;
      job.openingMeasured = true;
      const measure = () => {
        if (destroyed || records.get(record.data.id) !== record || record.opening.hidden) return;
        record.opening.dataset.openingDisplayMs = Math.max(0, clock() - job.startedAt).toFixed(1);
      };
      // A second frame allows the newly mounted opening text to be painted first.
      // This stays in the page for local acceptance checks; it is not telemetry.
      if (environment.requestAnimationFrame) environment.requestAnimationFrame(() => environment.requestAnimationFrame(measure));
      else measure();
    }
    function paint(record, content = true) {
      const data = record.data;
      record.element.dataset.status = data.status;
      record.title.textContent = data.question;
      const label = { generating: '正在回答', complete: '已完成', cancelled: '已停止 · 保留片段', error: '未完成', interrupted: '运行中断 · 保留片段' }[data.status] || data.status;
      const needsConfirmation = renderer.hasClarification(data.text), spoken = renderer.spokenText(data.text);
      record.status.textContent = [data.status === 'complete' && needsConfirmation ? (spoken ? '部分问题待确认' : '需要确认问题') : label, data.model || ''].filter(Boolean).join(' · ');
      record.error.textContent = data.error || '';
      record.error.hidden = !data.error;
      record.copy.disabled = data.status !== 'complete' || !spoken;
      record.copy.textContent = needsConfirmation && spoken ? '复制已答口述稿' : '复制口述稿';
      paintOpening(record);
      if (content) {
        if (data.text) renderer.render(record.body, data.text, { document: doc, environment, question: data.question, id: record.element.id });
        else record.body.replaceChildren(node('p', 'conversation-waiting', data.status === 'generating' ? (data.opening && data.opening.text ? '完整回答正在生成…' : '正在检索本地资料…') : '本轮尚未生成完整正文。'));
        if (data.overview && !data.text) record.body.replaceChildren(node('p', 'conversation-waiting', data.overview));
      }
      if (selected === data.id) renderQuestions(record);
      setPhase(phase);
    }
    function schedulePaint(record) {
      if (!environment.requestAnimationFrame) { paint(record); return; }
      if (record.scheduled) return;
      record.scheduled = true;
      environment.requestAnimationFrame(() => { record.scheduled = false; if (!destroyed && records.get(record.data.id) === record) paint(record); });
    }
    async function copy(record) {
      if (!record || record.data.status !== 'complete') return;
      const spoken = renderer.spokenText(record.data.text); if (!spoken) return;
      try { await environment.navigator.clipboard.writeText(spoken); setProgress(renderer.hasClarification(record.data.text) ? '已复制已答问题的口述稿，不含待确认问题、开场参考和内部提醒。' : '现场回答与一句话收束已复制，不含开场参考和内部提醒。'); }
      catch (_) { setProgress('未能复制，请选中口述稿文字复制。', true); }
    }
    async function copyOpening(record) {
      const opening = record && record.data.opening;
      if (!opening || !opening.text || opening.kind === 'insufficient') return;
      try { await environment.navigator.clipboard.writeText(String(opening.text).replace(/\*\*/g, '').trim()); setProgress(opening.kind === 'clarification' ? '已复制确认问句。' : '已复制开场回答，不含出处。'); }
      catch (_) { setProgress('未能复制，请选中开场回答文字复制。', true); }
    }
    function addRecord(data, focus = true) {
      const existing = records.get(data.id);
      if (existing) { existing.data = { ...existing.data, ...data }; sourceRows(existing.sources, existing.data.sources); paint(existing); if (focus) select(existing); return existing; }
      const element = node('details', 'conversation-turn'); element.open = true; element.id = 'turn-' + String(data.id).replace(/[^A-Za-z0-9_-]/g, '');
      const summary = node('summary', 'conversation-turn-header');
      const title = node('span', 'conversation-user-question'), status = node('span', 'conversation-turn-status');
      summary.append(title, status); element.append(summary);
      const opening = node('section', 'conversation-opening'), openingHeading = node('div', 'conversation-opening-heading');
      const openingTitle = node('h3', '', '先这样回答'), openingOrigin = node('span', 'conversation-opening-origin');
      const openingCopy = node('button', 'button secondary small', '复制开场回答'); openingCopy.type = 'button';
      openingHeading.append(openingTitle, openingOrigin, openingCopy);
      const openingText = node('p', 'conversation-opening-text');
      const openingEvidence = node('details', 'conversation-opening-evidence'), openingSources = node('div', 'conversation-sources');
      openingEvidence.append(node('summary', '', '开场依据｜不朗读'), openingSources);
      opening.append(openingHeading, openingText, openingEvidence);
      const body = node('div', 'generated-answer conversation-answer'), error = node('p', 'generation-error conversation-error');
      const actions = node('div', 'conversation-turn-actions');
      const copyButton = node('button', 'button secondary small', '复制口述稿'); copyButton.type = 'button';
      const again = node('button', 'text-button', '以此问题继续 / 重答'); again.type = 'button';
      actions.append(copyButton, again);
      const evidence = node('details', 'conversation-evidence'); const evidenceTitle = node('summary', '', '核对原文位置｜不朗读');
      const sources = node('div', 'conversation-sources'); evidence.append(evidenceTitle, sources);
      element.append(opening, body, error, evidence, actions); feed.append(element);
      const record = { data: { text: '', overview: '', status: 'generating', sources: [], layout: 'defense', ...data }, element, title, status, body, error, copy: copyButton, sources, opening, openingTitle, openingOrigin, openingCopy, openingText, openingEvidence, openingSources };
      records.set(data.id, record);
      summary.addEventListener('click', () => select(record));
      body.addEventListener('click', () => select(record));
      opening.addEventListener('click', () => select(record));
      openingCopy.addEventListener('click', () => copyOpening(record));
      copyButton.addEventListener('click', () => copy(record));
      again.addEventListener('click', () => { select(record); input.value = record.data.question; input.focus(); onSearch(); });
      sourceRows(sources, record.data.sources); paint(record);
      if (focus) select(record);
      return record;
    }
    async function loadHistory() {
      const epoch = historyEpoch;
      try {
        const response = await environment.fetch('/api/conversation');
        if (!response.ok) throw new Error('未能恢复对话记录，请确认已打开新版程序。');
        const data = await response.json();
        if (destroyed || current || clearing || epoch !== historyEpoch) return;
        for (const item of Array.isArray(data.turns) ? data.turns : []) addRecord(item, false);
        const last = [...records.values()].pop(); if (last) select(last);
        historyLoaded = true;
        setProgress(records.size ? '已恢复本机对话。继续提问会参考相关前文；记录仅在手动清空后删除。' : '对话会自动保存在本机，刷新或重启后可继续。');
      } catch (error) { if (!destroyed && !clearing && epoch === historyEpoch) setProgress(error.message || '未能读取本机对话。', true); }
      finally { setPhase(phase); }
    }
    const historyReady = loadHistory();
    function cancel(message = '已停止生成，当前片段已保留。') {
      const hadWork = !!current || !!pendingQuestion; pendingQuestion = ''; pendingStartedAt = null;
      if (current) {
        const job = current; current = null; job.cancelled = true; job.controller.abort();
        if (job.record && !job.done) { job.record.data.status = 'cancelled'; paint(job.record, false); }
      }
      if (hadWork) { setPhase('stopped'); if (message) setProgress(message); }
    }
    function onQuestionChanged() {
      // Typing another question must never erase or abort the previous answer.
      if (!current && !selectedRecord()) renderQuestions(null);
      if (pendingQuestion && pendingQuestion !== question()) { pendingQuestion = ''; pendingStartedAt = null; setPhase('idle'); }
    }
    async function clearHistory() {
      if (clearing || destroyed) return;
      if (environment.confirm && !environment.confirm('清空全部对话记录？导入的资料和答案库不会被删除。')) return;
      clearing = true; historyEpoch += 1;
      cancel('正在停止并清空对话…'); onClearRequested(); setPhase('idle');
      try {
        let response;
        for (let attempt = 0; attempt < 30; attempt++) {
          response = await environment.fetch('/api/conversation', { method: 'DELETE' });
          if (response.status !== 409) break;
          await new Promise(resolve => environment.setTimeout(resolve, 100));
        }
        if (!response.ok) { let result; try { result = await response.json(); } catch (_) {} throw new Error(result && result.detail || '当前任务尚未停止，请稍后再次清空。'); }
        records.clear(); feed.replaceChildren(); selected = null; historyLoaded = true;
        input.value = ''; $('clearSearch').hidden = true;
        $('questionSummary').classList.toggle('is-expanded', false);
        $('questionSummaryToggle').setAttribute('aria-expanded', 'false');
        $('questionSummaryToggle').textContent = '展开';
        renderQuestions(null); onSearch();
        setProgress('对话已清空。后续回答将不再引用已清除的对话。');
      } catch (error) { setProgress(error.message || '未能清空，对话仍保留。', true); }
      finally { clearing = false; setPhase('idle'); }
    }
    async function submit({ scroll = true, text, startedAt = clock() } = {}) {
      if (!active() || destroyed || clearing) return;
      if (current) { setProgress('当前回答仍在生成，可先停止后发送下一题。'); return; }
      const value = typeof text === 'string' ? text : question();
      if (!value) { setProgress('请先输入或说出专家的问题。'); input.focus(); return; }
      if (value.length > 500) { setProgress('问题请控制在500字以内，可分几轮提问。', true); return; }
      const epoch = historyEpoch;
      await historyReady;
      if (epoch !== historyEpoch) return;
      if (!historyLoaded) { await loadHistory(); if (!historyLoaded) return; }
      if (current || clearing || destroyed || epoch !== historyEpoch) return;
      if (!readiness || !readiness.ready) {
        if (!readiness || readiness.status === 'loading') { pendingQuestion = value; pendingStartedAt = startedAt; setPhase('waiting'); setProgress('回答模型正在准备，就绪后将回答这个问题。'); }
        else { setPhase('error'); setProgress(readiness.message || '回答模型暂不可用，请先检索已有答案。', true); }
        return;
      }
      const job = { question: value, startedAt, controller: new environment.AbortController(), cancelled: false, done: false, record: null, sources: [] };
      current = job; pendingQuestion = ''; pendingStartedAt = null; setPhase('generating'); setProgress('先查本地资料给出开场回答，再生成完整口述稿…');
      $('referencePanel').open = false;
      const ensureRecord = data => {
        if (!job.record) {
          job.record = addRecord({ id: data && data.id || 'pending-' + (++serial), question: value, ...data });
          if (scroll && job.record.element.scrollIntoView) job.record.element.scrollIntoView({ block: 'start', behavior: 'auto' });
        }
        return job.record;
      };
      try {
        const response = await environment.fetch('/api/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: value, length: 'detailed', selection: readiness.selection || { provider: readiness.provider || 'local' } }), signal: job.controller.signal });
        if (!isCurrent(job)) return;
        if (!response.ok) { let result; try { result = await response.json(); } catch (_) {} throw new Error(result && typeof result.detail === 'string' ? result.detail : '未能开始回答，请稍后重试。'); }
        await consumeNDJSON(response, event => {
          if (!isCurrent(job)) return;
          if (event.type === 'turn') { const record = ensureRecord(event.turn); markOpeningDisplayed(record, job); }
          else if (event.type === 'opening') {
            const record = ensureRecord(); record.data.opening = event.opening || null; paint(record);
            markOpeningDisplayed(record, job);
            const kind = record.data.opening && record.data.opening.kind;
            setProgress(kind === 'insufficient' ? '本地未找到可靠开场回答，正在按资料生成完整回答。' : kind === 'clarification' ? '请先核对开场卡中的问题口径。' : '开场回答已就绪，可先开口；完整回答继续生成。');
          }
          else if (event.type === 'sources') {
            const record = ensureRecord(); record.data.sources = Array.isArray(event.sources) ? event.sources : []; sourceRows(record.sources, record.data.sources);
            if (!record.data.opening) setProgress('正在根据资料组织现场回答…');
          } else if (event.type === 'overview_delta') { const record = ensureRecord(); record.data.overview += String(event.text || ''); schedulePaint(record); }
          else if (event.type === 'delta') { const record = ensureRecord(); record.data.text += String(event.text || ''); schedulePaint(record); }
          else if (event.type === 'answer_reset') { const record = ensureRecord(); record.data.text = ''; paint(record); }
          else if (event.type === 'error') throw new Error(event.message || '回答未能完成，请重试。');
          else if (event.type === 'done') {
            const record = ensureRecord(); record.data = { ...record.data, text: typeof event.text === 'string' ? event.text : record.data.text, overview: event.overview || '', status: 'complete', model: event.model || '', provider: event.provider || '', layout: event.layout || 'defense' };
            if (event.opening) record.data.opening = event.opening;
            if (!record.data.text.trim()) throw new Error('没有生成有效回答，请重新提问。');
            job.done = true; paint(record); setPhase('done');
            const seconds = Number(event.elapsed_ms) / 1000;
            setProgress((renderer.hasClarification(record.data.text) ? (renderer.spokenText(record.data.text) ? '部分问题待确认；已答内容已保存，可复制已答口述稿。' : '请补充确认后继续，本轮确认问题已保存。') : '回答已保存，可继续追问。') + (Number.isFinite(seconds) ? ' 本次生成' + seconds.toFixed(1) + '秒。' : ''));
          }
        }, () => isCurrent(job));
      } catch (error) {
        if (!isCurrent(job) || error.name === 'AbortError') return;
        if (job.record) { job.record.data.status = 'error'; job.record.data.error = error.message || '回答未完成'; paint(job.record, false); }
        setPhase('error'); setProgress(error.message || '生成未完成，请重新尝试。', true);
      } finally { if (current === job) current = null; }
    }
    async function checkStatus() {
      if (destroyed) return;
      const sequence = ++statusSequence, controller = new environment.AbortController();
      if (statusController) statusController.abort(); statusController = controller;
      try {
        const response = await environment.fetch('/api/generation/status', { signal: controller.signal });
        if (!response.ok) throw new Error('无法读取回答模型状态。');
        const status = await response.json();
        if (destroyed || sequence !== statusSequence) return;
        readiness = status;
        $('generationModelStatus').textContent = status.provider === 'api' ? [status.model || 'GPT API', status.ready ? '已配置' : status.message].filter(Boolean).join(' · ') : status.ready ? (status.model || '本地模型') + ' · 已就绪' : status.message || '正在准备本地模型…';
        $('generationModelStatus').classList.toggle('generation-error', ['missing', 'error'].includes(status.status));
        if (status.ready && pendingQuestion) { const value = pendingQuestion, startedAt = pendingStartedAt; pendingQuestion = ''; pendingStartedAt = null; if (active()) submit({ scroll: false, text: value, startedAt: startedAt == null ? clock() : startedAt }); }
        else if (pendingQuestion && status.status !== 'loading') { pendingQuestion = ''; pendingStartedAt = null; setPhase('error'); setProgress(status.message || '回答模型未就绪。', true); }
        if (!status.ready) statusTimer = environment.setTimeout(checkStatus, status.status === 'loading' ? 1800 : 10000);
      } catch (error) {
        if (destroyed || sequence !== statusSequence || error.name === 'AbortError') return;
        $('generationModelStatus').textContent = error.message || '无法读取回答模型状态。';
        statusTimer = environment.setTimeout(checkStatus, 5000);
      }
    }
    function refreshStatus() { if (statusTimer) environment.clearTimeout(statusTimer); statusTimer = null; readiness = null; return checkStatus(); }
    function setView(next, onlyFavorites = false) {
      view = next; favorites = onlyFavorites;
      const generationView = view === 'answers' && !favorites;
      $('generationControls').hidden = !generationView;
      card.hidden = !generationView || !enabled.checked;
      $('referencePanel').classList.toggle('reference-only', !generationView || !enabled.checked);
      if (!generationView || !enabled.checked) $('referencePanel').open = true;
      $('composerDock').classList.toggle('retrieval-composer', !generationView || !enabled.checked);
    }
    $('generateAnswer').addEventListener('click', () => { const startedAt = clock(); onSearch(); submit({ startedAt }); });
    $('generationAgain').addEventListener('click', () => { const record = selectedRecord(); if (record) { input.value = record.data.question; submit({ text: record.data.question }); } });
    $('generationStop').addEventListener('click', () => cancel());
    $('clearConversation').addEventListener('click', clearHistory);
    $('questionSummaryToggle').addEventListener('click', () => {
      const expanded = !$('questionSummary').classList.contains('is-expanded');
      $('questionSummary').classList.toggle('is-expanded', expanded);
      $('questionSummaryToggle').setAttribute('aria-expanded', String(expanded));
      $('questionSummaryToggle').textContent = expanded ? '收起' : '展开';
    });
    $('generationCopy').addEventListener('click', () => copy(selectedRecord()));
    input.addEventListener('input', onQuestionChanged);
    enabled.addEventListener('change', () => { if (!enabled.checked) cancel('已停止生成，对话记录仍保留。'); setView(view, favorites); setPhase(phase); });
    doc.addEventListener('keydown', event => { if (event.key === 'Escape' && (current || pendingQuestion)) cancel(); });
    function destroy() { cancel(''); destroyed = true; if (statusTimer) environment.clearTimeout(statusTimer); if (statusController) statusController.abort(); }
    environment.addEventListener('pagehide', destroy);
    setPhase('idle'); setView('answers'); renderQuestions(null); checkStatus();
    return { submit, cancel, onQuestionChanged, setView, refreshStatus, destroy, loadHistory, clearHistory };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { initAnswerGeneration };
  else root.ConversationUI = { initAnswerGeneration };
})(typeof window !== 'undefined' ? window : globalThis);
