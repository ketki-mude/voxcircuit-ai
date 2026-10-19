"use client";

import { useEffect, useRef } from "react";

import type { Analysers } from "@/hooks/use-call";
import { cn } from "@/lib/utils";

interface WaveformProps {
  getAnalysers: () => Analysers;
  /** Show Maya's audio when she is speaking, otherwise the caller's microphone. */
  speaking: boolean;
}

// Microphone audio is much quieter than Maya's, so it is drawn with more gain.
const GAIN = { mic: 4, player: 1.5 };

export function Waveform({ getAnalysers, speaking }: WaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const color = getComputedStyle(canvas).color;
    const samples = new Float32Array(1024);
    let frame = 0;

    const draw = () => {
      const ratio = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (canvas.width !== Math.round(width * ratio)) canvas.width = Math.round(width * ratio);
      if (canvas.height !== Math.round(height * ratio)) canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      context.strokeStyle = color;
      context.lineWidth = 2;
      context.lineJoin = "round";
      context.beginPath();

      const analysers = getAnalysers();
      const analyser = speaking ? analysers.player : analysers.mic;
      const middle = height / 2;
      if (analyser) {
        const count = Math.min(analyser.fftSize, samples.length);
        const view = samples.subarray(0, count);
        analyser.getFloatTimeDomainData(view);
        const gain = speaking ? GAIN.player : GAIN.mic;
        for (let i = 0; i < count; i++) {
          const x = (i / (count - 1)) * width;
          const y = middle - Math.max(-1, Math.min(1, view[i] * gain)) * middle * 0.9;
          if (i === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        }
      } else {
        context.moveTo(0, middle);
        context.lineTo(width, middle);
      }
      context.stroke();
      frame = requestAnimationFrame(draw);
    };

    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [getAnalysers, speaking]);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={speaking ? "Waveform of Maya's voice" : "Waveform of your microphone"}
      className={cn(
        "h-14 w-full rounded-lg bg-muted",
        speaking ? "text-primary" : "text-muted-foreground",
      )}
    />
  );
}
