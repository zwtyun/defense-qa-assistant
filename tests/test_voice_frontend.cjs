'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { encodeWav, createVoiceActivity, initVoiceSearch } = require('../static/voice.js');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('audio worklet mixes stereo and flushes the final partial audio batch', () => {
  let Recorder;
  const messages = [];
  const context = {
    Float32Array,
    AudioWorkletProcessor: class { constructor() { this.port = { postMessage: value => messages.push(value) }; } },
    registerProcessor(name, implementation) { assert.equal(name, 'defense-pcm-recorder'); Recorder = implementation; },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/pcm-worklet.js'), 'utf8'), context);
  const recorder = new Recorder();
  recorder.process([[new Float32Array(128).fill(0.75), new Float32Array(128).fill(0.25)]]);
  assert.equal(messages.length, 0);
  recorder.port.onmessage({ data: 'stop' });
  assert.equal(messages[0].length, 128);
  assert.equal(messages[0][127], 0.5);
  assert.equal(messages[1], 'stopped');
  assert.equal(recorder.process([]), false);
});

test('PCM WAV is mono 16 kHz, clips overshoot, and retains signed samples', () => {
  const bytes = encodeWav(new Float32Array([-2, -1, -0.5, 0, 0.5, 1, 2, NaN]));
  const wav = Buffer.from(bytes);
  assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
  assert.equal(wav.toString('ascii', 8, 12), 'WAVE');
  assert.equal(wav.readUInt32LE(4) + 8, wav.length);
  assert.equal(wav.readUInt16LE(20), 1);
  assert.equal(wav.readUInt16LE(22), 1);
  assert.equal(wav.readUInt32LE(24), 16000);
  assert.equal(wav.readUInt16LE(34), 16);
  assert.deepEqual(Array.from({ length: 8 }, (_, i) => wav.readInt16LE(44 + i * 2)), [-32768, -32768, -16384, 0, 16384, 32767, 32767, 0]);
});

test('pause detection ignores initial silence and brief noises, then waits after real speech', () => {
  const activity = createVoiceActivity();
  const signal = (seconds, amplitude) => new Float32Array(Math.round(seconds * 16000)).fill(amplitude);
  assert.equal(activity.push(signal(2, 0), 16000).shouldStop, false);
  assert.equal(activity.push(signal(0.05, 0.1), 16000).heardSpeech, false);
  assert.equal(activity.push(signal(2, 0), 16000).shouldStop, false);
  assert.equal(activity.push(signal(0.5, 0.05), 16000).heardSpeech, true);
  assert.equal(activity.push(signal(0.8, 0), 16000).shouldStop, false);
  assert.equal(activity.push(signal(0.3, 0.05), 16000).shouldStop, false);
  assert.equal(activity.push(signal(1.6, 0), 16000).shouldStop, true);
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
function makeHarness(transcribe, audioDuration = 0.5) {
  const elements = new Map(), transcripts = [], listeners = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      dataset: {}, style: {}, checked: true, textContent: '', listeners: {},
      classList: { toggle() {} }, setAttribute() {}, click() {},
      addEventListener(name, handler) { this.listeners[name] = handler; },
    });
    return elements.get(id);
  }
  class OfflineContext {
    constructor(channels, length) { this.length = length; }
    async decodeAudioData() { return { duration: audioDuration }; }
    createBufferSource() { return { connect() {}, start() {} }; }
    async startRendering() { return { getChannelData: () => new Float32Array(this.length).fill(0.02) }; }
  }
  const environment = {
    AbortController, Blob, FormData, OfflineAudioContext: OfflineContext,
    setTimeout, clearTimeout, setInterval, clearInterval, performance,
    addEventListener(name, callback) { listeners.set(name, callback); },
    fetch: async (url, options) => url.endsWith('/status') ? { ok: true, json: async () => ({ ready: true, status: 'ready' }) } : transcribe(options),
  };
  const controller = initVoiceSearch({ onTranscript: text => transcripts.push(text), document: { getElementById: element, addEventListener() {} }, environment });
  const file = { name: '问题.wav', size: 100, arrayBuffer: async () => new ArrayBuffer(100) };
  return { controller, element, environment, transcripts, file, listeners };
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('pause auto-send starts unchecked in markup and after browser state restoration', async () => {
  const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
  const checkbox = html.match(/<input\b[^>]*id="voiceAutoStop"[^>]*>/)[0];
  assert.doesNotMatch(checkbox, /\bchecked\b/);
  const h = makeHarness(() => { throw new Error('not recording'); });
  await tick();
  assert.equal(h.element('voiceAutoStop').checked, false);
  h.controller.destroy();
});

test('three-minute high-quality WAV is normalized and submitted without a five-MB limit', async () => {
  let uploadedBytes;
  const h = makeHarness(async options => {
    uploadedBytes = options.body.get('file').size;
    return { ok: true, json: async () => ({ text: '完整三分钟的问题', elapsed_ms: 100 }) };
  }, 180);
  await tick();
  await h.controller.recognizeFile({ ...h.file, size: 48000 * 2 * 2 * 180 + 44 });
  assert.equal(uploadedBytes, 16000 * 2 * 180 + 44);
  assert.deepEqual(h.transcripts, ['完整三分钟的问题']);
  h.controller.destroy();
});

test('WAV longer than three minutes is rejected before upload', async () => {
  let calls = 0;
  const h = makeHarness(async () => { calls++; }, 180.01);
  await tick();
  await h.controller.recognizeFile(h.file);
  assert.equal(calls, 0);
  assert.match(h.element('voiceMessage').textContent, /超过 3 分钟/);
  h.controller.destroy();
});

test('long transcript is preserved in full for editing instead of silently cutting the question', async () => {
  const text = '完整识别文字'.repeat(101);
  const h = makeHarness(async () => ({ ok: true, json: async () => ({ text }) }));
  await tick();
  await h.controller.recognizeFile(h.file);
  assert.deepEqual(h.transcripts, [text]);
  assert.match(h.element('voiceMessage').textContent, /超过500字.*精简/);
  h.controller.destroy();
});

test('recognized recording reaches search while microphone is never requested for a file', async () => {
  const harness = makeHarness(async options => {
    assert.equal(options.body.get('file').type, 'audio/wav');
    return { ok: true, json: async () => ({ text: '怎样区分传感器漂移？', elapsed_ms: 350 }) };
  });
  await tick();
  await harness.controller.recognizeFile(harness.file);
  assert.deepEqual(harness.transcripts, ['怎样区分传感器漂移？']);
  assert.match(harness.element('voiceMessage').textContent, /文字已完整填入/);
  harness.controller.destroy();
});

test('typing a new query cancels speech and discards even a late successful recognition', async () => {
  const pending = deferred();
  let signal;
  const harness = makeHarness(options => { signal = options.signal; return pending.promise; });
  await tick();
  const recognition = harness.controller.recognizeFile(harness.file);
  await tick();
  harness.element('searchInput').listeners.input();
  assert.equal(signal.aborted, true);
  pending.resolve({ ok: true, json: async () => ({ text: '过时的问题', elapsed_ms: 500 }) });
  await recognition;
  assert.deepEqual(harness.transcripts, []);
  assert.match(harness.element('voiceMessage').textContent, /文字输入/);
  harness.controller.destroy();
});

test('cancel while microphone permission is pending closes audio and stops a late stream', async () => {
  const harness = makeHarness(() => { throw new Error('No audio should be submitted'); });
  const permission = deferred();
  let closed = 0, stopped = 0;
  harness.environment.isSecureContext = true;
  harness.environment.navigator = { mediaDevices: { getUserMedia: () => permission.promise } };
  harness.environment.AudioWorkletNode = class {};
  harness.environment.AudioContext = class {
    constructor() { this.state = 'running'; }
    async resume() {}
    async close() { this.state = 'closed'; closed++; }
  };
  await tick();
  const starting = harness.element('voiceButton').listeners.click();
  harness.controller.cancel();
  permission.resolve({ getTracks: () => [{ stop: () => stopped++ }] });
  await starting;
  assert.equal(closed, 1);
  assert.equal(stopped, 1);
  assert.deepEqual(harness.transcripts, []);
  harness.controller.destroy();
});

test('empty recognition keeps current query and reports how to retry', async () => {
  const harness = makeHarness(async () => ({ ok: true, json: async () => ({ text: '' }) }));
  await tick();
  await harness.controller.recognizeFile(harness.file);
  assert.deepEqual(harness.transcripts, []);
  assert.match(harness.element('voiceMessage').textContent, /没有识别到/);
  assert.equal(harness.element('voicePanel').dataset.phase, 'idle');
  harness.controller.destroy();
});

test('microphone stop retains its last batch and releases the device before transcription', async () => {
  let stopped = 0, worklet, capturedLength = 0;
  const harness = makeHarness(async () => {
    assert.equal(stopped, 1, 'microphone must be released before ASR begins');
    return { ok: true, json: async () => ({ text: '九项装备分别是什么？', elapsed_ms: 100 }) };
  });
  const track = { stop() { stopped++; }, addEventListener() {} };
  harness.environment.isSecureContext = true;
  harness.environment.navigator = { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [track], getAudioTracks: () => [track] }) } };
  harness.environment.AudioWorkletNode = class {
    constructor() {
      worklet = this;
      this.port = { postMessage: command => {
        assert.equal(command, 'stop');
        this.port.onmessage({ data: new Float32Array(128).fill(0.1) });
        this.port.onmessage({ data: 'stopped' });
      } };
    }
    connect() {}
    disconnect() {}
  };
  harness.environment.AudioContext = class {
    constructor() { this.state = 'running'; this.sampleRate = 16000; this.audioWorklet = { addModule: async () => {} }; }
    async resume() {}
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createGain() { return { gain: {}, connect() {}, disconnect() {} }; }
    createBuffer(channels, count, sampleRate) { capturedLength = count; return { duration: count / sampleRate, copyToChannel() {} }; }
  };
  await tick();
  await harness.element('voiceButton').listeners.click();
  assert.equal(harness.element('voicePanel').dataset.phase, 'recording');
  worklet.port.onmessage({ data: new Float32Array(8000).fill(0.1) });
  await harness.element('voiceButton').listeners.click();
  assert.equal(capturedLength, 8128, 'last 128 samples must be retained');
  assert.deepEqual(harness.transcripts, ['九项装备分别是什么？']);
  harness.controller.destroy();
});

function attachMicrophone(harness) {
  let now = 0, serial = 0, worklet, stopped = 0;
  const intervals = new Map();
  const track = { stop() { stopped++; }, addEventListener() {} };
  Object.assign(harness.environment, {
    isSecureContext: true,
    performance: { now: () => now },
    setInterval(callback) { const id = ++serial; intervals.set(id, callback); return id; },
    clearInterval(id) { intervals.delete(id); },
    navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [track], getAudioTracks: () => [track] }) } },
  });
  harness.environment.AudioWorkletNode = class {
    constructor() {
      worklet = this;
      this.port = { postMessage: command => {
        assert.equal(command, 'stop');
        this.port.onmessage({ data: new Float32Array(128).fill(0.05) });
        this.port.onmessage({ data: 'stopped' });
      } };
    }
    connect() {}
    disconnect() {}
  };
  harness.environment.AudioContext = class {
    constructor() { this.state = 'running'; this.sampleRate = 16000; this.audioWorklet = { addModule: async () => {} }; }
    async resume() {}
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createGain() { return { gain: {}, connect() {}, disconnect() {} }; }
    createBuffer(channels, count, sampleRate) { return { duration: count / sampleRate, copyToChannel() {} }; }
  };
  return {
    emit(seconds, amplitude) { worklet.port.onmessage({ data: new Float32Array(Math.round(seconds * 16000)).fill(amplitude) }); },
    advance(seconds) { now += seconds * 1000; for (const callback of [...intervals.values()]) callback(); },
    get stopped() { return stopped; },
  };
}

test('default pause setting keeps recording after silence and sixty seconds, with a three-minute sample and timer stop', async () => {
  const uploads = [];
  const h = makeHarness(async options => {
    uploads.push(options.body.get('file').size);
    return { ok: true, json: async () => ({ text: '三分钟录音边界测试' }) };
  });
  const microphone = attachMicrophone(h);
  try {
    await tick();
    assert.equal(h.element('voiceAutoStop').checked, false);
    await h.element('voiceButton').listeners.click();
    microphone.emit(0.5, 0.05);
    microphone.emit(1.6, 0);
    await tick();
    assert.equal(h.element('voicePanel').dataset.phase, 'recording');
    assert.equal(uploads.length, 0, 'a normal pause must not submit with the default setting');
    microphone.advance(60);
    assert.equal(h.element('voicePanel').dataset.phase, 'recording');
    assert.equal(h.element('voiceTimer').textContent, '01:00');
    microphone.advance(119);
    assert.equal(h.element('voicePanel').dataset.phase, 'recording');
    // An oversized last batch and the worklet flush must both respect the cap.
    microphone.emit(179, 0.05);
    await tick();
    assert.deepEqual(uploads, [180 * 16000 * 2 + 44]);
    assert.equal(h.element('voicePanel').dataset.phase, 'idle');
    assert.equal(microphone.stopped, 1);
    // The wall-clock fallback also stops at three minutes if PCM delivery stalls.
    await h.element('voiceButton').listeners.click();
    microphone.emit(0.5, 0.05);
    microphone.advance(179);
    assert.equal(h.element('voicePanel').dataset.phase, 'recording');
    microphone.advance(1);
    await tick();
    assert.equal(uploads.length, 2);
    assert.ok(uploads[1] <= 180 * 16000 * 2 + 44);
    assert.equal(h.element('voiceTimer').textContent, '03:00');
    assert.equal(h.element('voicePanel').dataset.phase, 'idle');
    assert.equal(microphone.stopped, 2);
  } finally { h.controller.destroy(); }
});

test('explicitly enabling pause auto-send still stops after a spoken phrase and a long enough pause', async () => {
  let uploads = 0;
  const h = makeHarness(async () => {
    uploads++;
    return { ok: true, json: async () => ({ text: '主动勾选停顿发送测试' }) };
  });
  const microphone = attachMicrophone(h);
  try {
    await tick();
    h.element('voiceAutoStop').checked = true;
    await h.element('voiceButton').listeners.click();
    microphone.emit(0.5, 0.05);
    microphone.emit(1.4, 0);
    assert.equal(h.element('voicePanel').dataset.phase, 'recording');
    assert.equal(uploads, 0);
    microphone.emit(0.2, 0);
    await tick();
    assert.equal(uploads, 1);
    assert.equal(h.element('voicePanel').dataset.phase, 'idle');
    assert.equal(microphone.stopped, 1);
    assert.deepEqual(h.transcripts, ['主动勾选停顿发送测试']);
  } finally { h.controller.destroy(); }
});
