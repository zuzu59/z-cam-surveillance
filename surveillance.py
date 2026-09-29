#!/usr/bin/env python3
"""Threaded RTSP surveillance with headless production and browser test modes."""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import cv2
import numpy as np

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

TARGETS = {"person", "car", "bicycle", "motorcycle", "dog", "cat"}
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
    def __init__(self, model_path: str, labels_path: str | None, threshold: float):
        if Interpreter is None:
            raise RuntimeError("TFLite introuvable. Installez ai-edge-litert (ou un runtime compatible).")
        self.labels = load_labels(labels_path)
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
            if label in TARGETS:
                y_min, x_min, y_max, x_max = (float(v) for v in box)
                detections.append(Detection(label, confidence, (y_min, x_min, y_max, x_max)))
        return detections


class Surveillance:
    """Shared camera/inference/recording engine for test and production modes."""

    def __init__(self, url: str, detector: TFLiteDetector, threshold: float,
                 interval: float, no_detection_seconds: float, output_dir: Path,
                 recording_enabled: bool = True, preview_enabled: bool = False):
        self.url = url
        self.detector = detector
        self.threshold = threshold
        self.interval = interval
        self.no_detection_seconds = no_detection_seconds
        self.output_dir = output_dir
        self.recording_enabled = recording_enabled
        self.preview_enabled = preview_enabled
        self.stop_event = threading.Event()
        self.reconnect_event = threading.Event()
        self.lock = threading.Lock()
        self.latest_frame: np.ndarray | None = None
        self.latest_detections: list[Detection] = []
        self.latest_jpeg: bytes | None = None
        self.preview_sequence = 0
        self.connected = False
        self.capture_fps = 0.0
        self.last_inference = 0.0
        self.last_detection = 0.0
        self.record_requested = False
        self.started = False
        self.capture_thread: threading.Thread | None = None
        self.inference_thread: threading.Thread | None = None

    def update_settings(self, payload: dict[str, Any]) -> None:
        """Validate and atomically update non-secret runtime settings and optional URL."""
        threshold = float(payload.get("threshold", self.threshold))
        interval = float(payload.get("interval", self.interval))
        no_detection_seconds = float(payload.get("no_detection_seconds", self.no_detection_seconds))
        recording_enabled = payload.get("recording_enabled", self.recording_enabled)
        if not 0.05 <= threshold <= 0.99:
            raise ValueError("Le seuil doit être compris entre 0,05 et 0,99.")
        if not 0.2 <= interval <= 30:
            raise ValueError("La cadence doit être comprise entre 0,2 et 30 secondes.")
        if not 0 <= no_detection_seconds <= 3600:
            raise ValueError("Le délai doit être compris entre 0 et 3600 secondes.")
        if not isinstance(recording_enabled, bool):
            raise ValueError("Le réglage d'enregistrement doit être un booléen.")
        new_url = payload.get("url")
        if new_url is not None:
            if not isinstance(new_url, str) or len(new_url) > 2048:
                raise ValueError("URL RTSP invalide.")
            new_url = new_url.strip()
            if new_url:
                parsed = urlsplit(new_url)
                if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.hostname:
                    raise ValueError("Saisissez une URL rtsp:// ou rtsps:// valide.")
        with self.lock:
            self.threshold = threshold
            self.interval = interval
            self.no_detection_seconds = no_detection_seconds
            self.recording_enabled = recording_enabled
            self.detector.threshold = threshold
            if new_url:
                if new_url != self.url:
                    self.url = new_url
                    self.latest_frame = None
                    self.latest_detections = []
                    self.latest_jpeg = None
                    self.reconnect_event.set()
            if not recording_enabled:
                self.record_requested = False

    def settings_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "threshold": self.threshold,
                "interval": self.interval,
                "no_detection_seconds": self.no_detection_seconds,
                "recording_enabled": self.recording_enabled,
                "stream_configured": bool(self.url),
            }

    def status_snapshot(self) -> dict[str, Any]:
        with self.lock:
            frame = self.latest_frame
            detections = self.latest_detections
            return {
                "connected": self.connected,
                "recording": self.record_requested,
                "recording_enabled": self.recording_enabled,
                "width": int(frame.shape[1]) if frame is not None else None,
                "height": int(frame.shape[0]) if frame is not None else None,
                "capture_fps": round(self.capture_fps, 1),
                "detections": [
                    {"label": d.label, "confidence": round(d.confidence, 3), "box": d.box}
                    for d in detections
                ],
                "preview_sequence": self.preview_sequence,
                "settings": {
                    "threshold": self.threshold,
                    "interval": self.interval,
                    "no_detection_seconds": self.no_detection_seconds,
                    "recording_enabled": self.recording_enabled,
                    "stream_configured": bool(self.url),
                },
            }

    def preview_snapshot(self) -> tuple[int, bytes | None]:
        with self.lock:
            return self.preview_sequence, self.latest_jpeg

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.capture_thread = threading.Thread(target=self.capture_loop, name="rtsp-capture", daemon=True)
        self.inference_thread = threading.Thread(target=self.inference_loop, name="tflite-inference", daemon=True)
        self.capture_thread.start()
        self.inference_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.reconnect_event.set()
        if self.capture_thread:
            self.capture_thread.join(timeout=7)
        if self.inference_thread:
            self.inference_thread.join(timeout=7)
        self.started = False

    def capture_loop(self) -> None:
        capture: Any = None
        writer: Any = None
        last_size: tuple[int, int] | None = None
        reconnect_delay = 1.0
        previous_frame_at = 0.0
        try:
            while not self.stop_event.is_set():
                if self.reconnect_event.is_set():
                    self.reconnect_event.clear()
                    if capture is not None:
                        capture.release()
                        capture = None
                    if writer is not None:
                        writer.release()
                        writer = None
                    with self.lock:
                        self.connected = False
                with self.lock:
                    stream_url = self.url
                if not stream_url:
                    self.stop_event.wait(0.5)
                    continue
                if capture is None or not capture.isOpened():
                    if capture is not None:
                        capture.release()
                    logging.info("Connexion au flux RTSP…")
                    capture = cv2.VideoCapture(
                        stream_url, cv2.CAP_FFMPEG,
                        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                         cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000],
                    )
                    if not capture.isOpened():
                        with self.lock:
                            self.connected = False
                        logging.warning("Connexion RTSP impossible; nouvel essai dans %.0f s", reconnect_delay)
                        capture.release()
                        capture = None
                        self.reconnect_event.wait(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, 30.0)
                        continue
                    reconnect_delay = 1.0
                    with self.lock:
                        self.connected = True
                    logging.info("Connexion RTSP réussie")
                ok, frame = capture.read()
                if not ok or frame is None:
                    logging.warning("Flux RTSP interrompu; reconnexion automatique")
                    capture.release()
                    capture = None
                    with self.lock:
                        self.connected = False
                    if writer is not None:
                        writer.release()
                        writer = None
                    self.reconnect_event.wait(1.0)
                    continue
                now = time.monotonic()
                with self.lock:
                    self.latest_frame = frame
                    self.capture_fps = 1 / (now - previous_frame_at) if previous_frame_at else 0.0
                    should_record = self.record_requested and self.recording_enabled
                    last_detection = self.last_detection
                previous_frame_at = now
                height, width = frame.shape[:2]
                if should_record and writer is None:
                    self.output_dir.mkdir(parents=True, exist_ok=True)
                    filename = self.output_dir / f"{datetime.now():%Y%m%d_%H%M%S}.mp4"
                    fps = capture.get(cv2.CAP_PROP_FPS)
                    writer = cv2.VideoWriter(str(filename), cv2.VideoWriter_fourcc(*"mp4v"),
                                             fps if fps and fps > 0 else 10.0, (width, height))
                    if not writer.isOpened():
                        logging.error("Impossible de créer le fichier MP4 dans captures/ (%dx%d)", width, height)
                        writer.release()
                        writer = None
                    else:
                        last_size = (width, height)
                        logging.info("[ALERTE] Début de l'enregistrement: %s", filename)
                if writer is not None:
                    if (width, height) != last_size:
                        writer.release()
                        writer = None
                        logging.warning("Résolution du flux modifiée; enregistrement fermé")
                    elif not should_record or time.monotonic() - last_detection >= self.no_detection_seconds:
                        writer.release()
                        writer = None
                        logging.info("Fin de l'enregistrement")
                    else:
                        writer.write(frame)
        finally:
            with self.lock:
                self.connected = False
            if capture is not None:
                capture.release()
            if writer is not None:
                writer.release()

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
                    if self.preview_enabled:
                        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
                        if ok:
                            self.latest_jpeg = jpeg.tobytes()
                            self.preview_sequence += 1
                    was_recording = self.record_requested
                    if detections and self.recording_enabled:
                        self.record_requested = True
                        self.last_detection = now
                    elif self.record_requested and now - self.last_detection >= self.no_detection_seconds:
                        self.record_requested = False
                for detection in detections:
                    logging.info("[ALERTE] %s détecté (%.0f%%)", detection.label, detection.confidence * 100)
                if detections and self.recording_enabled and not was_recording:
                    logging.info("Alarme déclenchée; démarrage de l'enregistrement")
            except Exception:
                logging.exception("Erreur pendant l'inférence TFLite")
            self.stop_event.wait(interval)

    def run(self) -> None:
        self.start()
        try:
            while not self.stop_event.wait(0.5):
                pass
        except KeyboardInterrupt:
            logging.info("Arrêt demandé")
        finally:
            self.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Détection TFLite ciblée sur flux RTSP")
    parser.add_argument("--mode", choices=("prod", "test"), default="prod",
                        help="prod: headless; test: aperçu web et réglages interactifs")
    parser.add_argument("--url", default=os.getenv("RTSP_URL"), help="URL RTSP (ou variable RTSP_URL)")
    parser.add_argument("--model", default="models/ssd_mobilenet_v2_coco_quant_postprocess.tflite")
    parser.add_argument("--labels", default="models/coco_labels.txt")
    parser.add_argument("--threshold", type=float, default=float(os.getenv("DETECTION_THRESHOLD", "0.5")))
    parser.add_argument("--interval", type=float, default=float(os.getenv("INFERENCE_INTERVAL", "0.5")))
    parser.add_argument("--no-detection-seconds", type=float, default=10.0)
    parser.add_argument("--output-dir", type=Path, default=Path("captures"))
    parser.add_argument("--host", default="127.0.0.1", help="Hôte du tableau en mode test")
    parser.add_argument("--port", type=int, default=8091, help="Port du tableau en mode test")
    args = parser.parse_args()
    if not args.url and args.mode == "prod":
        parser.error("Le mode prod exige --url ou RTSP_URL (ne pas inscrire d'identifiants dans le code).")
    if not 0 < args.threshold <= 1 or args.interval <= 0 or args.no_detection_seconds < 0:
        parser.error("Seuil, intervalle ou durée d'arrêt invalide.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cv2.setNumThreads(2)
    detector = TFLiteDetector(args.model, args.labels, args.threshold)
    engine = Surveillance(args.url or "", detector, args.threshold, args.interval,
                          args.no_detection_seconds, args.output_dir,
                          recording_enabled=True, preview_enabled=args.mode == "test")
    if args.mode == "prod":
        engine.run()
        return

    from surveillance_web import create_app

    engine.start()
    logging.info("Mode test — tableau disponible sur http://%s:%d", args.host, args.port)
    try:
        create_app(engine).run(host=args.host, port=args.port, debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        logging.info("Arrêt demandé")
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
