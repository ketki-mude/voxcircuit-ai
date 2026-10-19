/* global sampleRate, registerProcessor, AudioWorkletProcessor */
// Plays 16-bit PCM chunks through a jitter buffer. Runs in an AudioContext whose sample
// rate matches the audio (24 kHz), so no resampling happens here.
//
// Messages in:  {type: "push", buffer: ArrayBuffer}  queue int16 samples
//               {type: "flush"}  the turn has ended: play what is buffered, even if short
//               {type: "clear"}  drop everything now (barge-in)
// Messages out: {type: "playing"} / {type: "idle"}

class PcmPlayerProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    // Wait for this much audio before starting, and again after an underrun, so network
    // jitter doesn't turn into crackles.
    this.prefill = Math.round((sampleRate * (opts.prefillMs ?? 120)) / 1000);
    // Report idle only after this much silence, so short underruns don't flicker.
    this.idleAfter = Math.round((sampleRate * (opts.idleMs ?? 250)) / 1000);
    this.maxBuffered = sampleRate * 120;
    this.chunks = [];
    this.offset = 0;
    this.buffered = 0;
    this.draining = false;
    this.flushRequested = false;
    this.silence = 0;
    this.reported = false;
    this.port.onmessage = (event) => this.onMessage(event.data);
  }

  onMessage(message) {
    if (message.type === "push") {
      const pcm = new Int16Array(message.buffer);
      if (pcm.length === 0 || this.buffered + pcm.length > this.maxBuffered) return;
      const samples = new Float32Array(pcm.length);
      for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 0x8000;
      this.chunks.push(samples);
      this.buffered += samples.length;
    } else if (message.type === "flush") {
      if (this.buffered > 0) this.flushRequested = true;
    } else if (message.type === "clear") {
      this.chunks = [];
      this.offset = 0;
      this.buffered = 0;
      this.draining = false;
      this.flushRequested = false;
      this.report(false);
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!this.draining && this.buffered > 0) {
      this.draining = this.buffered >= this.prefill || this.flushRequested;
    }
    let written = 0;
    if (this.draining) {
      while (written < out.length && this.chunks.length > 0) {
        const chunk = this.chunks[0];
        const count = Math.min(out.length - written, chunk.length - this.offset);
        out.set(chunk.subarray(this.offset, this.offset + count), written);
        written += count;
        this.offset += count;
        this.buffered -= count;
        if (this.offset === chunk.length) {
          this.chunks.shift();
          this.offset = 0;
        }
      }
      if (this.buffered === 0) {
        // Underrun or end of turn: buffer up again before playing more.
        this.draining = false;
        this.flushRequested = false;
      }
    }
    out.fill(0, written);
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);

    if (written > 0) {
      this.silence = 0;
      this.report(true);
    } else {
      this.silence += out.length;
      if (this.silence >= this.idleAfter) this.report(false);
    }
    return true;
  }

  report(playing) {
    if (playing !== this.reported) {
      this.reported = playing;
      this.port.postMessage({ type: playing ? "playing" : "idle" });
    }
  }
}

registerProcessor("pcm-player", PcmPlayerProcessor);
