'use strict';

(function (root) {
  // A small text-only Markdown subset: model output never becomes HTML.
  function answerBlocks(value) {
    const blocks = [];
    let paragraph = [];
    const flush = () => {
      if (paragraph.length) blocks.push({ kind: 'paragraph', text: paragraph.join('\n') });
      paragraph = [];
    };
    for (const raw of String(value || '').replace(/\r\n?/g, '\n').split('\n')) {
      const line = raw.trim();
      if (!line) { flush(); continue; }
      const heading = line.match(/^#{1,6}(?:\s+|$)(.*)$/);
      const numbered = /^(?:\d{1,2}\.(?!\d)|\d{1,2}[、）)]|[一二三四五六七八九十]+[、）)]|[（(][一二三四五六七八九十\d]+[）)])\s*\S/.test(line);
      const bullet = line.match(/^[-*+]\s+(.+)$/);
      if (heading || numbered || bullet) {
        flush();
        const text = heading ? heading[1].replace(/\s+#+$/, '').trim() : bullet ? '• ' + bullet[1] : line;
        if (text) blocks.push({ kind: heading ? 'heading' : 'point', text });
      } else paragraph.push(line);
    }
    flush();
    return blocks;
  }
  function plainAnswer(value) {
    return answerBlocks(value).map(block => block.text.replace(/\*\*/g, '').trim()).filter(Boolean).join('\n\n');
  }
  function renderAnswer(target, value, doc) {
    const blocks = answerBlocks(value);
    const nodes = blocks.map(block => {
      const element = doc.createElement(block.kind === 'heading' ? 'h4' : 'p');
      element.className = 'spoken-' + block.kind;
      // An unfinished bold span can already be read while its closing ** is in flight.
      block.text.split('**').forEach((part, index) => {
        if (!part) return;
        if (index % 2) {
          const strong = doc.createElement('strong');
          strong.textContent = part;
          element.append(strong);
        } else element.append(doc.createTextNode(part));
      });
      return element;
    });
    target.replaceChildren(...nodes);
  }

  // TextDecoder keeps incomplete UTF-8 code points between network chunks.
  async function consumeNDJSON(response, onEvent, isCurrent = () => true) {
    if (!response.body || !response.body.getReader) throw new Error('浏览器未能接收回答，请使用新版 Edge 或 Chrome。');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = '', terminal = false;
    function line(value) {
      if (!value.trim() || !isCurrent()) return;
      let event;
      try { event = JSON.parse(value); } catch (_) { throw new Error('回答传输中断，请重新生成。'); }
      onEvent(event);
      if (event.type === 'done' || event.type === 'error') terminal = true;
    }
    try {
      while (isCurrent()) {
        const { value, done } = await reader.read();
        if (!isCurrent()) break;
        pending += decoder.decode(value || new Uint8Array(), { stream: !done });
        let newline;
        while ((newline = pending.indexOf('\n')) >= 0) {
          line(pending.slice(0, newline));
          pending = pending.slice(newline + 1);
        }
        if (done) { if (pending.trim()) line(pending); break; }
        if (terminal) break;
      }
      if (isCurrent() && !terminal) throw new Error('回答尚未生成完整，连接已中断，请重新生成。');
    } finally {
      try { await reader.cancel(); } catch (_) {}
      reader.releaseLock();
    }
  }

  function initAnswerGeneration(options = {}) {
    const conversations = typeof module !== 'undefined' && module.exports ? require('./conversation.js') : root.ConversationUI;
    return conversations.initAnswerGeneration({ ...options, consumeNDJSON });
  }

  function initProviderSettings({ onChanging = () => {}, onChanged = () => {}, document: doc = root.document, environment = root } = {}) {
    const $ = id => doc.getElementById(id);
    const dialog = $('providerSettingsDialog'), select = $('generationProvider');
    let saved = null, destroyed = false, loading = false, sequence = 0, activeRequest = null, activeKind = '';
    const defaults = { provider: 'local', base_url: 'https://api.openai.com/v1', api_format: 'responses', model: '', reasoning_effort: 'default', use_system_proxy: false, remember_key: true, has_api_key: false, key_storage: 'missing', credential_warning: '' };
    function publicSettings(value) {
      const next = {};
      // Deliberately whitelist display settings. A key is never retained from a response.
      for (const key of Object.keys(defaults)) next[key] = value[key] == null ? defaults[key] : value[key];
      return next;
    }
    function hostOf(value) {
      try { return new URL(value).host; } catch (_) { return '所配置的服务'; }
    }
    function message(text, error = false) {
      $('providerSettingsMessage').textContent = text;
      $('providerSettingsMessage').classList.toggle('generation-error', error);
    }
    function errorText(error, secret = '') {
      let text = error && error.message ? String(error.message) : '连接设置未能完成，请稍后重试。';
      if (secret) text = text.split(secret).join('[密钥已隐藏]');
      return text.replace(/\bsk-[A-Za-z0-9_-]+/g, '[密钥已隐藏]');
    }
    function setBusy(value) {
      loading = value;
      select.disabled = value || !saved;
      $('providerSettingsFields').disabled = value;
      $('providerSave').disabled = value;
      $('providerTest').disabled = value;
      dialog.querySelectorAll('[data-close]').forEach(button => { button.disabled = value && activeKind === 'save'; });
    }
    function renderSaved() {
      if (!saved) return;
      select.value = saved.provider;
      const api = saved.provider === 'api';
      const notice = $('providerNotice');
      notice.dataset.provider = saved.provider;
      notice.classList.toggle('generation-error', false);
      notice.textContent = api
        ? 'API 模式：问题、相关前文和资料片段将发送到 ' + hostOf(saved.base_url) + '，按服务计费。'
        : '本地模式：问题与材料在这台电脑上处理。';
      $('providerSidebarTitle').textContent = api ? 'GPT API 回答' : '本地模型';
      $('providerSidebarNote').textContent = api ? '资料检索与语音仍在本机处理' : '问题与材料在本机处理';
    }
    function transferNote() {
      const target = hostOf($('providerBaseUrl').value.trim());
      $('providerTransferNote').textContent = $('providerMode').value === 'api'
        ? '生成时将向 ' + target + ' 发送当前问题、相关对话与资料片段，并按该服务计费。原始文件仍保存在本机。'
        : '当前选择本地模型，问题与材料在本机处理。下方 API 配置可保留，之后再切换使用。';
    }
    function syncModelPreset() {
      const preset = $('providerModelPreset'), model = $('providerModel').value.trim();
      preset.value = Array.from(preset.options).some(option => option.value === model) ? model : '';
    }
    function fillForm(provider) {
      const value = saved || defaults;
      $('providerMode').value = provider || value.provider;
      $('providerBaseUrl').value = value.base_url;
      $('providerApiFormat').value = value.api_format;
      $('providerModel').value = value.model;
      syncModelPreset();
      $('providerReasoning').value = value.reasoning_effort;
      $('providerUseProxy').checked = value.use_system_proxy;
      $('providerApiKey').value = '';
      $('providerRememberKey').checked = value.remember_key !== false;
      $('providerClearKey').checked = false;
      $('providerClearKeyLabel').hidden = !value.has_api_key;
      $('providerKeyState').textContent = value.has_api_key
        ? '已配置密钥（' + (value.key_storage === 'windows' ? '当前 Windows 用户加密保存' : '仅本次运行') + '）。留空保留，填写新密钥则替换。'
        : value.credential_warning || '尚未配置密钥。API 模式需要填写您所选服务的密钥。';
      $('providerKeyState').classList.toggle('generation-error', !value.has_api_key && !!value.credential_warning);
      $('providerApiKey').placeholder = value.has_api_key ? '留空保留已有密钥' : '填写您的 API Key';
      message('');
      transferNote();
    }
    function draft() {
      const value = {
        provider: $('providerMode').value,
        base_url: $('providerBaseUrl').value.trim(),
        api_format: $('providerApiFormat').value,
        model: $('providerModel').value.trim(),
        reasoning_effort: $('providerReasoning').value,
        use_system_proxy: $('providerUseProxy').checked,
        remember_key: $('providerRememberKey').checked,
        clear_api_key: $('providerClearKey').checked,
      };
      const key = $('providerApiKey').value.trim();
      if (key) value.api_key = key;
      return value;
    }
    async function request(url, method, payload, signal) {
      const options = { method, signal };
      if (payload) { options.headers = { 'Content-Type': 'application/json' }; options.body = JSON.stringify(payload); }
      const response = await environment.fetch(url, options);
      let body;
      try { body = await response.json(); } catch (_) { throw new Error('服务返回格式异常，请确认已打开新版程序。'); }
      if (!response.ok) throw new Error(body && typeof body.detail === 'string' ? body.detail : '连接设置未能完成，请稍后重试。');
      return body;
    }
    async function refresh() {
      const ticket = ++sequence;
      if (activeRequest) activeRequest.abort();
      const controller = new environment.AbortController();
      activeRequest = controller;
      activeKind = 'read';
      setBusy(true);
      try {
        const value = await request('/api/generation/settings', 'GET', null, controller.signal);
        if (destroyed || ticket !== sequence) return false;
        saved = publicSettings(value);
        renderSaved();
        return true;
      } catch (error) {
        if (destroyed || ticket !== sequence || error.name === 'AbortError') return false;
        $('providerNotice').textContent = errorText(error);
        $('providerNotice').classList.toggle('generation-error', true);
        message(errorText(error), true);
        return false;
      } finally { if (!destroyed && ticket === sequence) { activeRequest = null; setBusy(false); } }
    }
    async function openSettings(provider) {
      if (loading || destroyed) return;
      if (!await refresh()) return;
      fillForm(provider);
      if (!dialog.open) dialog.showModal();
      $('providerMode').focus();
    }
    async function save(payload, closeDialog = false) {
      if (loading || destroyed) return false;
      onChanging();
      const ticket = ++sequence, controller = new environment.AbortController();
      activeRequest = controller;
      activeKind = 'save';
      setBusy(true);
      message('正在保存连接设置…');
      try {
        const result = await request('/api/generation/settings', 'PUT', payload, controller.signal);
        if (destroyed || ticket !== sequence) return false;
        saved = publicSettings(result);
        $('providerApiKey').value = '';
        renderSaved();
        message('设置已保存。下一次提问将使用所选模型。');
        await onChanged(saved);
        if (closeDialog && !destroyed && ticket === sequence) dialog.close();
        return true;
      } catch (error) {
        if (destroyed || ticket !== sequence || error.name === 'AbortError') return false;
        if (saved) select.value = saved.provider;
        const text = errorText(error, payload.api_key);
        message(text, true);
        if (!dialog.open) { $('providerNotice').textContent = text; $('providerNotice').classList.toggle('generation-error', true); }
        return false;
      } finally { if (!destroyed && ticket === sequence) { activeRequest = null; setBusy(false); } }
    }
    async function switchProvider() {
      if (loading || !saved) return;
      const next = select.value;
      if (next === saved.provider) return;
      select.value = saved.provider;
      if (next === 'api' && (!saved.has_api_key || !saved.model || !saved.base_url)) return openSettings('api');
      return save({ provider: next });
    }
    async function testConnection() {
      if (loading || destroyed) return;
      const payload = { ...draft(), provider: 'api' };
      const ticket = ++sequence, controller = new environment.AbortController();
      activeRequest = controller;
      activeKind = 'test';
      setBusy(true);
      message('正在用通用短句测试 API 连接…');
      try {
        const result = await request('/api/generation/test', 'POST', payload, controller.signal);
        if (destroyed || ticket !== sequence || !dialog.open) return;
        const seconds = Number(result.elapsed_ms) / 1000;
        message([result.message || '连接测试成功。', result.model || payload.model,
          Number.isFinite(seconds) ? '用时 ' + seconds.toFixed(1) + ' 秒' : ''].filter(Boolean).join(' · '));
      } catch (error) {
        if (destroyed || ticket !== sequence || error.name === 'AbortError' || !dialog.open) return;
        message(errorText(error, payload.api_key), true);
      } finally { if (!destroyed && ticket === sequence) { activeRequest = null; setBusy(false); } }
    }
    function close() {
      sequence += 1;
      if (activeRequest) activeRequest.abort();
      activeRequest = null;
      $('providerApiKey').value = '';
      $('providerClearKey').checked = false;
      setBusy(false);
      if (saved) select.value = saved.provider;
    }
    function destroy() { destroyed = true; close(); }
    $('providerSettingsOpen').addEventListener('click', () => openSettings());
    select.addEventListener('change', switchProvider);
    $('providerSettingsForm').addEventListener('submit', event => { event.preventDefault(); return save(draft(), true); });
    $('providerTest').addEventListener('click', testConnection);
    $('providerBaseUrl').addEventListener('input', transferNote);
    $('providerMode').addEventListener('change', transferNote);
    $('providerModel').addEventListener('input', syncModelPreset);
    $('providerModelPreset').addEventListener('change', () => {
      const model = $('providerModelPreset').value;
      if (model) $('providerModel').value = model;
      else $('providerModel').focus();
    });
    dialog.addEventListener('close', close);
    dialog.addEventListener('cancel', event => { if (loading && activeKind === 'save') event.preventDefault(); });
    environment.addEventListener('pagehide', destroy);
    refresh();
    return { refresh, openSettings, switchProvider, testConnection, destroy };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { consumeNDJSON, initAnswerGeneration, initProviderSettings, renderAnswer, plainAnswer };
  else { root.initAnswerGeneration = initAnswerGeneration; root.initProviderSettings = initProviderSettings; }
})(typeof window !== 'undefined' ? window : globalThis);
