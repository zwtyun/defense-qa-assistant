'use strict';

(function (root) {
  const SAMPLE_RATE = 16000;
  const MAX_SECONDS = 180;
  const MAX_FILE_BYTES = 36 * 1024 * 1024;

  function encodeWav(samples) {
    const bytes = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(bytes);
    const text = (offset, value) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
    text(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVE');
    text(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true); view.setUint32(24, SAMPLE_RATE, true);
    view.setUint32(28, SAMPLE_RATE * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    text(36, 'data'); view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
      const sample = Math.max(-1, Math.min(1, Number.isFinite(samples[i]) ? samples[i] : 0));
      view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
    }
    return bytes;
  }

  function createVoiceActivity() {
    let duration = 0, voiceDuration = 0, lastVoice = 0, heardSpeech = false;
    return {
      push(samples, sampleRate) {
        const seconds = samples.length / sampleRate;
        duration += seconds;
        let squareSum = 0;
        for (const value of samples) squareSum += value * value;
        const rms = Math.sqrt(squareSum / Math.max(1, samples.length));
        if (rms >= (heardSpeech ? 0.009 : 0.012)) {
          voiceDuration += seconds;
          lastVoice = duration;
          if (voiceDuration >= 0.18) heardSpeech = true;
        } else if (!heardSpeech) {
          voiceDuration = Math.max(0, voiceDuration - seconds);
        }
        return { rms, duration, heardSpeech, shouldStop: heardSpeech && duration - lastVoice >= 1.5 };
      },
      get heardSpeech() { return heardSpeech; },
    };
  }

  function microphoneError(error) {
    const messages = {
      NotAllowedError: '麦克风权限未开启。请在浏览器地址栏的权限设置中允许麦克风，再试一次。',
      PermissionDeniedError: '麦克风权限未开启。请允许本机应用使用麦克风。',
      NotFoundError: '没有找到麦克风。请连接麦克风，或使用“识别录音文件”。',
      DevicesNotFoundError: '没有找到麦克风。请检查设备连接。',
      NotReadableError: '麦克风无法使用，可能正被其他程序占用。请检查 Windows 的输入设备和权限。',
      TrackStartError: '麦克风无法使用，请检查 Windows 的输入设备和权限。',
      SecurityError: '浏览器禁止访问麦克风。请通过程序打开本机 localhost 页面，并允许麦克风。',
    };
    return messages[error && error.name] || (error && error.message) || '语音输入未完成，请重试。';
  }

  function initVoiceSearch({ onTranscript, document: doc = root.document, environment = root }) {
    const $ = (id) => doc.getElementById(id);
    const panel = $('voicePanel'), mainButton = $('voiceButton'), label = $('voiceButtonLabel');
    const cancelButton = $('voiceCancel'), fileButton = $('voiceFileButton'), fileInput = $('voiceFileInput');
    const message = $('voiceMessage'), input = $('searchInput');
    $('voiceAutoStop').checked = false;
    let phase = 'idle', current = null, readiness = null, pollTimer = null, statusController = null, destroyed = false;
    const isCurrent = (job) => current === job && !job.cancelled && !destroyed;

    function showMessage(text, error = false) {
      message.textContent = text;
      message.classList.toggle('voice-error', error);
    }
    function setPhase(next) {
      phase = next;
      panel.dataset.phase = next;
      const busy = next !== 'idle';
      label.textContent = next === 'recording' ? '停止并检索' : next === 'transcribing' ? '正在识别…' : next === 'preparing' ? '正在准备…' : '语音提问';
      mainButton.disabled = busy ? next !== 'recording' : !(readiness && readiness.ready);
      mainButton.setAttribute('aria-pressed', String(next === 'recording'));
      cancelButton.hidden = !busy;
      $('voiceRecording').hidden = next !== 'recording';
      fileButton.disabled = busy || !(readiness && readiness.ready);
      $('voiceAutoStop').disabled = busy && next !== 'recording';
    }
    function cleanup(job) {
      if (!job) return;
      if (job.finishFlush) job.finishFlush();
      if (job.timer) environment.clearInterval(job.timer);
      job.timer = null;
      if (job.worklet) { job.worklet.port.onmessage = null; try { job.worklet.disconnect(); } catch (_) {} }
      if (job.source) { try { job.source.disconnect(); } catch (_) {} }
      if (job.silentGain) { try { job.silentGain.disconnect(); } catch (_) {} }
      if (job.stream) job.stream.getTracks().forEach(track => track.stop());
      if (job.context && job.context.state !== 'closed') job.context.close().catch(() => {});
      job.stream = null;
      job.chunks = [];
      $('voiceLevel').style.width = '0%';
    }
    function cancel(reason = '已取消语音输入，原来的搜索内容已保留。') {
      if (!current) return;
      const job = current;
      job.cancelled = true;
      current = null;
      job.controller.abort();
      cleanup(job);
      setPhase('idle');
      if (reason) showMessage(reason);
    }
    function fail(job, error) {
      if (!isCurrent(job)) return;
      cleanup(job);
      current = null;
      setPhase('idle');
      showMessage(microphoneError(error), true);
    }
    function newJob() {
      cancel('');
      const job = { cancelled: false, controller: new environment.AbortController(), chunks: [], sampleCount: 0 };
      current = job;
      return job;
    }

    async function checkStatus() {
      if (destroyed) return;
      statusController = new environment.AbortController();
      try {
        const response = await environment.fetch('/api/speech/status', { signal: statusController.signal });
        if (!response.ok) throw new Error('本地语音服务暂未连接，请确认已启动新版程序。');
        const data = await response.json();
        if (destroyed) return;
        readiness = data;
        if (phase === 'idle') {
          setPhase('idle');
          showMessage(data.ready ? '点击“语音提问”，说完后停止，识别文字会自动用于搜索。' : data.message || '正在准备本地语音识别…', ['missing', 'error'].includes(data.status));
        }
        if (data.status === 'loading' || data.status === 'missing') pollTimer = environment.setTimeout(checkStatus, data.status === 'loading' ? 1500 : 10000);
      } catch (error) {
        if (destroyed || error.name === 'AbortError') return;
        readiness = null;
        if (phase === 'idle') { setPhase('idle'); showMessage(error.message || '无法连接本地语音服务，请重新启动程序。', true); }
        pollTimer = environment.setTimeout(checkStatus, 5000);
      }
    }

    async function resample(buffer) {
      const OfflineContext = environment.OfflineAudioContext || environment.webkitOfflineAudioContext;
      if (!OfflineContext) throw new Error('当前浏览器不支持音频转换，请使用新版 Edge 或 Chrome。');
      const offline = new OfflineContext(1, Math.ceil(buffer.duration * SAMPLE_RATE), SAMPLE_RATE);
      const source = offline.createBufferSource();
      source.buffer = buffer;
      source.connect(offline.destination);
      source.start();
      const converted = await offline.startRendering();
      return converted.getChannelData(0);
    }
    async function transcribe(job, samples) {
      if (!isCurrent(job)) return;
      const duration = samples.length / SAMPLE_RATE;
      if (duration < 0.25) throw new Error('录音太短，请说出完整问题后再停止。');
      if (duration > MAX_SECONDS) throw new Error('录音超过 3 分钟，请截取一个问题后再试。');
      setPhase('transcribing');
      showMessage('正在本机识别语音，完成后自动检索…');
      const form = new environment.FormData();
      form.append('file', new environment.Blob([encodeWav(samples)], { type: 'audio/wav' }), 'question.wav');
      const response = await environment.fetch('/api/speech/transcribe', { method: 'POST', body: form, signal: job.controller.signal });
      const data = await response.json();
      if (!isCurrent(job)) return;
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '语音识别未完成，请重试。');
      const text = String(data.text || '').trim();
      if (!text) throw new Error('没有识别到清晰的语音。请靠近麦克风，重新说出问题。');
      current = null;
      setPhase('idle');
      const seconds = Number(data.elapsed_ms) / 1000;
      showMessage('已识别' + (Number.isFinite(seconds) ? '，用时 ' + seconds.toFixed(1) + ' 秒' : '') + '。文字已完整填入搜索框，可直接修改。' + (text.length > 500 ? '本次超过500字，请精简或分成几题后再发送。' : ''));
      onTranscript(text);
    }

    async function finishRecording(job) {
      if (!isCurrent(job) || phase !== 'recording') return;
      setPhase('transcribing');
      showMessage('录音结束，正在本机识别…');
      // Flush the worklet's final partial batch so the last syllable is retained.
      job.stopping = true;
      await new Promise(resolve => {
        let timeout;
        job.finishFlush = () => { environment.clearTimeout(timeout); job.finishFlush = null; resolve(); };
        timeout = environment.setTimeout(job.finishFlush, 250);
        job.worklet.port.postMessage('stop');
      });
      if (!isCurrent(job)) return;
      // Take ownership before cleanup; microphone tracks stop before recognition starts.
      const chunks = job.chunks;
      const count = job.sampleCount;
      const context = job.context;
      cleanup(job);
      try {
        const raw = new Float32Array(count);
        let offset = 0;
        for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.length; }
        if (raw.length < context.sampleRate * 0.25) throw new Error('录音太短，请说出完整问题后再停止。');
        const buffer = context.createBuffer(1, raw.length, context.sampleRate);
        buffer.copyToChannel(raw, 0);
        const samples = await resample(buffer);
        await transcribe(job, samples);
      } catch (error) { fail(job, error); }
    }

    async function startRecording() {
      if (phase === 'recording') return finishRecording(current);
      if (phase !== 'idle' || !readiness || !readiness.ready) return;
      if (!environment.isSecureContext || !environment.navigator.mediaDevices || !environment.navigator.mediaDevices.getUserMedia) {
        showMessage('当前浏览器无法使用麦克风，请通过程序在新版 Edge 或 Chrome 打开本机页面，也可以识别 WAV 录音文件。', true);
        return;
      }
      const Context = environment.AudioContext || environment.webkitAudioContext;
      if (!Context || !environment.AudioWorkletNode) {
        showMessage('当前浏览器不支持录音，请使用新版 Edge 或 Chrome，或识别 WAV 录音文件。', true);
        return;
      }
      const job = newJob();
      setPhase('preparing');
      showMessage('请允许麦克风访问，等待“正在听”后说出问题。');
      try {
        job.context = new Context();
        // Resume inside the click handler, before any asynchronous permission request.
        const resumed = job.context.resume();
        resumed.catch(() => {}); // Closing while a permission prompt is pending may reject resume.
        const stream = await environment.navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
        if (!isCurrent(job)) { stream.getTracks().forEach(track => track.stop()); return; }
        job.stream = stream;
        await resumed;
        await job.context.audioWorklet.addModule('/static/pcm-worklet.js');
        if (!isCurrent(job)) return;
        job.source = job.context.createMediaStreamSource(stream);
        job.worklet = new environment.AudioWorkletNode(job.context, 'defense-pcm-recorder');
        job.silentGain = job.context.createGain();
        job.silentGain.gain.value = 0;
        job.source.connect(job.worklet);
        job.worklet.connect(job.silentGain);
        job.silentGain.connect(job.context.destination);
        job.activity = createVoiceActivity();
        job.started = environment.performance.now();
        job.worklet.port.onmessage = ({ data }) => {
          if (!isCurrent(job)) return;
          if (data === 'stopped') { if (job.finishFlush) job.finishFlush(); return; }
          if ((phase !== 'recording' && !job.stopping) || !(data instanceof Float32Array)) return;
          const remaining = Math.max(0, Math.floor(job.context.sampleRate * MAX_SECONDS) - job.sampleCount);
          const chunk = data.length > remaining ? data.slice(0, remaining) : data;
          if (chunk.length) { job.chunks.push(chunk); job.sampleCount += chunk.length; }
          if (job.stopping) return;
          const activity = job.activity.push(chunk, job.context.sampleRate);
          $('voiceLevel').style.width = Math.min(100, activity.rms * 600) + '%';
          if ($('voiceAutoStop').checked && activity.shouldStop) finishRecording(job);
          else if (activity.duration >= MAX_SECONDS) finishRecording(job);
        };
        for (const track of stream.getAudioTracks()) track.addEventListener('ended', () => {
          if (isCurrent(job) && phase === 'recording') fail(job, new Error('麦克风已断开，请检查设备后重新录音。'));
        });
        job.timer = environment.setInterval(() => {
          if (!isCurrent(job) || phase !== 'recording') return;
          const seconds = Math.floor((environment.performance.now() - job.started) / 1000);
          $('voiceTimer').textContent = String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
          if (seconds >= 10 && !job.activity.heardSpeech) fail(job, new Error('暂未听到清晰语音，已停止录音。请检查麦克风输入后再试。'));
          else if (seconds >= MAX_SECONDS) finishRecording(job);
        }, 200);
        $('voiceTimer').textContent = '00:00';
        setPhase('recording');
        showMessage('正在听，最长 3 分钟。说完点击“停止并检索”；勾选“停顿后自动发送”时，停顿约 1.5 秒后结束。');
      } catch (error) { fail(job, error); }
    }

    async function recognizeFile(file) {
      if (!file || phase !== 'idle') return;
      if (!/\.wav$/i.test(file.name)) { showMessage('请选择 WAV 格式的录音文件。', true); return; }
      if (file.size > MAX_FILE_BYTES) { showMessage('录音文件过大，请截取 3 分钟以内的 WAV 录音。', true); return; }
      const job = newJob();
      setPhase('preparing');
      showMessage('正在读取录音，仅在本机处理…');
      try {
        const OfflineContext = environment.OfflineAudioContext || environment.webkitOfflineAudioContext;
        if (!OfflineContext) throw new Error('当前浏览器不支持读取录音，请使用新版 Edge 或 Chrome。');
        const bytes = await file.arrayBuffer();
        if (!isCurrent(job)) return;
        const decoder = new OfflineContext(1, 1, SAMPLE_RATE);
        let buffer;
        try { buffer = await decoder.decodeAudioData(bytes); } catch (_) { throw new Error('无法读取这个 WAV 文件，请检查录音是否完整。'); }
        if (!isCurrent(job)) return;
        if (buffer.duration > MAX_SECONDS) throw new Error('录音超过 3 分钟，请截取一个问题后再试。');
        const samples = await resample(buffer);
        await transcribe(job, samples);
      } catch (error) { fail(job, error); }
      finally { fileInput.value = ''; }
    }

    mainButton.addEventListener('click', startRecording);
    cancelButton.addEventListener('click', () => cancel());
    fileButton.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => recognizeFile(fileInput.files[0]));
    input.addEventListener('input', () => cancel('已切换为文字输入，语音已取消。'));
    $('clearSearch').addEventListener('click', () => cancel('已取消语音输入。'));
    doc.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && current) { event.preventDefault(); cancel(); }
    });
    const destroy = () => {
      cancel('');
      destroyed = true;
      if (pollTimer) environment.clearTimeout(pollTimer);
      if (statusController) statusController.abort();
    };
    environment.addEventListener('pagehide', destroy);
    checkStatus();
    return { cancel, destroy, recognizeFile };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { encodeWav, createVoiceActivity, initVoiceSearch };
  else root.initVoiceSearch = initVoiceSearch;
})(typeof window !== 'undefined' ? window : globalThis);
