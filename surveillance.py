#!/usr/bin/env python3
"""Headless RTSP object detection and event recording for Raspberry Pi."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

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
# COCO's 80-category order, matching the usual SSD MobileNet COCO TFLite output IDs.
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


def load_labels(path: str | None) -> list[str]:
    if not path:
        return [label.strip() for label in COCO_LABELS]
    labels: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Accept label files formatted as either `id label` or one label per line.
        parts = line.split(maxsplit=1)
        labels.append(parts[1] if len(parts) == 2 and parts[0].isdigit() else line)
    return labels


class TFLiteDetector:
    def __init__(self, model_path: str, labels_path: str | None, threshold: float):
        if Interpreter is None:
            raise RuntimeError("TFLite introuvable. Installez tflite-runtime (ou tensorflow) dans cet environnement.")
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

    def detect(self, frame: np.ndarray) -> list[tuple[str, float]]:
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
        # SSD postprocess models generally emit boxes, classes, scores, count in this order.
        values = [np.squeeze(self.interpreter.get_tensor(item["index"])) for item in self.outputs]
        named: dict[str, np.ndarray] = {}
        for item, value in zip(self.outputs, values):
            name = item["name"].lower()
            for key in ("box", "class", "score", "count"):
                if key in name:
                    named[key] = value
        classes = named.get("class", values[1] if len(values) > 1 else np.array([])).reshape(-1)
        scores = named.get("score", values[2] if len(values) > 2 else np.array([])).reshape(-1)
        count_arr = named.get("count", values[3] if len(values) > 3 else np.array([])).reshape(-1)
        count = min(int(count_arr[0]) if count_arr.size else len(scores), len(scores), len(classes))
        detections: list[tuple[str, float]] = []
        for class_id, score in zip(classes[:count], scores[:count]):
            confidence = float(score)
            index = int(class_id)
            if confidence < self.threshold:
                continue
            # This downloaded SSD model uses COCO category IDs (1-based, with `n/a` gaps).
            if len(self.labels) == 90 and self.labels[11].lower() == "n/a":
                label = self.labels[index - 1] if 0 < index <= len(self.labels) else ""
            else:
                label = self.labels[index] if 0 <= index < len(self.labels) else ""
            if label in TARGETS:
                detections.append((label, confidence))
        return detections


class Surveillance:
    def __init__(self, url: str, detector: TFLiteDetector, threshold: float,
                 interval: float, no_detection_seconds: float, output_dir: Path):
        self.url = url
        self.detector = detector
        self.threshold = threshold
        self.interval = interval
        self.no_detection_seconds = no_detection_seconds
        self.output_dir = output_dir
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest_frame: np.ndarray | None = None
        self.last_detection = 0.0
        self.record_requested = False

    def capture_loop(self) -> None:
        capture: Any = None
        writer: Any = None
        last_size: tuple[int, int] | None = None
        reconnect_delay = 1.0
        try:
            while not self.stop_event.is_set():
                if capture is None or not capture.isOpened():
                    if capture is not None:
                        capture.release()
                    logging.info("Connexion au flux RTSP…")
                    capture = cv2.VideoCapture(
                        self.url,
                        cv2.CAP_FFMPEG,
                        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                         cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000],
                    )
                    if not capture.isOpened():
                        logging.warning("Connexion RTSP impossible; nouvel essai dans %.0f s", reconnect_delay)
                        capture.release()
                        capture = None
                        self.stop_event.wait(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, 30.0)
                        continue
                    reconnect_delay = 1.0
                    logging.info("Connexion RTSP réussie")
                ok, frame = capture.read()
                if not ok or frame is None:
                    logging.warning("Flux RTSP interrompu; reconnexion automatique")
                    capture.release()
                    capture = None
                    if writer is not None:
                        writer.release()
                        writer = None
                    continue
                with self.lock:
                    self.latest_frame = frame
                    should_record = self.record_requested
                    last_detection = self.last_detection
                height, width = frame.shape[:2]
                if should_record and writer is None:
                    self.output_dir.mkdir(parents=True, exist_ok=True)
                    filename = self.output_dir / f"{datetime.now():%Y%m%d_%H%M%S}.mp4"
                    writer = cv2.VideoWriter(str(filename), cv2.VideoWriter_fourcc(*"mp4v"),
                                             max(capture.get(cv2.CAP_PROP_FPS), 10.0), (width, height))
                    if not writer.isOpened():
                        logging.error("Impossible de créer le fichier vidéo %s", filename)
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
                    elif not should_record and time.monotonic() - last_detection >= self.no_detection_seconds:
                        writer.release()
                        writer = None
                        logging.info("Fin de l'enregistrement (absence de détection depuis %.0f s)", self.no_detection_seconds)
                    else:
                        writer.write(frame)
        finally:
            if capture is not None:
                capture.release()
            if writer is not None:
                writer.release()

    def inference_loop(self) -> None:
        while not self.stop_event.is_set():
            with self.lock:
                frame = None if self.latest_frame is None else self.latest_frame.copy()
            if frame is None:
                self.stop_event.wait(0.1)
                continue
            try:
                detections = self.detector.detect(frame)
                if detections:
                    now = time.monotonic()
                    with self.lock:
                        was_recording = self.record_requested
                        self.record_requested = True
                        self.last_detection = now
                    for label, confidence in detections:
                        logging.info("[ALERTE] %s détecté (%.0f%%)", label, confidence * 100)
                    if not was_recording:
                        logging.info("Alarme déclenchée; l'enregistrement démarre")
                else:
                    with self.lock:
                        if self.record_requested and time.monotonic() - self.last_detection >= self.no_detection_seconds:
                            self.record_requested = False
            except Exception:
                logging.exception("Erreur pendant l'inférence TFLite")
            self.stop_event.wait(self.interval)

    def run(self) -> None:
        capture_thread = threading.Thread(target=self.capture_loop, name="rtsp-capture", daemon=True)
        inference_thread = threading.Thread(target=self.inference_loop, name="tflite-inference", daemon=True)
        capture_thread.start()
        inference_thread.start()
        try:
            while not self.stop_event.wait(0.5):
                pass
        except KeyboardInterrupt:
            logging.info("Arrêt demandé")
        finally:
            self.stop_event.set()
            capture_thread.join(timeout=5)
            inference_thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Détection TFLite ciblée sur flux RTSP")
    parser.add_argument("--url", default=os.getenv("RTSP_URL"), help="URL RTSP (ou variable RTSP_URL)")
    parser.add_argument("--model", default="models/ssd_mobilenet_v2_coco_quant_postprocess.tflite")
    parser.add_argument("--labels", default="models/coco_labels.txt", help="Labels COCO fournis dans models/")
    parser.add_argument("--threshold", type=float, default=0.5, help="Confiance minimale entre 0 et 1")
    parser.add_argument("--interval", type=float, default=0.5, help="Intervalle d'inférence en secondes")
    parser.add_argument("--no-detection-seconds", type=float, default=10.0)
    parser.add_argument("--output-dir", type=Path, default=Path("captures"))
    args = parser.parse_args()
    if not args.url:
        parser.error("Fournir --url ou définir RTSP_URL (ne pas inscrire d'identifiants dans le code).")
    if not 0 < args.threshold <= 1 or args.interval <= 0 or args.no_detection_seconds < 0:
        parser.error("Seuil, intervalle ou durée d'arrêt invalide.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cv2.setNumThreads(2)
    detector = TFLiteDetector(args.model, args.labels, args.threshold)
    Surveillance(args.url, detector, args.threshold, args.interval,
                 args.no_detection_seconds, args.output_dir).run()


if __name__ == "__main__":
    main()
