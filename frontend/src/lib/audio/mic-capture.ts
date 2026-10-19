/**
 * Microphone capture as 16 kHz, 16-bit, mono PCM frames (see public/worklets/pcm-capture.js).
 *
 * The AudioContext runs at the device's native rate and the worklet downsamples, because
 * some browsers refuse to connect a microphone to a context with a different sample rate.
 */
export class MicCapture {
  private constructor(
    private readonly context: AudioContext,
    private readonly stream: MediaStream,
    private readonly node: AudioWorkletNode,
    readonly analyser: AnalyserNode,
  ) {}

  /** Call from a user gesture: the AudioContext is created before the first await. */
  static async open(onFrame: (pcm: ArrayBuffer) => void): Promise<MicCapture> {
    const context = new AudioContext({ latencyHint: "interactive" });
    let stream: MediaStream | null = null;
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new MicUnavailableError("Voice needs a secure page (https or localhost).");
      }
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      await context.audioWorklet.addModule("/worklets/pcm-capture.js");
      const source = context.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(context, "pcm-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        channelCount: 1,
        channelCountMode: "explicit",
      });
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      // The worklet must reach the destination to be processed; mute it so the caller
      // doesn't hear themselves.
      const mute = context.createGain();
      mute.gain.value = 0;
      source.connect(analyser);
      source.connect(node);
      node.connect(mute).connect(context.destination);
      node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => onFrame(event.data);
      await context.resume();
      return new MicCapture(context, stream, node, analyser);
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop());
      await context.close();
      throw error;
    }
  }

  close(): void {
    this.node.port.onmessage = null;
    this.stream.getTracks().forEach((track) => track.stop());
    void this.context.close();
  }
}

export class MicUnavailableError extends Error {}

export function describeMicError(error: unknown): string {
  if (error instanceof MicUnavailableError) return error.message;
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") {
      return "Microphone access is blocked. Allow it in your browser's site settings, or switch to text mode.";
    }
    if (error.name === "NotFoundError") return "No microphone was found.";
    if (error.name === "NotReadableError") {
      return "The microphone is in use by another app.";
    }
  }
  return "Could not start the microphone.";
}
