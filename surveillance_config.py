"""Load and persist local surveillance settings with private file permissions."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_CONFIG: dict[str, Any] = {
    "low_resolution_url": "",
    "high_resolution_url": "",
    "threshold": 0.5,
    "interval": 0.5,
    "no_detection_seconds": 10.0,
    "recording_enabled": True,
    "output_dir": "captures",
    "model": "models/ssd_mobilenet_v2_coco_quant_postprocess.tflite",
    "labels": "models/coco_labels.txt",
    "host": "0.0.0.0",
    "port": 8091,
    "mode": "test",
}


def validate_rtsp_url(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(f"{field} doit être une URL RTSP valide.")
    value = value.strip()
    if value:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.hostname:
            raise ValueError(f"{field} doit commencer par rtsp:// ou rtsps://.")
    return value


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    result = DEFAULT_CONFIG.copy()
    result.update({key: config[key] for key in DEFAULT_CONFIG if key in config})
    result["low_resolution_url"] = validate_rtsp_url(
        result["low_resolution_url"], "Le flux basse résolution"
    )
    result["high_resolution_url"] = validate_rtsp_url(
        result["high_resolution_url"], "Le flux haute résolution"
    )
    try:
        result["threshold"] = float(result["threshold"])
        result["interval"] = float(result["interval"])
        result["no_detection_seconds"] = float(result["no_detection_seconds"])
        result["port"] = int(result["port"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Paramètres numériques invalides dans la configuration.") from exc
    if not 0.05 <= result["threshold"] <= 0.99:
        raise ValueError("Le seuil doit être compris entre 0,05 et 0,99.")
    if not 0.2 <= result["interval"] <= 30:
        raise ValueError("La cadence doit être comprise entre 0,2 et 30 secondes.")
    if not 0 <= result["no_detection_seconds"] <= 3600:
        raise ValueError("Le délai doit être compris entre 0 et 3600 secondes.")
    if not isinstance(result["recording_enabled"], bool):
        raise ValueError("recording_enabled doit être un booléen.")
    if not 1 <= result["port"] <= 65535:
        raise ValueError("Le port doit être compris entre 1 et 65535.")
    if not isinstance(result["mode"], str) or result["mode"] not in {"prod", "test"}:
        raise ValueError("Le mode doit être 'prod' ou 'test'.")
    for field in ("output_dir", "model", "labels", "host"):
        if not isinstance(result[field], str) or not result[field].strip():
            raise ValueError(f"Le paramètre {field} doit être une chaîne non vide.")
    return result


def save_config(path: Path, config: dict[str, Any]) -> None:
    """Atomically write JSON containing RTSP credentials as owner-readable only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_config = validate_config(config)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(safe_config, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def load_config(path: Path) -> dict[str, Any]:
    """Read local settings, creating a private defaults file on first run."""
    if not path.exists():
        config = DEFAULT_CONFIG.copy()
        save_config(path, config)
        return config
    try:
        with path.open(encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Impossible de lire le fichier de configuration JSON.") from exc
    if not isinstance(raw, dict):
        raise ValueError("La configuration JSON doit contenir un objet.")
    config = validate_config(raw)
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ValueError("Impossible de protéger les permissions du fichier de configuration.") from exc
    return config
