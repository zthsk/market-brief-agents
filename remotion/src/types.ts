export type RemotionVideoMeta = {
  width: number;
  height: number;
  fps: number;
  total_duration_seconds: number;
  ticker: string;
  company: string;
  date: string;
  disclaimer: string;
  price: string;
  change_pct: string;
  direction: string;
};

export type RemotionTemplateMeta = {
  template_id?: string | null;
  template_name?: string | null;
  scene_order: string[];
};

export type RemotionCaptionBeat = {
  text: string;
  start_seconds: number;
  end_seconds: number;
  scene_index: number;
};

export type RemotionVisualBeat = {
  beat_type: string;
  start_seconds: number;
  end_seconds: number;
  payload: Record<string, unknown>;
};

export type RemotionTextBeat = {
  text: string;
  start_seconds: number;
  end_seconds: number;
  scene_index: number;
  beat_type: string;
};

export type RemotionBackgroundSegment = {
  public_path: string;
  source_path: string;
  start_seconds: number;
  duration_seconds: number;
  transition: string;
  transition_duration_seconds: number;
  segment_type: string;
};

export type RemotionMusicTrack = {
  source_path: string;
  public_path: string;
  volume: number;
  loop: boolean;
  start_seconds: number;
  duration_seconds: number;
};

export type RemotionPricePoint = {
  date: string;
  close: number;
  volume: number;
  change_percent: number;
};

export type RemotionChartData = {
  title: string;
  source: string;
  synthetic: boolean;
  points: RemotionPricePoint[];
};

export type RemotionSceneInput = {
  scene_index: number;
  scene_type: string;
  card_type?: string | null;
  slot_id?: string | null;
  visual_style?: string | null;
  motion?: string | null;
  caption_style?: string | null;
  start_seconds: number;
  end_seconds: number;
  duration_seconds: number;
  narration: string;
  headline: string;
  subheadline: string;
  detail_text: string;
  bullets: string[];
  caption_text: string;
  source_ids: string[];
  sources: Record<string, unknown>[];
  caption_beats: RemotionCaptionBeat[];
  visual_beats: RemotionVisualBeat[];
  chart?: RemotionChartData | null;
  asset_public_paths: string[];
  background_public_path?: string | null;
};

export type RemotionAsset = {
  asset_type: string;
  source_path: string;
  public_path: string;
};

export type RemotionRenderInput = {
  video: RemotionVideoMeta;
  template: RemotionTemplateMeta;
  scenes: RemotionSceneInput[];
  assets: RemotionAsset[];
  intro_video?: RemotionAsset | null;
  background_segments?: RemotionBackgroundSegment[];
  text_beats?: RemotionTextBeat[];
  music_track?: RemotionMusicTrack | null;
  public_dir?: string | null;
};
