import React from "react";
import { Composition } from "remotion";
import { Launch } from "./Launch";
import { FPS, TOTAL } from "./timeline";

export const Root: React.FC = () => (
  <>
    <Composition id="Launch" component={Launch} durationInFrames={TOTAL} fps={FPS} width={1920} height={1080} />
    <Composition id="LaunchVertical" component={Launch} durationInFrames={TOTAL} fps={FPS} width={1080} height={1920} />
  </>
);
