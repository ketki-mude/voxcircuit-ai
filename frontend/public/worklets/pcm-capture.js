/* global sampleRate, registerProcessor, AudioWorkletProcessor */
// Captures mono microphone audio, resamples it to 16 kHz, and posts 16-bit PCM frames
// (ArrayBuffers of FRAME_SAMPLES little-endian int16 samples) to the main thread.

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 640; // 40 ms at 16 kHz.

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE; // Input samples per output sample.
    this.sum = 0; // Weighted sum of input samples for the output sample being built.
    this.weight = 0; // How much input (in samples, fractional) that sum covers.
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.length = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      // Box-filter downsampling: each output sample is the average of the input it spans.
      let remaining = 1;
      while (remaining > 0) {
        const take = Math.min(remaining, this.ratio - this.weight);
        this.sum += channel[i] * take;
        this.weight += take;
        remaining -= take;
        if (this.weight >= this.ratio - 1e-9) {
          this.emit(this.sum / this.ratio);
          this.sum = 0;
          this.weight = 0;
        }
      }
    }
    return true;
  }

  emit(value) {
    const clamped = Math.max(-1, Math.min(1, value));
    this.frame[this.length++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    if (this.length === FRAME_SAMPLES) {
      const buffer = this.frame.buffer;
      this.port.postMessage(buffer, [buffer]);
      this.frame = new Int16Array(FRAME_SAMPLES);
      this.length = 0;
    }
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
