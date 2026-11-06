'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { initProviderSettings } = require('../static/generation.js');
const settingsMarkup = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const presetMarkup = settingsMarkup.match(/<select id="providerModelPreset">([\s\S]*?)<\/select>/)?.[1] || '';
const modelOptions = Array.from(presetMarkup.matchAll(/<option value="([^"]*)"/g), match => ({ value: match[1] }));
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
const initial = { provider: 'local', base_url: 'https://example.test/v1', api_format: 'chat_completions', model: 'gpt-example', reasoning_effort: 'low', use_system_proxy: false, remember_key: true, has_api_key: true, key_storage: 'windows' };
function harness(route, options = {}) {
  const nodes = new Map(), requests = [], changes = [], changing = [];
  function element(id) {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, { id, value: '', checked: false, disabled: false, hidden: false, open: false, dataset: {}, textContent: '', listeners: {},
        options: id === 'providerModelPreset' ? modelOptions : [],
        classList: { toggle(name, value) { if (value) classes.add(name); else classes.delete(name); }, contains: name => classes.has(name) },
        addEventListener(name, fn) { this.listeners[name] = fn; }, querySelectorAll() { return []; }, focus() {},
        showModal() { this.open = true; }, close() { this.open = false; this.listeners.close?.(); },
      });
    }
    return nodes.get(id);
  }
  let stored = { ...initial, ...options };
  const environment = { AbortController, addEventListener() {},
    get localStorage() { throw new Error('Settings must not use browser storage'); },
    async fetch(url, request = {}) {
      requests.push({ url, ...request });
      if (route) {
        const value = await route(url, request);
        if (value) return value;
      }
      if (request.method === 'PUT') {
        stored = { ...stored, ...JSON.parse(request.body) };
        delete stored.api_key;
        if (JSON.parse(request.body).api_key) stored.has_api_key = true;
      }
      return { ok: true, json: async () => stored };
    },
  };
  const controller = initProviderSettings({ document: { getElementById: element }, environment,
    onChanging: () => changing.push('cancel'), onChanged: value => changes.push(value) });
  return { controller, element, requests, changes, changing };
}

test('Luna is directly selectable from Terra without clearing the model, and save/reopen retains it', async () => {
  assert.ok(modelOptions.some(option => option.value === 'gpt-5.6-luna'));
  const h = harness(null, { provider: 'api', model: 'gpt-6.1-terra' });
  await tick();
  await h.controller.openSettings();
  assert.equal(h.element('providerModelPreset').value, 'gpt-6.1-terra');
  h.element('providerModelPreset').value = 'gpt-5.6-luna';
  h.element('providerModelPreset').listeners.change();
  assert.equal(h.element('providerModel').value, 'gpt-5.6-luna');
  assert.equal(h.requests.some(request => request.method === 'PUT' || request.method === 'POST'), false);
  await h.element('providerSettingsForm').listeners.submit({ preventDefault() {} });
  const payload = JSON.parse(h.requests.find(request => request.method === 'PUT').body);
  assert.equal(payload.model, 'gpt-5.6-luna');
  assert.equal('api_key' in payload, false);
  await h.controller.openSettings();
  assert.equal(h.element('providerModelPreset').value, 'gpt-5.6-luna');
  assert.equal(h.element('providerModel').value, 'gpt-5.6-luna');
  h.controller.destroy();
});

test('cancel discards a preset change and arbitrary model names remain editable and saveable', async () => {
  const h = harness(null, { provider: 'api', model: 'gpt-6.1-terra' });
  await tick();
  await h.controller.openSettings();
  h.element('providerModelPreset').value = 'gpt-5.6-luna';
  h.element('providerModelPreset').listeners.change();
  h.element('providerSettingsDialog').close();
  await h.controller.openSettings();
  assert.equal(h.element('providerModel').value, 'gpt-6.1-terra');
  assert.equal(h.requests.some(request => request.method === 'PUT'), false);
  h.element('providerModel').value = 'my-custom-model';
  h.element('providerModel').listeners.input();
  assert.equal(h.element('providerModelPreset').value, '');
  await h.element('providerSettingsForm').listeners.submit({ preventDefault() {} });
  assert.equal(JSON.parse(h.requests.find(request => request.method === 'PUT').body).model, 'my-custom-model');
  await h.controller.openSettings();
  assert.equal(h.element('providerModel').value, 'my-custom-model');
  assert.equal(h.element('providerModelPreset').value, '');
  h.controller.destroy();
});

test('saved key is represented only by presence; opening/closing never refills it or uses browser storage', async () => {
  const h = harness(null, { api_key: 'sk-should-never-be-revealed' });
  await tick();
  assert.equal(h.element('generationProvider').disabled, false);
  await h.controller.openSettings();
  assert.equal(h.element('providerApiKey').value, '');
  assert.match(h.element('providerKeyState').textContent, /已配置密钥/);
  assert.match(h.element('providerKeyState').textContent, /加密保存/);
  h.element('providerApiKey').value = 'sk-entered-temporarily';
  h.element('providerSettingsDialog').close();
  assert.equal(h.element('providerApiKey').value, '');
  await h.controller.openSettings();
  assert.equal(h.element('providerApiKey').value, '');
  h.controller.destroy();
});

test('connection test sends only draft connection fields, preserves provider selection, and reports measured time', async () => {
  const h = harness((url) => url.endsWith('/test') ? { ok: true, json: async () => ({ ok: true, message: '连接测试成功。', model: 'gpt-example', elapsed_ms: 1280 }) } : null);
  await tick();
  await h.controller.openSettings();
  h.element('searchInput').value = '不能发送的专家问题与秘密项目材料';
  h.element('providerApiKey').value = 'sk-test-draft';
  h.element('providerUseProxy').checked = true;
  await h.controller.testConnection();
  const request = h.requests.find(x => x.url.endsWith('/test'));
  const payload = JSON.parse(request.body);
  assert.equal(payload.api_key, 'sk-test-draft');
  assert.equal(payload.use_system_proxy, true);
  assert.equal(payload.provider, 'api');
  assert.equal('question' in payload, false);
  assert.equal('sources' in payload, false);
  assert.equal(request.body.includes('秘密项目材料'), false);
  assert.equal(h.requests.some(x => x.method === 'PUT'), false);
  assert.equal(h.element('generationProvider').value, 'local');
  assert.match(h.element('providerSettingsMessage').textContent, /1\.3 秒/);
  assert.equal(h.changing.length, 0);
  h.controller.destroy();
});

test('switching a configured provider cancels the old answer and refreshes state without generating a new question', async () => {
  const h = harness();
  await tick();
  h.element('generationProvider').value = 'api';
  await h.controller.switchProvider();
  assert.deepEqual(h.changing, ['cancel']);
  assert.equal(h.changes.length, 1);
  assert.equal(h.changes[0].provider, 'api');
  assert.equal(h.element('generationProvider').value, 'api');
  assert.match(h.element('providerNotice').textContent, /example\.test/);
  assert.match(h.element('providerNotice').textContent, /相关前文.*资料片段/);
  assert.deepEqual(JSON.parse(h.requests.find(x => x.method === 'PUT').body), { provider: 'api' });
  assert.equal(h.requests.some(x => x.url === '/api/generate'), false);
  h.controller.destroy();
});

test('an unconfigured API opens settings and cancellation retains local mode', async () => {
  const h = harness(null, { has_api_key: false, key_storage: 'missing' });
  await tick();
  h.element('generationProvider').value = 'api';
  await h.controller.switchProvider();
  assert.equal(h.element('providerSettingsDialog').open, true);
  assert.equal(h.element('providerMode').value, 'api');
  assert.equal(h.element('generationProvider').value, 'local');
  h.element('providerSettingsDialog').close();
  assert.equal(h.element('generationProvider').value, 'local');
  assert.equal(h.requests.some(x => x.method === 'PUT'), false);
  h.controller.destroy();
});

test('no model is preselected; an API with a saved key waits for the users model choice', async () => {
  const h = harness(null, { model: '', has_api_key: true });
  await tick();
  h.element('generationProvider').value = 'api';
  await h.controller.switchProvider();
  assert.equal(h.element('providerSettingsDialog').open, true);
  assert.equal(h.element('providerModel').value, '');
  assert.equal(h.element('generationProvider').value, 'local');
  assert.equal(h.requests.some(x => x.method === 'PUT'), false);
  h.element('providerModel').value = 'user-selected-model';
  await h.element('providerSettingsForm').listeners.submit({ preventDefault() {} });
  const payload = JSON.parse(h.requests.find(x => x.method === 'PUT').body);
  assert.equal(payload.model, 'user-selected-model');
  assert.equal(payload.provider, 'api');
  assert.equal('api_key' in payload, false);
  assert.equal(h.element('generationProvider').value, 'api');
  assert.equal(h.element('providerSettingsDialog').open, false);
  h.controller.destroy();
});

test('unreadable encrypted credentials show the recovery explanation without exposing any key', async () => {
  const warning = '当前 Windows 用户无法解密已有密钥，请重新填写 API Key。';
  const h = harness(null, { has_api_key: false, key_storage: 'missing', credential_warning: warning });
  await tick();
  await h.controller.openSettings('api');
  assert.equal(h.element('providerKeyState').textContent, warning);
  assert.equal(h.element('providerKeyState').classList.contains('generation-error'), true);
  assert.equal(h.element('providerApiKey').value, '');
  h.controller.destroy();
});

test('saving key replacement clears its field and refreshes status; blank input preserves the saved key', async () => {
  const h = harness();
  await tick();
  await h.controller.openSettings('api');
  h.element('providerApiKey').value = 'sk-new-key';
  await h.element('providerSettingsForm').listeners.submit({ preventDefault() {} });
  assert.equal(h.element('providerApiKey').value, '');
  assert.equal(h.element('providerSettingsDialog').open, false);
  assert.equal(h.element('generationProvider').value, 'api');
  assert.equal(h.changes[0].api_key, undefined);
  await h.controller.openSettings();
  await h.element('providerSettingsForm').listeners.submit({ preventDefault() {} });
  const puts = h.requests.filter(x => x.method === 'PUT').map(x => JSON.parse(x.body));
  assert.equal(puts[0].api_key, 'sk-new-key');
  assert.equal('api_key' in puts[1], false);
  h.controller.destroy();
});

test('busy rejection keeps the actual provider and hides any key echoed in an error', async () => {
  const h = harness((url, request) => request.method === 'PUT' ? { ok: false, json: async () => ({ detail: '上一条回答仍在生成，请停止后稍候。 sk-sensitive-value' }) } : null);
  await tick();
  await h.controller.openSettings('api');
  h.element('providerApiKey').value = 'sk-sensitive-value';
  await h.element('providerSettingsForm').listeners.submit({ preventDefault() {} });
  assert.equal(h.element('generationProvider').value, 'local');
  assert.equal(h.element('providerSettingsDialog').open, true);
  assert.equal(h.changes.length, 0);
  assert.match(h.element('providerSettingsMessage').textContent, /请停止/);
  assert.equal(h.element('providerSettingsMessage').textContent.includes('sk-sensitive-value'), false);
  assert.equal(h.element('providerSettingsMessage').classList.contains('generation-error'), true);
  h.controller.destroy();
});

test('closing connection test aborts it, clears the key, and discards late success', async () => {
  const pending = deferred();
  const h = harness(url => url.endsWith('/test') ? pending.promise : null);
  await tick();
  await h.controller.openSettings();
  h.element('providerApiKey').value = 'sk-ephemeral';
  const work = h.controller.testConnection();
  await tick();
  h.element('providerSettingsDialog').close();
  assert.equal(h.requests.find(x => x.url.endsWith('/test')).signal.aborted, true);
  assert.equal(h.element('providerApiKey').value, '');
  pending.resolve({ ok: true, json: async () => ({ message: '不应显示的迟到结果' }) });
  await work;
  assert.equal(h.element('providerSettingsMessage').textContent.includes('不应显示'), false);
  h.controller.destroy();
});
