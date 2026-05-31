import React from "react";
import {Composition} from "remotion";
import {FinanceBrief} from "./FinanceBrief";
import {sampleInput} from "./sampleInput";
import type {RemotionRenderInput} from "./types";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="FinanceBrief"
      component={FinanceBrief}
      width={sampleInput.video.width}
      height={sampleInput.video.height}
      fps={sampleInput.video.fps}
      durationInFrames={Math.ceil(sampleInput.video.total_duration_seconds * sampleInput.video.fps)}
      defaultProps={sampleInput}
      calculateMetadata={({props}) => {
        const input = props as RemotionRenderInput;
        const fps = input.video.fps || 30;
        return {
          width: input.video.width || 1080,
          height: input.video.height || 1920,
          fps,
          durationInFrames: Math.max(1, Math.ceil(input.video.total_duration_seconds * fps)),
        };
      }}
    />
  );
};
