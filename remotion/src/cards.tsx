import React from "react";
import {getLength} from "@remotion/paths";
import {Arrow} from "@remotion/shapes";
import {
  AbsoluteFill,
  Easing,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type {
  RemotionBackgroundSegment,
  RemotionChartData,
  RemotionRenderInput,
  RemotionSceneInput,
  RemotionTextBeat,
} from "./types";

const colors = {
  ink: "#f8fafc",
  muted: "#94a3b8",
  soft: "#cbd5e1",
  panel: "#101827",
  panel2: "#172033",
  line: "#22d3ee",
  green: "#22c55e",
  red: "#fb7185",
  yellow: "#facc15",
  blue: "#60a5fa",
  black: "#020617",
  border: "rgba(148, 163, 184, 0.25)",
};

type CardProps = {
  input: RemotionRenderInput;
  scene: RemotionSceneInput;
  activeTextBeat?: RemotionTextBeat;
  activeText: string;
  textBeats: RemotionTextBeat[];
};

type SceneCardProps = {
  input: RemotionRenderInput;
  scene: RemotionSceneInput;
};

export const BackgroundTimeline: React.FC<{input: RemotionRenderInput}> = ({input}) => {
  const segments = input.background_segments || [];
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const motion = interpolate(frame, [0, 240], [0, 1], {
    extrapolateRight: "extend",
  });

  return (
    <AbsoluteFill style={backgroundLayer}>
      {segments.length ? (
        segments.map((segment, index) => {
          const from = Math.max(0, Math.round(segment.start_seconds * fps));
          const durationInFrames = Math.max(1, Math.round(segment.duration_seconds * fps));
          return (
            <Sequence key={`${segment.public_path}-${index}-${from}`} from={from} durationInFrames={durationInFrames}>
              <BackgroundSegment segment={segment} index={index} durationInFrames={durationInFrames} />
            </Sequence>
          );
        })
      ) : (
        <div
          style={{
            ...generatedBackdrop,
            transform: `scale(1.08) translate3d(${Math.sin(motion * Math.PI * 2) * 26}px, ${
              Math.cos(motion * Math.PI * 2) * 18
            }px, 0)`,
          }}
        />
      )}
      <div style={backgroundShade} />
    </AbsoluteFill>
  );
};

const BackgroundSegment: React.FC<{
  segment: RemotionBackgroundSegment;
  index: number;
  durationInFrames: number;
}> = ({segment, index, durationInFrames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const transitionFrames = Math.max(4, Math.round((segment.transition_duration_seconds || 0.45) * fps));
  const fadeIn = index === 0
    ? 1
    : interpolate(frame, [0, transitionFrames], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
  const fadeOut = interpolate(frame, [durationInFrames - transitionFrames, durationInFrames], [1, 0.82], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const scale = segment.segment_type === "intro" ? 1 : 1.05;

  return (
    <AbsoluteFill>
      <OffthreadVideo
        src={staticFile(segment.public_path)}
        muted
        startFrom={0}
        style={{
          ...backgroundVideo,
          opacity: fadeIn * fadeOut * (segment.segment_type === "intro" ? 0.78 : 0.92),
          transform: `scale(${scale})`,
        }}
      />
    </AbsoluteFill>
  );
};

export const SceneCard: React.FC<SceneCardProps> = ({input, scene}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const textBeats = sceneTextBeats(input, scene);
  const activeTextBeat = activeSceneTextBeat(input, scene, frame, fps);
  const activeText = revealedBeatText(scene, activeTextBeat, frame, fps);
  const enter = spring({frame, fps, config: {damping: 24, stiffness: 120}});
  const fade = interpolate(frame, [0, 10], [0, 1], {extrapolateRight: "clamp"});
  const exit = interpolate(frame, [durationInFrames - 12, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const style: React.CSSProperties = {
    opacity: fade * exit,
    transform: `translateY(${(1 - enter) * 38}px) scale(${0.985 + enter * 0.015})`,
  };

  return (
    <AbsoluteFill style={backgroundStyle(input, scene)}>
      <Noise />
      <TopBar input={input} scene={scene} />
      <div style={{...safeArea, ...style}}>
        <CardBody
          input={input}
          scene={scene}
          activeTextBeat={activeTextBeat}
          activeText={activeText}
          textBeats={textBeats}
        />
      </div>
      <Footer input={input} scene={scene} />
    </AbsoluteFill>
  );
};

const CardBody: React.FC<CardProps> = ({input, scene, activeTextBeat, activeText, textBeats}) => {
  switch (scene.card_type) {
    case "hook_card":
      return <HookCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} />;
    case "price_move_card":
      return <PriceMoveCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} />;
    case "news_headline_card":
      return <HeadlineCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} label="CATALYST" />;
    case "chart_card":
      return <ChartCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} />;
    case "three_bullet_card":
      return <BulletStackCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} />;
    case "earnings_card":
      return <MetricCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} label="EARNINGS" />;
    case "analyst_card":
      return <HeadlineCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} label="ANALYST CALL" />;
    case "volume_spike_card":
      return <MetricCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} label="VOLUME CHECK" />;
    case "risk_card":
      return <RiskCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} />;
    case "bull_bear_card":
      return <BullBearCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} />;
    case "takeaway_card":
      return <TakeawayCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} />;
    case "outro_disclaimer_card":
      return <OutroCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} />;
    default:
      return <HeadlineCard input={input} scene={scene} activeTextBeat={activeTextBeat} activeText={activeText} textBeats={textBeats} label={scene.scene_type.toUpperCase()} />;
  }
};

const HookCard: React.FC<CardProps> = ({input, scene, activeText, textBeats}) => {
  const pct = parsePercent(input.video.change_pct);
  const move = trendColor(pct);
  return (
    <div style={stack}>
      <Eyebrow color={move}>MARKET BRIEF</Eyebrow>
      <h1 style={{...heroTitle, fontSize: fitFont(scene.headline, 104, 74)}}>{scene.headline}</h1>
      <div style={hookMetricRow}>
        <MetricPill label="Ticker" value={input.video.ticker} color={colors.line} />
        <MetricPill label="Move" value={input.video.change_pct} color={move} />
      </div>
      {scene.chart ? <PriceChart chart={scene.chart} input={input} scene={scene} textBeats={textBeats} compact /> : null}
      <p style={lede}>{displayDetail(scene, activeText, textBeats.length > 0) || input.video.company}</p>
      <BulletList scene={scene} bullets={displayBullets(scene, textBeats).slice(0, 2)} textBeats={textBeats} />
      <AssetStrip scene={scene} />
    </div>
  );
};

const PriceMoveCard: React.FC<CardProps> = ({input, scene, activeText, textBeats}) => {
  const pct = parsePercent(input.video.change_pct);
  const move = trendColor(pct);
  const arrow = pct >= 0 ? "UP" : "DOWN";
  const arrowDirection = pct >= 0 ? "up" : "down";
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const count = spring({frame: frame - 8, fps, config: {damping: 22}});
  const shownPct = pct * Math.max(0, Math.min(1, count));
  return (
    <div style={stack}>
      <Eyebrow color={move}>PRICE ACTION</Eyebrow>
      <div style={priceGrid}>
        <div>
          <div style={priceLabel}>Last price</div>
          <div style={priceText}>{input.video.price}</div>
        </div>
        <div style={{...directionBlock, borderColor: move}}>
          <div style={shapeArrowWrap}>
            <Arrow
              length={116}
              headWidth={76}
              headLength={48}
              shaftWidth={34}
              direction={arrowDirection}
              cornerRadius={8}
              fill={move}
              style={{width: 116, height: 116}}
            />
          </div>
          <div style={{...directionText, color: move}}>{arrow}</div>
          <div style={{...bigPercent, color: move}}>{formatPercent(shownPct)}</div>
        </div>
      </div>
      <h2 style={sectionTitle}>{scene.headline}</h2>
      <p style={lede}>{displayDetail(scene, activeText, textBeats.length > 0)}</p>
      <BulletList scene={scene} bullets={displayBullets(scene, textBeats).slice(0, 2)} textBeats={textBeats} />
      <AssetStrip scene={scene} />
    </div>
  );
};

const HeadlineCard: React.FC<CardProps & {label: string}> = ({scene, activeText, textBeats, label}) => (
  <div style={stack}>
    <Eyebrow color={colors.yellow}>{label}</Eyebrow>
    <h2 style={{...sectionTitle, fontSize: fitFont(scene.headline, 82, 58)}}>{scene.headline}</h2>
    <p style={lede}>{displayDetail(scene, activeText, textBeats.length > 0)}</p>
    <BulletList scene={scene} bullets={displayBullets(scene, textBeats)} textBeats={textBeats} />
    <AssetStrip scene={scene} />
  </div>
);

const BulletStackCard: React.FC<CardProps> = ({scene, activeText, textBeats}) => (
  <div style={stack}>
    <Eyebrow color={colors.blue}>THREE THINGS</Eyebrow>
    <h2 style={sectionTitle}>{scene.headline}</h2>
    <p style={lede}>{displayDetail(scene, activeText, textBeats.length > 0)}</p>
    <BulletList scene={scene} bullets={displayBullets(scene, textBeats)} textBeats={textBeats} />
    <AssetStrip scene={scene} />
  </div>
);

const MetricCard: React.FC<CardProps & {label: string}> = ({input, scene, activeText, textBeats, label}) => {
  const pct = parsePercent(input.video.change_pct);
  const move = trendColor(pct);
  return (
    <div style={stack}>
      <Eyebrow color={move}>{label}</Eyebrow>
      <div style={tileGrid}>
        <MetricTile label="Price" value={input.video.price} />
        <MetricTile label="Move" value={input.video.change_pct} accent={move} />
      </div>
      <h2 style={sectionTitle}>{scene.headline}</h2>
      <p style={lede}>{displayDetail(scene, activeText, textBeats.length > 0)}</p>
      <BulletList scene={scene} bullets={displayBullets(scene, textBeats)} textBeats={textBeats} />
    </div>
  );
};

const RiskCard: React.FC<CardProps> = ({scene, activeText, textBeats}) => (
  <div style={stack}>
    <Eyebrow color={colors.red}>RISK RADAR</Eyebrow>
    <div style={warningBox}>
      <div style={warningIcon}>!</div>
      <h2 style={{...sectionTitle, margin: 0}}>{scene.headline}</h2>
    </div>
    <p style={lede}>{displayDetail(scene, activeText, textBeats.length > 0)}</p>
    <BulletList scene={scene} bullets={displayBullets(scene, textBeats)} textBeats={textBeats} />
  </div>
);

const BullBearCard: React.FC<CardProps> = ({scene, activeText, textBeats}) => {
  const bullets = displayBullets(scene, textBeats);
  if (textBeats.length > 0) {
    return (
      <div style={stack}>
        <Eyebrow color={colors.line}>BULL VS BEAR</Eyebrow>
        <h2 style={sectionTitle}>{scene.headline}</h2>
        <p style={lede}>{displayDetail(scene, activeText, true)}</p>
        <BulletList scene={scene} bullets={displayBullets(scene, textBeats)} textBeats={textBeats} />
      </div>
    );
  }
  return (
    <div style={stack}>
      <Eyebrow color={colors.line}>BULL VS BEAR</Eyebrow>
      <h2 style={sectionTitle}>{scene.headline}</h2>
      <div style={splitGrid}>
        <SidePanel title="Bull case" text={bullets[0] || scene.caption_text} color={colors.green} />
        <SidePanel title="Bear case" text={bullets[1] || scene.subheadline || scene.caption_text} color={colors.red} />
      </div>
    </div>
  );
};

const TakeawayCard: React.FC<CardProps> = ({scene, activeText, textBeats}) => (
  <div style={stack}>
    <Eyebrow color={colors.green}>TAKEAWAY</Eyebrow>
    <h2 style={{...heroTitle, fontSize: fitFont(scene.headline, 86, 62)}}>{scene.headline}</h2>
    <p style={lede}>{displayDetail(scene, activeText, textBeats.length > 0)}</p>
    <BulletList scene={scene} bullets={displayBullets(scene, textBeats)} textBeats={textBeats} />
  </div>
);

const OutroCard: React.FC<CardProps> = ({input, scene, activeText, textBeats}) => (
  <div style={{...stack, justifyContent: "center"}}>
    <Eyebrow color={colors.line}>MARKET BRIEF AGENTS</Eyebrow>
    <h2 style={sectionTitle}>{scene.headline || "Follow the facts, not the noise"}</h2>
    <p style={lede}>{displayDetail(scene, activeText, textBeats.length > 0) || input.video.disclaimer}</p>
    <div style={outroLine} />
    <p style={smallText}>{input.video.ticker} | {input.video.date}</p>
  </div>
);

const ChartCard: React.FC<CardProps> = ({input, scene, activeText, textBeats}) => {
  const chart = scene.chart;
  if (hasEarlierChart(input, scene)) {
    return (
      <div style={stack}>
        <Eyebrow color={trendColor(parsePercent(input.video.change_pct))}>PRICE ACTION</Eyebrow>
        <h2 style={sectionTitle}>{scene.headline || `${input.video.ticker} ${input.video.change_pct}`}</h2>
        <p style={lede}>{displayDetail(scene, activeText, textBeats.length > 0)}</p>
        <div style={tileGrid}>
          <MetricTile label="Latest" value={input.video.price} />
          <MetricTile label="Move" value={input.video.change_pct} accent={trendColor(parsePercent(input.video.change_pct))} />
        </div>
        <BulletList scene={scene} bullets={displayBullets(scene, textBeats)} textBeats={textBeats} />
      </div>
    );
  }
  return (
    <div style={stack}>
      <Eyebrow color={colors.line}>CHART CHECK</Eyebrow>
      <h2 style={sectionTitle}>{chart?.title || scene.headline || "Price context"}</h2>
      <p style={lede}>{displayDetail(scene, activeText, textBeats.length > 0)}</p>
      <PriceChart chart={chart} input={input} scene={scene} textBeats={textBeats} />
      <p style={chartSource}>{chart?.source || "latest market snapshot"}</p>
    </div>
  );
};

const PriceChart: React.FC<{
  chart?: RemotionChartData | null;
  input: RemotionRenderInput;
  scene: RemotionSceneInput;
  textBeats: RemotionTextBeat[];
  compact?: boolean;
}> = ({
  chart,
  input,
  scene,
  textBeats,
  compact = false,
}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const points = chart?.points.length ? chart.points : fallbackPoints(input);
  const closes = points.map((point) => point.close);
  const volumes = points.map((point) => point.volume || 0);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const volumeMax = Math.max(...volumes, 1);
  const w = 860;
  const h = compact ? 320 : 520;
  const padX = 42;
  const padY = compact ? 42 : 58;
  const innerW = w - padX * 2;
  const innerH = h - padY * 2;
  const yFor = (value: number) => padY + innerH - ((value - min) / Math.max(0.01, max - min)) * innerH;
  const xFor = (index: number) => padX + (index / Math.max(1, points.length - 1)) * innerW;
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xFor(index).toFixed(1)} ${yFor(point.close).toFixed(1)}`)
    .join(" ");
  const length = safePathLength(path);
  const chartBeats = textBeats.filter((beat) => beat.beat_type === "chart_annotation" || beat.scene_index === scene.scene_index);
  const beatStart = chartBeats[0]?.start_seconds ?? scene.start_seconds;
  const beatEnd = chartBeats[chartBeats.length - 1]?.end_seconds ?? scene.end_seconds;
  const openingDelayFrames = scene.card_type === "hook_card"
    ? Math.round(Math.min(2.8, scene.duration_seconds * 0.45) * fps)
    : 0;
  const revealStart = Math.max(6, openingDelayFrames, Math.round((beatStart - scene.start_seconds) * fps) + 4);
  const revealEnd = Math.max(revealStart + 12, Math.min(durationInFrames - 10, Math.round((beatEnd - scene.start_seconds) * fps)));
  const reveal = interpolate(frame, [revealStart, revealEnd], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  const last = points[points.length - 1];
  const pct = parsePercent(input.video.change_pct);
  const move = trendColor(pct);

  return (
    <div style={{...chartPanel, padding: compact ? "20px 24px 18px" : chartPanel.padding}}>
      <div style={chartStatRow}>
        <div>
          <div style={priceLabel}>Latest</div>
          <div style={{...chartPrice, fontSize: compact ? 42 : 52, color: move}}>{formatMoney(last.close)}</div>
        </div>
        <div style={{textAlign: "right"}}>
          <div style={priceLabel}>Move</div>
          <div style={{...chartMove, fontSize: compact ? 36 : 42, color: move}}>{input.video.change_pct}</div>
        </div>
      </div>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{...chartSvg, height: h}}>
        {[0, 1, 2, 3].map((line) => {
          const y = padY + (line / 3) * innerH;
          return <line key={line} x1={padX} x2={w - padX} y1={y} y2={y} stroke="rgba(148,163,184,0.18)" strokeWidth={2} />;
        })}
        {points.map((point, index) => {
          const barH = Math.max(6, ((point.volume || 0) / volumeMax) * (compact ? 52 : 86));
          const barReveal = interpolate(frame - index * 2, [10, 28], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          return (
            <rect
              key={`${point.date}-${index}`}
              x={xFor(index) - 9}
              y={h - padY - barH * barReveal}
              width={18}
              height={barH * barReveal}
              rx={4}
              fill="rgba(34,211,238,0.22)"
            />
          );
        })}
        <path
          d={path}
          fill="none"
          stroke="rgba(34,211,238,0.18)"
          strokeWidth={16}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={length}
          strokeDashoffset={length * (1 - reveal)}
        />
        <path
          d={path}
          fill="none"
          stroke={move}
          strokeWidth={6}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={length}
          strokeDashoffset={length * (1 - reveal)}
        />
        <circle
          cx={xFor(points.length - 1)}
          cy={yFor(last.close)}
          r={interpolate(reveal, [0.75, 1], [0, 13], {extrapolateLeft: "clamp", extrapolateRight: "clamp"})}
          fill={move}
          stroke="#f8fafc"
          strokeWidth={4}
        />
      </svg>
    </div>
  );
};

const TopBar: React.FC<SceneCardProps> = ({input, scene}) => {
  const pct = parsePercent(input.video.change_pct);
  const move = trendColor(pct);
  return (
    <div style={topBar}>
      <div style={brandBox}>
        <div style={brand}>Market Brief Agents</div>
        <div style={subBrand}>{input.video.company}</div>
      </div>
      <div style={tickerBox}>
        <div style={ticker}>{input.video.ticker}</div>
        <div style={{...tickerMove, color: move}}>{input.video.change_pct}</div>
      </div>
      <Progress scene={scene} color={move} />
    </div>
  );
};

const Footer: React.FC<SceneCardProps> = ({input}) => (
  <div style={footer}>
    <span>{input.video.date}</span>
    <span>{input.video.disclaimer}</span>
  </div>
);

const Progress: React.FC<{scene: RemotionSceneInput; color: string}> = ({scene, color}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const local = frame / fps;
  const progress = Math.max(0, Math.min(1, local / Math.max(0.1, scene.duration_seconds)));
  return (
    <div style={progressTrack}>
      <div style={{...progressFill, width: `${progress * 100}%`, backgroundColor: color}} />
    </div>
  );
};

const BulletList: React.FC<{
  scene: RemotionSceneInput;
  bullets: string[];
  textBeats: RemotionTextBeat[];
}> = ({scene, bullets, textBeats}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div style={bulletWrap}>
      {bullets.filter(Boolean).slice(0, 3).map((bullet, index) => {
        const relatedBeat = textBeats.find((beat) => sameText(beat.text, bullet)) || textBeats[index];
        const startFrame = relatedBeat
          ? Math.max(0, Math.round((relatedBeat.start_seconds - scene.start_seconds) * fps))
          : 8 + index * 8;
        const rawShow = spring({frame: frame - startFrame, fps, config: {damping: 18}});
        const show = frame >= startFrame ? rawShow : 0;
        return (
          <div
            key={`${bullet}-${index}`}
            style={{
              ...bulletRow,
              opacity: show,
              transform: `translateX(${(1 - show) * -34}px)`,
            }}
          >
            <span style={bulletDot}>{index + 1}</span>
            <span>{bullet}</span>
          </div>
        );
      })}
    </div>
  );
};

const MetricPill: React.FC<{label: string; value: string; color: string}> = ({label, value, color}) => (
  <div style={{...metricPill, borderColor: color}}>
    <span style={metricLabel}>{label}</span>
    <span style={{...metricValue, color}}>{value}</span>
  </div>
);

const MetricTile: React.FC<{label: string; value: string; accent?: string}> = ({
  label,
  value,
  accent = colors.line,
}) => (
  <div style={metricTile}>
    <div style={metricLabel}>{label}</div>
    <div style={{...metricTileValue, color: accent}}>{value}</div>
  </div>
);

const SidePanel: React.FC<{title: string; text: string; color: string}> = ({title, text, color}) => (
  <div style={{...sidePanel, borderColor: color}}>
    <div style={{...metricLabel, color}}>{title}</div>
    <div style={sideText}>{text}</div>
  </div>
);

const AssetStrip: React.FC<{scene: RemotionSceneInput}> = ({scene}) => {
  return null;
};

const Eyebrow: React.FC<{children: React.ReactNode; color: string}> = ({children, color}) => (
  <div style={{...eyebrow, color}}>{children}</div>
);

const Noise: React.FC = () => <div style={noise} />;

const safePathLength = (path: string): number => {
  try {
    return Math.max(1, getLength(path));
  } catch {
    return 1;
  }
};

const activeSceneTextBeat = (
  input: RemotionRenderInput,
  scene: RemotionSceneInput,
  frame: number,
  fps: number,
): RemotionTextBeat | undefined => {
  const globalSeconds = scene.start_seconds + frame / fps;
  return sceneTextBeats(input, scene).find((beat) => {
    return globalSeconds >= beat.start_seconds && globalSeconds < beat.end_seconds;
  });
};

const revealedBeatText = (
  scene: RemotionSceneInput,
  beat: RemotionTextBeat | undefined,
  frame: number,
  fps: number,
): string => {
  if (!beat) {
    return "";
  }
  const globalSeconds = scene.start_seconds + frame / fps;
  const duration = Math.max(0.4, beat.end_seconds - beat.start_seconds);
  const progress = Math.max(0, Math.min(1, (globalSeconds - beat.start_seconds - 0.18) / (duration * 0.82)));
  if (progress <= 0) {
    return "";
  }
  const words = beat.text.trim().split(/\s+/).filter(Boolean);
  const wordCount = Math.max(1, Math.ceil(words.length * progress));
  return words.slice(0, wordCount).join(" ");
};

const sceneTextBeats = (input: RemotionRenderInput, scene: RemotionSceneInput): RemotionTextBeat[] => {
  const beats = input.text_beats || [];
  return beats.filter((beat) => beat.scene_index === scene.scene_index);
};

const hasEarlierChart = (input: RemotionRenderInput, scene: RemotionSceneInput): boolean =>
  input.scenes.some((candidate) => {
    return candidate.scene_index < scene.scene_index && Boolean(candidate.chart);
  });

const displayDetail = (scene: RemotionSceneInput, activeText: string, beatDriven: boolean): string => {
  if (beatDriven) {
    const staticDetail = scene.subheadline || "";
    return staticDetail && !sameText(staticDetail, scene.headline) ? staticDetail : "";
  }
  return scene.detail_text || scene.subheadline || scene.caption_text || scene.headline;
};

const displayBullets = (scene: RemotionSceneInput, textBeats: RemotionTextBeat[]): string[] => {
  if (textBeats.length > 0) {
    return textBeats
      .map((beat) => beat.text.trim())
      .filter((text, index, all) => text.length > 0 && all.indexOf(text) === index)
      .slice(0, 3);
  }
  const values = scene.bullets.length
    ? scene.bullets
    : [scene.detail_text, scene.subheadline, scene.caption_text];
  const detail = displayDetail(scene, "", false).toLowerCase();
  return values
    .map((value) => value.trim())
    .filter((value, index, all) => {
      const lower = value.toLowerCase();
      return value.length > 0 && lower !== detail && all.indexOf(value) === index;
    })
    .slice(0, 3);
};

const sameText = (left: string, right: string): boolean =>
  left.trim().toLowerCase() === right.trim().toLowerCase();

const fallbackPoints = (input: RemotionRenderInput) => {
  const price = parseMoney(input.video.price);
  const pct = parsePercent(input.video.change_pct);
  const previous = pct === -100 ? price : price / (1 + pct / 100);
  return Array.from({length: 8}, (_, index) => {
    const progress = index / 7;
    const close = previous + (price - previous) * progress;
    return {
      date: `${index + 1}`,
      close,
      volume: 1000 + index * 120,
      change_percent: index === 7 ? pct : 0,
    };
  });
};

const parseMoney = (value: string): number => {
  const parsed = Number(value.replace(/[^0-9.-]+/g, ""));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 100;
};

const parsePercent = (value: string): number => {
  const parsed = Number(value.replace(/[^0-9.-]+/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
};

const formatPercent = (value: number): string => `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
const formatMoney = (value: number): string => `$${value.toFixed(2)}`;
const trendColor = (pct: number): string => (pct >= 0 ? colors.green : colors.red);
const fitFont = (text: string, max: number, min: number): number => {
  const length = text.length;
  if (length < 34) {
    return max;
  }
  if (length > 78) {
    return min;
  }
  return Math.round(max - ((length - 34) / 44) * (max - min));
};

const backgroundStyle = (input: RemotionRenderInput, scene: RemotionSceneInput): React.CSSProperties => {
  const pct = parsePercent(input.video.change_pct);
  const accent = scene.card_type === "risk_card" ? colors.red : trendColor(pct);
  return {
    background: "transparent",
    color: colors.ink,
    fontFamily:
      'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  };
};

const backgroundLayer: React.CSSProperties = {
  position: "absolute",
  inset: 0,
  overflow: "hidden",
};

const backgroundVideo: React.CSSProperties = {
  width: "100%",
  height: "100%",
  objectFit: "cover",
  filter: "saturate(1.04) contrast(1.04) brightness(0.86)",
};

const generatedBackdrop: React.CSSProperties = {
  position: "absolute",
  inset: "-8%",
  background:
    "linear-gradient(115deg, rgba(34,211,238,0.22) 0%, transparent 28%), linear-gradient(245deg, rgba(37,99,235,0.24) 0%, transparent 34%), repeating-linear-gradient(100deg, rgba(255,255,255,0.05) 0 2px, transparent 2px 42px), linear-gradient(145deg, #020617 0%, #111827 48%, #042f2e 100%)",
};

const backgroundShade: React.CSSProperties = {
  position: "absolute",
  inset: 0,
  background:
    "linear-gradient(180deg, rgba(2,6,23,0.56) 0%, rgba(2,6,23,0.28) 44%, rgba(2,6,23,0.7) 100%)",
};

const safeArea: React.CSSProperties = {
  position: "absolute",
  left: 72,
  right: 72,
  top: 270,
  bottom: 214,
  padding: "34px 38px",
  background: "linear-gradient(135deg, rgba(2,6,23,0.46), rgba(15,23,42,0.34))",
  boxShadow: "0 34px 90px rgba(0,0,0,0.28)",
};

const stack: React.CSSProperties = {
  height: "100%",
  display: "flex",
  flexDirection: "column",
  justifyContent: "center",
  gap: 34,
};

const topBar: React.CSSProperties = {
  position: "absolute",
  left: 72,
  right: 72,
  top: 62,
  height: 138,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
};

const brand: React.CSSProperties = {
  fontSize: 38,
  fontWeight: 800,
};

const subBrand: React.CSSProperties = {
  marginTop: 8,
  fontSize: 24,
  color: colors.muted,
};

const brandBox: React.CSSProperties = {
  minWidth: 360,
  maxWidth: 580,
  padding: "18px 24px",
  border: `1px solid ${colors.border}`,
  background: "rgba(15,23,42,0.76)",
};

const tickerBox: React.CSSProperties = {
  minWidth: 210,
  padding: "18px 24px",
  border: `1px solid ${colors.border}`,
  background: "rgba(15,23,42,0.72)",
};

const ticker: React.CSSProperties = {
  fontSize: 36,
  fontWeight: 900,
  textAlign: "right",
};

const tickerMove: React.CSSProperties = {
  marginTop: 6,
  fontSize: 24,
  fontWeight: 800,
  textAlign: "right",
};

const progressTrack: React.CSSProperties = {
  position: "absolute",
  left: 0,
  right: 0,
  bottom: 0,
  height: 8,
  background: "rgba(148,163,184,0.18)",
};

const progressFill: React.CSSProperties = {
  height: "100%",
};

const footer: React.CSSProperties = {
  position: "absolute",
  left: 72,
  right: 72,
  bottom: 54,
  display: "flex",
  justifyContent: "space-between",
  gap: 28,
  color: colors.muted,
  fontSize: 20,
};

const eyebrow: React.CSSProperties = {
  fontSize: 26,
  fontWeight: 900,
  textTransform: "uppercase",
};

const heroTitle: React.CSSProperties = {
  margin: 0,
  lineHeight: 1.02,
  fontWeight: 950,
  textShadow: "0 5px 24px rgba(0,0,0,0.82)",
};

const sectionTitle: React.CSSProperties = {
  margin: 0,
  fontSize: 72,
  lineHeight: 1.06,
  fontWeight: 920,
  textShadow: "0 5px 24px rgba(0,0,0,0.82)",
};

const lede: React.CSSProperties = {
  margin: 0,
  maxWidth: 840,
  color: colors.soft,
  fontSize: 38,
  lineHeight: 1.24,
  fontWeight: 650,
  minHeight: 96,
  textShadow: "0 3px 18px rgba(0,0,0,0.82)",
};

const hookMetricRow: React.CSSProperties = {
  display: "flex",
  gap: 20,
};

const metricPill: React.CSSProperties = {
  minWidth: 220,
  padding: "22px 26px",
  border: "2px solid",
  background: "rgba(15,23,42,0.78)",
};

const metricLabel: React.CSSProperties = {
  display: "block",
  color: colors.muted,
  fontSize: 20,
  fontWeight: 800,
  textTransform: "uppercase",
};

const metricValue: React.CSSProperties = {
  display: "block",
  marginTop: 10,
  fontSize: 42,
  fontWeight: 950,
};

const priceGrid: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1.1fr",
  gap: 26,
  alignItems: "stretch",
};

const priceLabel: React.CSSProperties = {
  color: colors.muted,
  fontSize: 22,
  fontWeight: 800,
  textTransform: "uppercase",
};

const priceText: React.CSSProperties = {
  marginTop: 14,
  fontSize: 90,
  fontWeight: 950,
};

const directionBlock: React.CSSProperties = {
  padding: "30px 34px",
  border: "2px solid",
  background: "rgba(15,23,42,0.74)",
};

const shapeArrowWrap: React.CSSProperties = {
  height: 118,
  display: "flex",
  alignItems: "center",
  justifyContent: "flex-end",
};

const directionText: React.CSSProperties = {
  fontSize: 30,
  fontWeight: 900,
};

const bigPercent: React.CSSProperties = {
  marginTop: 14,
  fontSize: 98,
  lineHeight: 0.98,
  fontWeight: 950,
};

const bulletWrap: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 18,
};

const bulletRow: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "58px 1fr",
  alignItems: "center",
  gap: 20,
  padding: "24px 28px",
  border: `1px solid ${colors.border}`,
  background: "rgba(15,23,42,0.84)",
  color: colors.ink,
  fontSize: 34,
  lineHeight: 1.18,
  fontWeight: 760,
};

const bulletDot: React.CSSProperties = {
  width: 58,
  height: 58,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "rgba(34,211,238,0.18)",
  color: colors.line,
  fontSize: 24,
  fontWeight: 950,
};

const tileGrid: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 24,
};

const metricTile: React.CSSProperties = {
  padding: "34px 34px",
  border: `1px solid ${colors.border}`,
  background: "rgba(15,23,42,0.76)",
};

const metricTileValue: React.CSSProperties = {
  marginTop: 16,
  fontSize: 60,
  fontWeight: 950,
};

const warningBox: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "96px 1fr",
  alignItems: "center",
  gap: 26,
  padding: "32px 34px",
  border: `2px solid ${colors.red}`,
  background: "rgba(127,29,29,0.28)",
};

const warningIcon: React.CSSProperties = {
  width: 80,
  height: 80,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: colors.red,
  color: colors.black,
  fontSize: 54,
  fontWeight: 950,
};

const splitGrid: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 22,
};

const sidePanel: React.CSSProperties = {
  minHeight: 360,
  padding: "34px 30px",
  border: "2px solid",
  background: "rgba(15,23,42,0.76)",
};

const sideText: React.CSSProperties = {
  marginTop: 24,
  fontSize: 34,
  lineHeight: 1.18,
  fontWeight: 780,
};

const outroLine: React.CSSProperties = {
  width: 220,
  height: 8,
  background: colors.line,
};

const smallText: React.CSSProperties = {
  color: colors.muted,
  fontSize: 28,
  fontWeight: 750,
};

const chartPanel: React.CSSProperties = {
  padding: "28px 30px 24px",
  border: `1px solid ${colors.border}`,
  background: "rgba(248,250,252,0.97)",
  color: colors.black,
};

const chartStatRow: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  marginBottom: 6,
};

const chartPrice: React.CSSProperties = {
  marginTop: 8,
  fontSize: 52,
  fontWeight: 950,
};

const chartMove: React.CSSProperties = {
  marginTop: 8,
  fontSize: 42,
  fontWeight: 950,
};

const chartSvg: React.CSSProperties = {
  display: "block",
  width: "100%",
  height: 520,
};

const chartSource: React.CSSProperties = {
  margin: 0,
  color: colors.muted,
  fontSize: 22,
  fontWeight: 700,
};

const captionBox: React.CSSProperties = {
  position: "absolute",
  left: 96,
  right: 96,
  bottom: 128,
  minHeight: 116,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: "22px 28px",
  background: "rgba(2,6,23,0.86)",
  border: `1px solid ${colors.border}`,
};

const captionText: React.CSSProperties = {
  color: colors.ink,
  fontSize: 36,
  lineHeight: 1.12,
  fontWeight: 850,
  textAlign: "center",
};

const assetStrip: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 18,
};

const assetImage: React.CSSProperties = {
  width: "100%",
  maxHeight: 260,
  objectFit: "cover",
  border: `1px solid ${colors.border}`,
};

const noise: React.CSSProperties = {
  position: "absolute",
  inset: 0,
  opacity: 0.14,
  backgroundImage:
    "linear-gradient(rgba(255,255,255,0.045) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px)",
  backgroundSize: "54px 54px",
};
