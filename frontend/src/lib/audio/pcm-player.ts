export const OUTPUT_SAMPLE_RATE = 24000;

/**
 * Plays Maya's 24 kHz, 16-bit, mono PCM through a jitter buffer
 * (see public/worklets/pcm-player.js).
 */
export class PcmPlayer {
  private constructor(
    private readonly context: AudioContext,
    private readonly node: AudioWorkletNode,
    readonly analyser: AnalyserNode,
  ) {}

  /** Call from a user gesture: the AudioContext is created before the first await. */
  static async create(onPlayingChange: (playing: boolean) => void): Promise<PcmPlayer> {
    const context = new AudioContext({
      sampleRate: OUTPUT_SAMPLE_RATE,
      latencyHint: "interactive",
    });
    try {
      await context.audioWorklet.addModule("/worklets/pcm-player.js");
      const node = new AudioWorkletNode(context, "pcm-player", {
        numberOfInputs: 0,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        processorOptions: { prefillMs: 120, idleMs: 250 },
      });
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      node.connect(analyser).connect(context.destination);
      node.port.onmessage = (event: MessageEvent<{ type: "playing" | "idle" }>) =>
        onPlayingChange(event.data.type === "playing");
      await context.resume();
      return new PcmPlayer(context, node, analyser);
    } catch (error) {
      await context.close();
      throw error;
    }
  }

  push(pcm: ArrayBuffer): void {
    this.node.port.postMessage({ type: "push", buffer: pcm }, [pcm]);
  }

  /** The turn ended: play whatever is buffered, even if it is shorter than the prefill. */
  flush(): void {
    this.node.port.postMessage({ type: "flush" });
  }

  /** Barge-in: drop all queued audio immediately. */
  clear(): void {
    this.node.port.postMessage({ type: "clear" });
  }

  close(): void {
    this.node.port.onmessage = null;
    void this.context.close();
  }
}
