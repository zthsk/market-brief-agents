from __future__ import annotations

import subprocess

from media_engine.probe import media_resolution


def test_media_resolution_normalizes_trailing_csv_separator(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr("media_engine.probe.shutil.which", lambda name: "/usr/bin/ffprobe")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="1080x1920x\n", stderr="")

    monkeypatch.setattr("media_engine.probe.subprocess.run", fake_run)

    assert media_resolution(video) == "1080x1920"
