from __future__ import annotations

from typing import Any
import re


MARKET_BRIEFING_VOICE = {
    "name": "Market Brief Agents Briefing Voice",
    "recommended_gemini_voice": "Kore",
    "target_wpm": "145-165",
    "prompt": (
        "Voice character: Market Brief Agents Briefing Voice.\n"
        "Persona: a calm, intelligent market analyst explaining important stock moves "
        "to busy viewers, in the same clean voice family as the Market Brief Agents visual system. "
        "Credible, focused, slightly urgent, and emotionally varied.\n"
        "Tone: professional, analytical, composed, clear. Be lightly dramatic when "
        "revealing the catalyst, cautious during risk or context, and confident in the takeaway.\n"
        "Energy: medium-high but controlled. Alert and engaged, never excited for its own sake.\n"
        "Emotion map: curious in the hook; confident in the explanation; slightly cautious "
        "during context and caveats; decisive in the takeaway.\n"
        "Pacing: fast enough for short-form social media, but not rushed. Target 145 to "
        "165 words per minute. Slow down slightly for numbers, company names, ticker symbols, "
        "percentage moves, catalyst names, and key caveats.\n"
        "Delivery: use natural pauses. Emphasize ticker symbols, percentage moves, catalyst "
        "names, and contrast words like but, however, the catch, and watch this.\n"
        "Avoid: Do not sound robotic, like a teleprompter news anchor, like a hype trader, "
        "overacted, or like you are making investment recommendations."
    ),
}


def briefing_tts_prompt(transcript: str) -> str:
    return (
        f"{MARKET_BRIEFING_VOICE['prompt']}\n\n"
        "Read the transcript below as one cohesive short-form market briefing. "
        "Respect punctuation and line breaks as natural pause cues. Keep the voice clear, "
        "human, and trustworthy. Bracketed cues are delivery direction only; do not read "
        "them aloud.\n\n"
        "Transcript:\n"
        f"{_clean_transcript(transcript)}"
    )


def package_transcript(package: dict[str, Any]) -> str:
    scenes = package.get("scenes")
    if isinstance(scenes, list) and scenes:
        lines = []
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            text = str(scene.get("narration") or "").strip()
            if not text:
                continue
            scene_type = str(scene.get("type") or "")
            importance = str(scene.get("importance") or "medium")
            confidence = str(scene.get("confidence_level") or "medium")
            highlights = [str(item).strip() for item in scene.get("highlights", []) if str(item).strip()]
            cue = _delivery_cue(scene_type, "")
            if importance == "high":
                cue = f"{cue}; high-retention emphasis"
            if confidence == "medium":
                cue = f"{cue}; use careful uncertainty"
            if highlights:
                cue = f"{cue}; emphasize: {', '.join(highlights[:4])}"
            lines.append(f"[{cue}] {text}")
        return "\n\n".join(lines) or str(package.get("script") or "")

    segments = package.get("narration_segments")
    if not isinstance(segments, list) or not segments:
        return str(package.get("script") or "")
    lines: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        tone = _delivery_cue(str(segment.get("segment") or ""), str(segment.get("tone") or ""))
        emphasis = [
            str(item).strip()
            for item in segment.get("emphasis_terms", [])
            if str(item).strip()
        ]
        cue = tone
        if emphasis:
            cue = f"{cue}; emphasize: {', '.join(emphasis[:4])}"
        lines.append(f"[{cue}] {text}")
    return "\n\n".join(lines) or str(package.get("script") or "")


def scene_transcript(scenes: list) -> str:
    lines: list[str] = []
    for scene in scenes:
        narration = str(getattr(scene, "narration", "") or "").strip()
        if not narration:
            continue
        label = str(getattr(scene, "scene_type", "scene")).replace("_", " ").upper()
        if label == "HOOK":
            cue = "[curious, crisp hook]"
        elif label == "CHART":
            cue = "[slightly cautious context]"
        elif label == "TAKEAWAY":
            cue = "[decisive takeaway]"
        elif label == "CONTEXT":
            cue = "[slightly cautious context]"
        elif label == "BULLET REVEAL":
            cue = "[confident explanation]"
        else:
            cue = "[composed analyst tone]"
        lines.append(f"{cue} {narration}")
    return "\n\n".join(lines)


def _clean_transcript(transcript: str) -> str:
    text = re.sub(r"\b(HOOK|WHAT HAPPENED|WHY IT MATTERS|WHAT TO WATCH|CTA):\s*", "", transcript)
    return re.sub(r"[ \t]+", " ", text).strip()


def _delivery_cue(segment: str, tone: str) -> str:
    if segment == "hook" or tone == "curious_urgent":
        return "curious, crisp hook with controlled urgency"
    if segment == "risk" or tone == "cautious":
        return "slightly cautious and measured"
    if segment == "watch":
        return "focused, practical what-to-watch"
    if segment == "takeaway" or tone == "decisive":
        return "decisive, grounded takeaway"
    if segment == "catalyst":
        return "confident explanation with light intrigue"
    return "composed analyst tone"
