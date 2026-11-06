'use strict';

// Transfer PCM in small batches; never feed microphone audio to the speakers.
class DefensePcmRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.used = 0;
    this.active = true;
    this.port.onmessage = ({ data }) => {
      if (data === 'stop') {
        this.active = false;
        if (this.used) this.port.postMessage(this.buffer.slice(0, this.used));
        this.port.postMessage('stopped');
      }
    };
  }
  process(inputs) {
    if (!this.active) return false;
    const channels = inputs[0];
    if (!channels || !channels.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let sample = 0;
      for (const channel of channels) sample += channel[i] || 0;
      this.buffer[this.used++] = sample / channels.length;
      if (this.used === this.buffer.length) {
        this.port.postMessage(this.buffer);
        this.buffer = new Float32Array(2048);
        this.used = 0;
      }
    }
    return true;
  }
}
registerProcessor('defense-pcm-recorder', DefensePcmRecorder);
