"""Safe listing and metadata helpers for local MP4 event recordings."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

PAGE_SIZE = 40


def recordings_directory(output_dir: str | Path) -> Path:
    """Resolve the configured output directory without exposing it to clients."""
    return Path(output_dir).expanduser().resolve()


def _safe_mp4(directory: Path, filename: str) -> Path | None:
    if (not filename or filename.startswith(".") or "/" in filename or "\\" in filename
            or Path(filename).name != filename or Path(filename).suffix.lower() != ".mp4"):
        return None
    try:
        root = directory.resolve()
        candidate = root / filename
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        if resolved.parent != root or not resolved.is_file():
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def list_recordings(output_dir: str | Path, query: str = "", page: int = 0) -> dict[str, Any]:
    """Return one page of filename/date/size metadata, filtering names case-insensitively."""
    if page < 0:
        raise ValueError("La page doit être positive.")
    directory = recordings_directory(output_dir)
    terms = query.casefold().split()
    files: list[dict[str, Any]] = []
    if not directory.is_dir():
        return {"files": [], "total": 0, "page": page, "page_size": PAGE_SIZE}
    for entry in directory.iterdir():
        if entry.is_symlink() or entry.suffix.casefold() != ".mp4" or entry.name.startswith("."):
            continue
        safe_path = _safe_mp4(directory, entry.name)
        if safe_path is None:
            continue
        try:
            info = safe_path.stat()
        except OSError:
            continue
        modified_at = datetime.fromtimestamp(info.st_mtime).astimezone().isoformat(timespec="seconds")
        searchable = f"{entry.name} {modified_at} {datetime.fromtimestamp(info.st_mtime).astimezone():%d/%m/%Y %H:%M}".casefold()
        if terms and not all(term in searchable for term in terms):
            continue
        files.append({"name": entry.name, "size_bytes": info.st_size, "modified_at": modified_at})
    files.sort(key=lambda item: (item["modified_at"], item["name"].casefold()), reverse=True)
    total = len(files)
    start = page * PAGE_SIZE
    return {"files": files[start:start + PAGE_SIZE], "total": total, "page": page, "page_size": PAGE_SIZE}


def resolve_recording(output_dir: str | Path, filename: str) -> Path | None:
    """Resolve only a direct, regular MP4 child of the configured directory."""
    return _safe_mp4(recordings_directory(output_dir), filename)


def resolve_recording_sidecar(output_dir: str | Path, filename: str,
                              extension: str) -> Path | None:
    """Resolve a JPG/TXT sidecar only when its matching MP4 is a safe library item."""
    if extension not in {".jpg", ".txt"}:
        return None
    video_path = resolve_recording(output_dir, filename)
    if video_path is None:
        return None
    candidate = video_path.with_suffix(extension)
    try:
        root = recordings_directory(output_dir)
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        if resolved.parent != root or not resolved.is_file():
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _probe_media(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name,width,height,r_frame_rate:format=duration,bit_rate,format_name",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=4, check=False,
        )
        if result.returncode != 0:
            return {}
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        return {}
    streams = payload.get("streams") or []
    stream = streams[0] if streams and isinstance(streams[0], dict) else {}
    media_format = payload.get("format") or {}
    try:
        duration = float(media_format.get("duration"))
        if not math.isfinite(duration) or duration < 0:
            duration = None
    except (TypeError, ValueError):
        duration = None
    try:
        bit_rate = int(media_format.get("bit_rate"))
    except (TypeError, ValueError):
        bit_rate = None
    return {
        "duration_seconds": duration,
        "width": stream.get("width") if isinstance(stream.get("width"), int) else None,
        "height": stream.get("height") if isinstance(stream.get("height"), int) else None,
        "codec": stream.get("codec_name"),
        "frame_rate": stream.get("r_frame_rate"),
        "bit_rate": bit_rate,
        "format": media_format.get("format_name"),
    }


def recording_details(output_dir: str | Path, filename: str) -> dict[str, Any] | None:
    path = resolve_recording(output_dir, filename)
    if path is None:
        return None
    info = path.stat()
    labels_path = resolve_recording_sidecar(output_dir, filename, ".txt")
    labels: list[str] = []
    if labels_path is not None:
        try:
            if labels_path.stat().st_size <= 16_384:
                labels = list(dict.fromkeys(
                    line.strip()[:128]
                    for line in labels_path.read_text(encoding="utf-8").splitlines()[:80]
                    if line.strip()
                ))
        except (OSError, UnicodeError):
            labels = []
    evidence_path = resolve_recording_sidecar(output_dir, filename, ".jpg")
    return {
        "name": path.name,
        "event_labels": labels,
        "evidence_available": evidence_path is not None,
        "size_bytes": info.st_size,
        "modified_at": datetime.fromtimestamp(info.st_mtime).astimezone().isoformat(timespec="seconds"),
        **_probe_media(path),
    }
