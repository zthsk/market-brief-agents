from __future__ import annotations

from media_engine.story_schema import Scene, Story, StorySection


def build_scenes(story: Story, chart_path: str | None = None, max_duration: int = 75) -> list[Scene]:
    scenes = [
        Scene(
            scene_type="hook",
            duration=6.0,
            headline=story.hook,
            subheadline="Market brief",
            narration=(
                f"{story.hook}. Watch this setup closely. The move is not just about the "
                "headline number; it is about why investors suddenly changed the story."
            ),
            caption_text=story.hook,
        ),
        Scene(
            scene_type="price_card",
            duration=8.0,
            headline=f"{story.price_card.change_pct} {story.price_card.period}",
            subheadline=f"{story.ticker} at {story.price_card.price}",
            narration=(
                f"{story.ticker} is trading near {story.price_card.price}, with a "
                f"{story.price_card.change_pct} move {story.price_card.period}. Pause on that number, "
                "because a move like this usually means traders are reacting to a fresh catalyst, "
                "not just normal market noise."
            ),
            caption_text=f"{story.ticker}: {story.price_card.change_pct} today",
        ),
    ]
    catalyst = _section(story, "catalyst")
    if catalyst:
        scenes.append(
            Scene(
                scene_type="bullet_reveal",
                duration=10.0,
                headline=catalyst.title,
                bullets=catalyst.bullets,
                narration=(
                    f"The catalyst: {_join(catalyst.bullets)}. The important part is not that "
                    "one name moved the stock by itself. It is that investors were given a cleaner "
                    "reason to revisit the bull case."
                ),
                caption_text=catalyst.title,
            )
        )
    context = _section(story, "context")
    if context:
        scenes.append(
            Scene(
                scene_type="context",
                duration=10.0,
                headline=context.title,
                bullets=context.bullets,
                narration=(
                    f"But here is the catch: {_join(context.bullets)}. A good short-term reaction "
                    "does not automatically erase the bigger trend, so the context matters."
                ),
                caption_text=context.title,
            )
        )
    scenes.append(
        Scene(
            scene_type="chart",
            duration=11.0,
            headline="Zoom out",
            subheadline=story.chart_insight,
            chart_path=chart_path,
            narration=(
                f"Now zoom out to the chart. {story.chart_insight}. That contrast is the whole story: "
                "today's reaction is strong, but the market still needs evidence that the trend can hold."
            ),
            caption_text=story.chart_insight,
        )
    )
    watch = _section(story, "watch")
    if watch:
        scenes.append(
            Scene(
                scene_type="bullet_reveal",
                duration=10.0,
                headline=watch.title,
                bullets=watch.bullets,
                narration=(
                    f"What should you watch next? {_join(watch.bullets)}. If those updates support "
                    "the catalyst, momentum has a better chance of lasting. If not, this can fade fast."
                ),
                caption_text=watch.title,
            )
        )
    scenes.append(
        Scene(
            scene_type="takeaway",
            duration=9.0,
            headline="Takeaway",
            subheadline=story.takeaway,
            narration=(
                f"Takeaway: {story.takeaway}. This is educational market context, not a trade call. "
                "The move is worth watching, but the next data points decide whether it becomes a trend."
            ),
            caption_text=story.takeaway,
        )
    )
    _apply_progress(scenes)
    return _fit_duration(scenes, max_duration)


def _section(story: Story, kind: str):
    for section in story.sections:
        if section.type == kind:
            return section
    return None


def _join(section_or_bullets: StorySection | list[str]) -> str:
    bullets = section_or_bullets.bullets if isinstance(section_or_bullets, StorySection) else section_or_bullets
    return "; ".join(bullets)


def _apply_progress(scenes: list[Scene]) -> None:
    total = sum(scene.duration for scene in scenes) or 1
    elapsed = 0.0
    for scene in scenes:
        scene.progress_start = elapsed / total
        elapsed += scene.duration
        scene.progress_end = elapsed / total


def _fit_duration(scenes: list[Scene], max_duration: int) -> list[Scene]:
    total = sum(scene.duration for scene in scenes)
    if total <= max_duration:
        return scenes
    scale = max_duration / total
    for scene in scenes:
        scene.duration = max(2.8, scene.duration * scale)
    _apply_progress(scenes)
    return scenes
