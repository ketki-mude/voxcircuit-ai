"use client";

import { useEffect, useRef } from "react";

import type { Analysers } from "@/hooks/use-call";
import { cn } from "@/lib/utils";

export type WaveSource = "idle" | "mic" | "maya";

interface WaveformProps {
  getAnalysers: () => Analysers;
  /** Whose audio to draw: nobody (a still pattern), the caller's microphone, or Maya. */
  source: WaveSource;
}

// The still pattern, also the floor the live bars never drop below. Symmetric, peaking in the middle.
const RESTING = [
  0.16, 0.22, 0.34, 0.5, 0.7, 0.46, 0.32, 0.56, 0.82, 0.6, 0.4, 0.72, 0.95, 0.72, 0.4, 0.6, 0.82,
  0.56, 0.32, 0.46, 0.7, 0.5, 0.34, 0.22, 0.16,
];
const FLOOR = 0.08;
// Microphone audio is much quieter than Maya's, so it is drawn with more gain.
const GAIN = { mic: 9, maya: 4 };

export function Waveform({ getAnalysers, source }: WaveformProps) {
  const barsRef = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    const bars = barsRef.current;
    const setHeights = (heights: number[]) =>
      bars.forEach((bar, i) => {
        if (bar) bar.style.height = `${Math.round(heights[i] * 100)}%`;
      });

    if (source === "idle") {
      setHeights(RESTING);
      return;
    }

    const samples = new Float32Array(2048);
    const shown = RESTING.map(() => FLOOR);
    let frame = 0;
    const draw = () => {
      const analysers = getAnalysers();
      const analyser = source === "maya" ? analysers.player : analysers.mic;
      if (analyser) {
        const count = Math.min(analyser.fftSize, samples.length);
        const view = samples.subarray(0, count);
        analyser.getFloatTimeDomainData(view);
        const size = Math.floor(count / RESTING.length);
        const gain = source === "maya" ? GAIN.maya : GAIN.mic;
        RESTING.forEach((shape, i) => {
          let sum = 0;
          for (let j = i * size; j < (i + 1) * size; j++) sum += view[j] * view[j];
          const level = Math.min(1, Math.sqrt(sum / size) * gain);
          // Shape each bar like the resting pattern, rise fast and fall slowly.
          const target = Math.max(FLOOR, level * (0.45 + 0.55 * shape));
          shown[i] = target > shown[i] ? target : shown[i] * 0.88 + target * 0.12;
        });
      }
      setHeights(shown);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [getAnalysers, source]);

  return (
    <div
      role="img"
      aria-label={
        source === "maya"
          ? "Waveform of Maya's voice"
          : source === "mic"
            ? "Waveform of your microphone"
            : "Voice waveform"
      }
      className="flex h-36 items-center justify-center gap-1.5 sm:h-44"
    >
      {RESTING.map((height, i) => (
        <span
          key={i}
          ref={(bar) => {
            barsRef.current[i] = bar;
          }}
          style={{ height: `${Math.round(height * 100)}%` }}
          className={cn(
            "w-1.5 rounded-full transition-[height] duration-75 ease-out sm:w-2",
            source === "maya" ? "bg-wave" : source === "mic" ? "bg-wave/75" : "bg-wave/45",
          )}
        />
      ))}
    </div>
  );
}
