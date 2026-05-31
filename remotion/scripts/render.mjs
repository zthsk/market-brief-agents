import fs from "node:fs";
import path from "node:path";
import {bundle} from "@remotion/bundler";
import {renderMedia, selectComposition} from "@remotion/renderer";

const args = process.argv.slice(2);

const readArg = (name) => {
  const index = args.indexOf(name);
  if (index === -1 || index === args.length - 1) {
    return null;
  }
  return args[index + 1];
};

const input = readArg("--input");
const output = readArg("--output");
const publicDirArg = readArg("--public-dir");

if (!input || !output) {
  throw new Error("Usage: npm run render -- --input /path/input.json --output /path/video.mp4 --public-dir /path/public");
}

const inputPath = path.resolve(input);
const outputPath = path.resolve(output);
const inputProps = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const publicDir = path.resolve(publicDirArg || inputProps.public_dir || "public");

fs.mkdirSync(publicDir, {recursive: true});
fs.mkdirSync(path.dirname(outputPath), {recursive: true});

const serveUrl = await bundle({
  entryPoint: path.resolve("src/index.ts"),
  publicDir,
  webpackOverride: (config) => config,
});

const composition = await selectComposition({
  serveUrl,
  id: "FinanceBrief",
  inputProps,
});

await renderMedia({
  composition,
  serveUrl,
  codec: "h264",
  outputLocation: outputPath,
  inputProps,
  muted: true,
  enforceAudioTrack: false,
  imageFormat: "jpeg",
  crf: 18,
  x264Preset: "veryfast",
  concurrency: "25%",
  timeoutInMilliseconds: 300000,
});

console.log(
  JSON.stringify(
    {
      renderer: "remotion",
      compositionId: "FinanceBrief",
      outputPath,
      durationInFrames: composition.durationInFrames,
      fps: composition.fps,
      width: composition.width,
      height: composition.height,
    },
    null,
    2,
  ),
);
