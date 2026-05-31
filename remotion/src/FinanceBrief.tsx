import React from "react";
import {AbsoluteFill, Sequence} from "remotion";
import {BackgroundTimeline, SceneCard} from "./cards";
import type {RemotionRenderInput} from "./types";

export const FinanceBrief: React.FC<RemotionRenderInput> = (input) => {
  const fps = input.video.fps || 30;

  return (
    <AbsoluteFill style={{backgroundColor: "#020617"}}>
      <BackgroundTimeline input={input} />
      {input.scenes.map((scene, index) => {
        const from = Math.max(0, Math.round(scene.start_seconds * fps));
        const next = input.scenes[index + 1];
        const plannedDuration = next
          ? Math.round(next.start_seconds * fps) - from
          : Math.round(scene.duration_seconds * fps);
        const durationInFrames = Math.max(1, plannedDuration);
        return (
          <Sequence key={`${scene.scene_index}-${scene.card_type || scene.scene_type}`} from={from} durationInFrames={durationInFrames}>
            <SceneCard input={input} scene={scene} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
