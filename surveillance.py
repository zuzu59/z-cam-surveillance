#!/usr/bin/env python3
"""Web-based threaded RTSP surveillance application."""
from __future__ import annotations

import argparse
import logging
import math
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from surveillance_media import transcode_mp4_to_h264
from surveillance_config import (
    DEFAULT_CONFIG,
    DEFAULT_ENABLED_LABELS,
    load_config,
    save_config,
    validate_config,
    validate_rtsp_url,
)

try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter  # type: ignore[no-redef]
    except ImportError:
        try:
            from tensorflow.lite import Interpreter  # type: ignore[no-redef]
        except ImportError:
            Interpreter = None  # type: ignore[assignment,misc]

TARGETS = set(DEFAULT_ENABLED_LABELS)
COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat",
    "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog",
    "pizza", "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    # Normalized coordinates: (y_min, x_min, y_max, x_max).
    box: tuple[float, float, float, float]


def load_labels(path: str | None) -> list[str]:
    if not path:
        return COCO_LABELS.copy()
    labels: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        labels.append(parts[1] if len(parts) == 2 and parts[0].isdigit() else line)
    return labels


def label_for_class(labels: list[str], class_id: int) -> str:
    """Look up the model's zero-based class ID in its aligned label map."""
    return labels[class_id] if 0 <= class_id < len(labels) else ""


class TFLiteDetector:
    def __init__(self, model_path: str, labels_path: str | None, threshold: float,
                 enabled_labels: list[str] | None = None):
        if Interpreter is None:
            raise RuntimeError("TFLite introuvable. Installez ai-edge-litert (ou un runtime compatible).")
        self.labels = load_labels(labels_path)
        self.enabled_labels = frozenset(TARGETS if enabled_labels is None else enabled_labels)
        self.threshold = threshold
        self.interpreter = Interpreter(model_path=model_path, num_threads=2)
        self.interpreter.allocate_tensors()
        self.input = self.interpreter.get_input_details()[0]
        self.outputs = self.interpreter.get_output_details()
        shape = self.input["shape"]
        if len(shape) != 4:
            raise ValueError(f"Entrée TFLite inattendue: {shape}")
        self.height, self.width = int(shape[1]), int(shape[2])
        logging.info("Modèle TFLite chargé (%s × %s)", self.width, self.height)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        resized = cv2.resize(frame, (self.width, self.height))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        dtype = self.input["dtype"]
        if dtype == np.float32:
            tensor = (rgb.astype(np.float32) - 127.5) / 127.5
        elif dtype in (np.uint8, np.int8):
            tensor = rgb.astype(dtype)
        else:
            raise ValueError(f"Type d'entrée TFLite non pris en charge: {dtype}")
        self.interpreter.set_tensor(self.input["index"], np.expand_dims(tensor, 0))
        self.interpreter.invoke()
        values = [np.squeeze(self.interpreter.get_tensor(item["index"])) for item in self.outputs]
        # SSD postprocess output order: boxes, classes, scores, count.
        boxes = values[0].reshape(-1, 4)
        classes = values[1].reshape(-1)
        scores = values[2].reshape(-1)
        count_arr = values[3].reshape(-1)
        count = min(int(count_arr[0]) if count_arr.size else len(scores), len(scores), len(classes), len(boxes))
        detections: list[Detection] = []
        for box, class_id, score in zip(boxes[:count], classes[:count], scores[:count]):
            confidence = float(score)
            if confidence < self.threshold:
                continue
            index = int(class_id)
            # Model classes are zero-based; the bundled 90-entry COCO map keeps
            # the original category gaps as n/a entries, so class 0 is person.
            label = label_for_class(self.labels, index)
            if label in self.enabled_labels:
                y_min, x_min, y_max, x_max = (float(v) for v in box)
                detections.append(Detection(label, confidence, (y_min, x_min, y_max, x_max)))
        return detections


class Surveillance:
    """Shared low-resolution inference and high-resolution recording engine."""

    def __init__(self, low_resolution_url: str, detector: TFLiteDetector, threshold: float,
                 interval: float, no_detection_seconds: float, output_dir: Path,
                 recording_enabled: bool = True, high_resolution_url: str = "",
                 config_path: Path | None = None, config_data: dict[str, Any] | None = None):
        self.low_resolution_url = low_resolution_url
        self.high_resolution_url = high_resolution_url
        self.detector = detector
        self.threshold = threshold
        self.interval = interval
        self.no_detection_seconds = no_detection_seconds
        self.output_dir = output_dir
        self.recording_enabled = recording_enabled
        self.config_path = config_path
        self.config = DEFAULT_CONFIG.copy() if config_data is None else config_data.copy()
        self.config.update({
            "low_resolution_url": low_resolution_url,
            "high_resolution_url": high_resolution_url,
            "threshold": threshold,
            "interval": interval,
            "no_detection_seconds": no_detection_seconds,
            "recording_enabled": recording_enabled,
            "output_dir": str(output_dir),
        })
        self.config.setdefault("enabled_labels", list(DEFAULT_ENABLED_LABELS))
        self.config["enabled_labels"] = list(self.config["enabled_labels"])
        self.detector.enabled_labels = frozenset(self.config["enabled_labels"])
        self.startup_config = self.config.copy()
        self.pending_restart_fields: set[str] = set()
        self.stop_event = threading.Event()
        self.reconnect_event = threading.Event()
        self.recording_reconnect_event = threading.Event()
        self.recording_wakeup_event = threading.Event()
        self.lock = threading.Lock()
        self.latest_frame: np.ndarray | None = None
        self.latest_detections: list[Detection] = []
        self.latest_jpeg: bytes | None = None
        self.preview_sequence = 0
        self.connected = False
        self.recording_active = False
        self.capture_fps = 0.0
        self.last_inference = 0.0
        self.last_detection = 0.0
        self.record_requested = False
        self.started = False
        self.capture_thread: threading.Thread | None = None
        self.inference_thread: threading.Thread | None = None
        self.recording_thread: threading.Thread | None = None
        self.media_transcoder = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mp4-h264-transcode")

    def _should_record_unlocked(self, now: float | None = None) -> bool:
        if not (self.record_requested and self.recording_enabled):
            return False
        elapsed = (time.monotonic() if now is None else now) - self.last_detection
        if self.no_detection_seconds > 0 and elapsed >= self.no_detection_seconds:
            self.record_requested = False
            return False
        return True

    @staticmethod
    def _frame_target_for_elapsed(elapsed: float, fps: float) -> int:
        if not math.isfinite(fps) or fps <= 0:
            return 1
        return max(1, int(max(0.0, elapsed) * fps))

    def available_labels(self) -> list[str]:
        labels = getattr(self.detector, "labels", None)
        if labels is None:
            try:
                labels = load_labels(self.config.get("labels"))
            except (OSError, UnicodeError):
                labels = COCO_LABELS
        return list(dict.fromkeys(
            label.strip() for label in labels if label.strip() and label.strip().casefold() != "n/a"
        ))

    def update_settings(self, payload: dict[str, Any]) -> None:
        """Validate and persist the full JSON configuration without exposing RTSP URLs."""
        with self.lock:
            candidate = self.config.copy()
            low_field = "low_resolution_url" if "low_resolution_url" in payload else "url"
            clear_low = payload.get("clear_low_resolution_url", False)
            clear_high = payload.get("clear_high_resolution_url", False)
            if not isinstance(clear_low, bool) or not isinstance(clear_high, bool):
                raise ValueError("Les commandes d'effacement des URL doivent être booléennes.")
            if low_field in payload:
                low_url = validate_rtsp_url(payload[low_field], "Le flux basse résolution")
                if low_url:
                    candidate["low_resolution_url"] = low_url
            if "high_resolution_url" in payload:
                high_url = validate_rtsp_url(payload["high_resolution_url"], "Le flux haute résolution")
                if high_url:
                    candidate["high_resolution_url"] = high_url
            if clear_low:
                candidate["low_resolution_url"] = ""
            if clear_high:
                candidate["high_resolution_url"] = ""

            for field in ("threshold", "interval", "no_detection_seconds"):
                if field in payload:
                    try:
                        candidate[field] = float(payload[field])
                    except (TypeError, ValueError) as exc:
                        raise ValueError("Paramètres numériques invalides.") from exc
            if "port" in payload:
                try:
                    candidate["port"] = int(payload["port"])
                except (TypeError, ValueError) as exc:
                    raise ValueError("Le port doit être un nombre entier.") from exc
            if "recording_enabled" in payload:
                if not isinstance(payload["recording_enabled"], bool):
                    raise ValueError("Le réglage d'enregistrement doit être un booléen.")
                candidate["recording_enabled"] = payload["recording_enabled"]
            for field in ("output_dir", "model", "labels", "host"):
                if field in payload:
                    candidate[field] = payload[field]
            if "enabled_labels" in payload:
                candidate["enabled_labels"] = payload["enabled_labels"]
            candidate = validate_config(candidate)
            unknown_labels = set(candidate["enabled_labels"]) - set(self.available_labels())
            if unknown_labels:
                raise ValueError(
                    "Labels de détection inconnus: " + ", ".join(sorted(unknown_labels))
                )
            if self.config_path is not None:
                try:
                    save_config(self.config_path, candidate)
                except OSError as exc:
                    raise ValueError("Impossible d'enregistrer la configuration locale.") from exc

            low_url = candidate["low_resolution_url"]
            high_url = candidate["high_resolution_url"]
            low_changed = low_url != self.low_resolution_url
            high_changed = high_url != self.high_resolution_url
            self.config = candidate
            self.pending_restart_fields = {
                field for field in ("model", "labels", "host", "port")
                if candidate[field] != self.startup_config[field]
            }
            self.low_resolution_url = low_url
            self.high_resolution_url = high_url
            self.threshold = candidate["threshold"]
            self.interval = candidate["interval"]
            self.no_detection_seconds = candidate["no_detection_seconds"]
            self.recording_enabled = candidate["recording_enabled"]
            self.output_dir = Path(candidate["output_dir"])
            self.detector.threshold = self.threshold
            self.detector.enabled_labels = frozenset(candidate["enabled_labels"])
            if low_changed:
                self.latest_frame = None
                self.latest_detections = []
                self.latest_jpeg = None
                self.reconnect_event.set()
            if high_changed:
                self.recording_reconnect_event.set()
                self.recording_wakeup_event.set()
            if not self.recording_enabled:
                self.record_requested = False
                self.recording_wakeup_event.set()

    def settings_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return self.settings_snapshot_unlocked()

    def status_snapshot(self) -> dict[str, Any]:
        with self.lock:
            frame = self.latest_frame
            detections = self.latest_detections
            return {
                "connected": self.connected,
                "recording": self.recording_active,
                "recording_requested": self.record_requested,
                "recording_enabled": self.recording_enabled,
                "width": int(frame.shape[1]) if frame is not None else None,
                "height": int(frame.shape[0]) if frame is not None else None,
                "capture_fps": round(self.capture_fps, 1),
                "detections": [
                    {"label": d.label, "confidence": round(d.confidence, 3), "box": d.box}
                    for d in detections
                ],
                "preview_sequence": self.preview_sequence,
                "settings": self.settings_snapshot_unlocked(),
            }

    def settings_snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "interval": self.interval,
            "no_detection_seconds": self.no_detection_seconds,
            "recording_enabled": self.recording_enabled,
            "low_stream_configured": bool(self.low_resolution_url),
            "high_stream_configured": bool(self.high_resolution_url),
            "stream_configured": bool(self.low_resolution_url),
        }

    def config_snapshot(self) -> dict[str, Any]:
        """Return editable config values while redacting both RTSP URLs."""
        with self.lock:
            snapshot = {key: value for key, value in self.config.items()
                        if key not in {"low_resolution_url", "high_resolution_url"}}
            snapshot.update({
                "available_labels": self.available_labels(),
                "low_stream_configured": bool(self.low_resolution_url),
                "high_stream_configured": bool(self.high_resolution_url),
                "restart_required_fields": sorted(self.pending_restart_fields),
            })
            return snapshot

    def reveal_stream_url(self, source: str) -> str:
        """Return exactly one saved RTSP URL after an explicit reveal request."""
        with self.lock:
            if source == "low":
                return self.low_resolution_url
            if source == "high":
                return self.high_resolution_url
            raise ValueError("Source vidéo invalide.")

    def preview_snapshot(self) -> tuple[int, bytes | None]:
        with self.lock:
            return self.preview_sequence, self.latest_jpeg

    def _finalize_recording(self, source: Path, destination: Path) -> None:
        if transcode_mp4_to_h264(source, destination):
            try:
                source.unlink(missing_ok=True)
            except OSError:
                logging.warning("MP4 H.264 créé, mais le fichier temporaire n’a pas pu être supprimé")
            logging.info("Enregistrement prêt pour les navigateurs (H.264): %s", destination.name)
            return
        try:
            if source.is_file():
                source.replace(destination)
                logging.error("Conversion H.264 indisponible; clip publié dans son codec source: %s", destination.name)
        except OSError:
            logging.exception("Impossible de finaliser le clip MP4")

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.capture_thread = threading.Thread(target=self.capture_loop, name="rtsp-low-capture", daemon=True)
        self.inference_thread = threading.Thread(target=self.inference_loop, name="tflite-inference", daemon=True)
        self.recording_thread = threading.Thread(target=self.recording_loop, name="rtsp-high-recording", daemon=True)
        self.capture_thread.start()
        self.inference_thread.start()
        self.recording_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.reconnect_event.set()
        self.recording_reconnect_event.set()
        self.recording_wakeup_event.set()
        for worker in (self.capture_thread, self.inference_thread, self.recording_thread):
            if worker:
                worker.join(timeout=7)
        self.media_transcoder.shutdown(wait=False, cancel_futures=False)
        self.started = False

    def capture_loop(self) -> None:
        capture: Any = None
        reconnect_delay = 1.0
        previous_frame_at = 0.0
        try:
            while not self.stop_event.is_set():
                if self.reconnect_event.is_set():
                    self.reconnect_event.clear()
                    if capture is not None:
                        capture.release()
                        capture = None
                    with self.lock:
                        self.connected = False
                with self.lock:
                    stream_url = self.low_resolution_url
                if not stream_url:
                    self.stop_event.wait(0.5)
                    continue
                if capture is None or not capture.isOpened():
                    if capture is not None:
                        capture.release()
                    logging.info("Connexion au flux basse résolution…")
                    capture = cv2.VideoCapture(
                        stream_url, cv2.CAP_FFMPEG,
                        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                         cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000],
                    )
                    if not capture.isOpened():
                        with self.lock:
                            self.connected = False
                        logging.warning("Flux basse résolution indisponible; nouvel essai dans %.0f s", reconnect_delay)
                        capture.release()
                        capture = None
                        self.reconnect_event.wait(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, 30.0)
                        continue
                    reconnect_delay = 1.0
                    with self.lock:
                        self.connected = True
                    logging.info("Flux basse résolution connecté")
                ok, frame = capture.read()
                if not ok or frame is None:
                    logging.warning("Flux basse résolution interrompu; reconnexion automatique")
                    capture.release()
                    capture = None
                    with self.lock:
                        self.connected = False
                    self.reconnect_event.wait(1.0)
                    continue
                now = time.monotonic()
                with self.lock:
                    self.latest_frame = frame
                    self.capture_fps = 1 / (now - previous_frame_at) if previous_frame_at else 0.0
                previous_frame_at = now
        finally:
            with self.lock:
                self.connected = False
            if capture is not None:
                capture.release()

    def recording_loop(self) -> None:
        capture: Any = None
        writer: Any = None
        writer_path: Path | None = None
        writer_frames = 0
        writer_started_at: float | None = None
        writer_start_stream_position: float | None = None
        writer_fps = 0.0
        last_writer_frame: np.ndarray | None = None
        published_path: Path | None = None
        last_size: tuple[int, int] | None = None
        reconnect_delay = 1.0
        warned_missing_source = False

        def close_writer() -> None:
            nonlocal writer, writer_path, writer_frames, writer_started_at
            nonlocal writer_start_stream_position, writer_fps, last_writer_frame
            nonlocal published_path, last_size
            current_writer = writer
            current_path = writer_path
            current_published_path = published_path
            current_frames = writer_frames
            current_started_at = writer_started_at
            current_fps = writer_fps
            writer = None
            writer_path = None
            published_path = None
            writer_frames = 0
            writer_started_at = None
            writer_start_stream_position = None
            writer_fps = 0.0
            last_writer_frame = None
            last_size = None
            if current_writer is not None:
                current_writer.release()
                if current_frames and current_started_at is not None and current_fps > 0:
                    elapsed = max(0.0, time.monotonic() - current_started_at)
                    logging.info(
                        "Enregistrement clôturé : %d images, %.2f s réelles / %.2f s vidéo",
                        current_frames, elapsed, current_frames / current_fps,
                    )
            if current_frames == 0 and current_path is not None:
                try:
                    current_path.unlink(missing_ok=True)
                except OSError:
                    logging.warning("Impossible de supprimer le MP4 vide")
            elif current_frames > 0 and current_path is not None and current_published_path is not None:
                try:
                    self.media_transcoder.submit(
                        self._finalize_recording, current_path, current_published_path
                    )
                except RuntimeError:
                    self._finalize_recording(current_path, current_published_path)
            with self.lock:
                self.recording_active = False

        def close_capture() -> None:
            nonlocal capture
            if capture is not None:
                capture.release()
                capture = None

        try:
            while not self.stop_event.is_set():
                if self.recording_reconnect_event.is_set():
                    self.recording_reconnect_event.clear()
                    close_writer()
                    close_capture()
                with self.lock:
                    should_record = self._should_record_unlocked()
                    stream_url = self.high_resolution_url
                if not should_record:
                    close_writer()
                    close_capture()
                    self.recording_wakeup_event.wait(0.25)
                    self.recording_wakeup_event.clear()
                    continue
                if not stream_url:
                    if not warned_missing_source:
                        logging.warning("Flux haute résolution non configuré; enregistrement impossible")
                        warned_missing_source = True
                    self.stop_event.wait(0.25)
                    continue
                warned_missing_source = False
                if capture is None or not capture.isOpened():
                    close_capture()
                    logging.info("Connexion au flux haute résolution pour l'enregistrement…")
                    capture = cv2.VideoCapture(
                        stream_url, cv2.CAP_FFMPEG,
                        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                         cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000],
                    )
                    if not capture.isOpened():
                        logging.warning("Flux haute résolution indisponible; nouvel essai dans %.0f s", reconnect_delay)
                        capture.release()
                        capture = None
                        self.recording_wakeup_event.wait(reconnect_delay)
                        self.recording_wakeup_event.clear()
                        reconnect_delay = min(reconnect_delay * 2, 30.0)
                        continue
                    reconnect_delay = 1.0
                    logging.info("Flux haute résolution connecté")
                ok, frame = capture.read()
                frame_read_at = time.monotonic()
                position_msec = capture.get(cv2.CAP_PROP_POS_MSEC) if ok else float("nan")
                stream_position = (
                    position_msec / 1000.0
                    if math.isfinite(position_msec) and position_msec >= 0
                    else None
                )
                if not ok or frame is None:
                    logging.warning("Flux haute résolution interrompu; reconnexion automatique")
                    close_writer()
                    close_capture()
                    self.stop_event.wait(1.0)
                    continue
                with self.lock:
                    should_record = self._should_record_unlocked()
                if not should_record:
                    close_writer()
                    close_capture()
                    continue
                height, width = frame.shape[:2]
                if writer is None:
                    self.output_dir.mkdir(parents=True, exist_ok=True)
                    filename = self.output_dir / f"{datetime.now():%Y%m%d_%H%M%S_%f}.mp4"
                    fps = capture.get(cv2.CAP_PROP_FPS)
                    writer_fps = float(fps) if math.isfinite(fps) and fps > 0 else 10.0
                    writer_path = filename.with_name(f".{filename.stem}.recording.mp4")
                    published_path = filename
                    writer = cv2.VideoWriter(str(writer_path), cv2.VideoWriter_fourcc(*"mp4v"),
                                             writer_fps, (width, height))
                    if not writer.isOpened():
                        logging.error("Impossible de créer le MP4 haute résolution (%dx%d)", width, height)
                        close_writer()
                        continue
                    writer_started_at = time.monotonic()
                    writer_start_stream_position = stream_position
                    last_size = (width, height)
                    with self.lock:
                        self.recording_active = True
                    logging.info("[ALERTE] Début de l'enregistrement haute résolution: %s", filename)
                elif (width, height) != last_size:
                    close_writer()
                    logging.warning("Résolution haute du flux modifiée; clip fermé")
                    continue
                wall_elapsed = max(0.0, frame_read_at - writer_started_at)
                # OpenCV can drain buffered RTSP frames faster than real time; trust
                # their media position when available, and use monotonic time as fallback.
                stream_elapsed = (
                    max(0.0, stream_position - writer_start_stream_position)
                    if stream_position is not None and writer_start_stream_position is not None
                    else 0.0
                )
                elapsed = max(wall_elapsed, stream_elapsed)
                target_frames = max(
                    writer_frames, self._frame_target_for_elapsed(elapsed, writer_fps)
                )
                frames_to_write = max(0, target_frames - writer_frames)
                for _ in range(max(0, frames_to_write - 1)):
                    writer.write(last_writer_frame if last_writer_frame is not None else frame)
                if frames_to_write:
                    writer.write(frame)
                    writer_frames += frames_to_write
                    last_writer_frame = frame
        finally:
            close_writer()
            close_capture()

    def inference_loop(self) -> None:
        while not self.stop_event.is_set():
            with self.lock:
                frame = None if self.latest_frame is None else self.latest_frame.copy()
                interval = self.interval
            if frame is None:
                self.stop_event.wait(0.1)
                continue
            try:
                detections = self.detector.detect(frame)
                now = time.monotonic()
                with self.lock:
                    self.latest_detections = detections
                    self.last_inference = now
                    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
                    if ok:
                        self.latest_jpeg = jpeg.tobytes()
                        self.preview_sequence += 1
                    was_recording = self.record_requested
                    if detections and self.recording_enabled:
                        self.record_requested = True
                        self.last_detection = now
                        self.recording_wakeup_event.set()
                    elif (self.record_requested and self.no_detection_seconds > 0
                          and now - self.last_detection >= self.no_detection_seconds):
                        self.record_requested = False
                for detection in detections:
                    logging.info("[ALERTE] %s détecté (%.0f%%)", detection.label, detection.confidence * 100)
                if detections and self.recording_enabled and not was_recording:
                    logging.info("Alarme déclenchée; démarrage de l'enregistrement")
            except Exception:
                logging.exception("Erreur pendant l'inférence TFLite")
            self.stop_event.wait(interval)

def _handle_termination_signal(signum: int, _frame: Any) -> None:
    logging.info("Arrêt demandé par le signal %d", signum)
    raise KeyboardInterrupt


def main() -> None:
    from app_version import APP_VERSION

    parser = argparse.ArgumentParser(prog="z-cam-surveillance",
                                     description="Application web de surveillance RTSP avec détection TFLite")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--config", type=Path, default=Path("surveillance.config.json"),
                        help="Fichier JSON local des flux et paramètres")
    parser.add_argument("--low-url", "--url", dest="low_url", default=None,
                        help="Remplace le flux basse résolution (détection)")
    parser.add_argument("--high-url", default=None,
                        help="Remplace le flux haute résolution (enregistrement)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--labels", default=None)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--no-detection-seconds", type=float, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--host", default=None, help="Adresse d'écoute du serveur web")
    parser.add_argument("--port", type=int, default=None, help="Port du serveur web")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        overrides = {
            "low_resolution_url": args.low_url,
            "high_resolution_url": args.high_url,
            "model": args.model,
            "labels": args.labels,
            "threshold": args.threshold,
            "interval": args.interval,
            "no_detection_seconds": args.no_detection_seconds,
            "output_dir": str(args.output_dir) if args.output_dir is not None else None,
            "host": args.host,
            "port": args.port,
        }
        config.update({key: value for key, value in overrides.items() if value is not None})
        config = validate_config(config)
        save_config(args.config, config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cv2.setNumThreads(2)
    detector = TFLiteDetector(
        config["model"], config["labels"], config["threshold"], config["enabled_labels"]
    )
    engine = Surveillance(
        config["low_resolution_url"], detector, config["threshold"], config["interval"],
        config["no_detection_seconds"], Path(config["output_dir"]),
        recording_enabled=config["recording_enabled"],
        high_resolution_url=config["high_resolution_url"], config_path=args.config, config_data=config,
    )
    from surveillance_web import create_app

    signal.signal(signal.SIGTERM, _handle_termination_signal)
    engine.start()
    logging.info("Tableau de surveillance disponible sur http://%s:%d", config["host"], config["port"])
    try:
        create_app(engine).run(host=config["host"], port=config["port"], debug=False,
                              threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        logging.info("Arrêt demandé")
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
