"""H.264 MP4 conversion helpers for browser-compatible recordings."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path


def probe_video_codec(path: str | Path) -> tuple[str | None, str | None]:
    """Return the first video stream codec and pixel format, without printing paths."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None, None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name,pix_fmt", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode != 0:
            return None, None
        streams = json.loads(result.stdout).get("streams") or []
        if not streams:
            return None, None
        return streams[0].get("codec_name"), streams[0].get("pix_fmt")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        return None, None


def transcode_mp4_to_h264(source: str | Path, destination: str | Path) -> bool:
    """Atomically create a browser-friendly H.264/yuv420p MP4; preserve source on failure."""
    source_path = Path(source)
    destination_path = Path(destination)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe") or not source_path.is_file():
        return False

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination_path.with_name(
        f".{destination_path.stem}.{uuid.uuid4().hex}.tmp.mp4"
    )
    try:
        source_stat = source_path.stat()
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-i", str(source_path), "-map", "0:v:0", "-an", "-c:v", "libx264",
             "-preset", "ultrafast", "-crf", "23", "-threads", "2",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4",
             str(temporary_path)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            return False
        codec, pixel_format = probe_video_codec(temporary_path)
        if codec != "h264" or pixel_format != "yuv420p":
            return False
        os.chmod(temporary_path, source_stat.st_mode & 0o777)
        os.utime(temporary_path, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
        os.replace(temporary_path, destination_path)
        return True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
