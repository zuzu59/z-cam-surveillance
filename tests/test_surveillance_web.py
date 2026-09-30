import json
import stat
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np

from app_version import APP_VERSION
from surveillance import Detection, Surveillance, label_for_class, load_labels
from surveillance_config import DEFAULT_CONFIG
from surveillance_web import create_app


class SurveillanceDashboardTests(unittest.TestCase):
    def setUp(self):
        self.engine = Surveillance(
            "rtsp://192.0.2.15/substream",
            SimpleNamespace(threshold=0.5),
            0.5,
            0.5,
            10.0,
            Path("captures"),
        )
        self.client = create_app(self.engine).test_client()

    def test_coco_label_map_uses_zero_based_model_classes(self):
        labels = load_labels("models/coco_labels.txt")
        self.assertEqual(label_for_class(labels, 0), "person")
        self.assertEqual(label_for_class(labels, 1), "bicycle")
        self.assertEqual(label_for_class(labels, 64), "bed")
        self.assertEqual(label_for_class(labels, 90), "")

    def test_web_pages_and_navigation_render(self):
        for route in ("/", "/configuration", "/help", "/about"):
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200, route)
            self.assertIn(b"Configuration", response.data)
            self.assertIn(b"Aide", response.data)
            self.assertIn(b"propos", response.data)
        self.assertIn(b"Objets d\xc3\xa9tect\xc3\xa9s", self.client.get("/").data)
        self.assertIn(APP_VERSION.encode(), self.client.get("/about").data)
        self.assertNotIn(b"MODE TEST", self.client.get("/").data)

    def test_default_configuration_has_no_operating_mode(self):
        self.assertNotIn("mode", DEFAULT_CONFIG)

    def test_settings_update_without_restart(self):
        response = self.client.post("/api/config", json={
            "threshold": 0.65,
            "interval": 0.7,
            "no_detection_seconds": 6,
            "recording_enabled": False,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.engine.detector.threshold, 0.65)
        self.assertEqual(self.engine.settings_snapshot()["interval"], 0.7)
        self.assertFalse(self.engine.settings_snapshot()["recording_enabled"])

    def test_rejects_bad_config_without_partial_update(self):
        response = self.client.post("/api/config", json={
            "threshold": 0.8,
            "interval": 0.1,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.engine.threshold, 0.5)
        self.assertEqual(self.engine.interval, 0.5)

    def test_both_streams_and_settings_persist_without_exposing_urls(self):
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "surveillance.json"
            config = DEFAULT_CONFIG.copy()
            engine = Surveillance("", SimpleNamespace(threshold=0.5), 0.5, 0.5, 10,
                                  Path("captures"), config_path=config_path, config_data=config)
            client = create_app(engine).test_client()
            low_url = "rtsp://user:secret@192.0.2.20/low"
            high_url = "rtsp://user:secret@192.0.2.20/high"
            response = client.post("/api/config", json={
                "low_resolution_url": low_url,
                "high_resolution_url": high_url,
                "threshold": 0.66,
                "interval": 0.8,
                "no_detection_seconds": 12,
                "recording_enabled": True,
            })
            self.assertEqual(response.status_code, 200)
            body = client.get("/api/state").get_data(as_text=True)
            config_body = client.get("/api/config").get_data(as_text=True)
            self.assertNotIn("192.0.2.20", body)
            self.assertNotIn("secret", body)
            self.assertNotIn("192.0.2.20", config_body)
            self.assertNotIn("secret", config_body)
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["low_resolution_url"], low_url)
            self.assertEqual(saved["high_resolution_url"], high_url)
            self.assertEqual(saved["interval"], 0.8)
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertTrue(engine.settings_snapshot()["low_stream_configured"])
            self.assertTrue(engine.settings_snapshot()["high_stream_configured"])

    def test_configuration_page_saves_all_json_parameters(self):
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "surveillance.json"
            engine = Surveillance("rtsp://camera/low", SimpleNamespace(threshold=0.5),
                                  0.5, 0.5, 10, Path("captures"),
                                  high_resolution_url="rtsp://camera/high",
                                  config_path=config_path, config_data=DEFAULT_CONFIG)
            client = create_app(engine).test_client()
            response = client.post("/api/config", json={
                "threshold": 0.7,
                "interval": 1.2,
                "no_detection_seconds": 18,
                "recording_enabled": False,
                "output_dir": "event-clips",
                "model": "models/custom.tflite",
                "labels": "models/custom.txt",
                "host": "127.0.0.1",
                "port": 8123,
            })
            self.assertEqual(response.status_code, 200)
            result = response.get_json()
            self.assertEqual(result["config"]["output_dir"], "event-clips")
            self.assertEqual(result["config"]["model"], "models/custom.tflite")
            self.assertEqual(result["config"]["host"], "127.0.0.1")
            self.assertEqual(result["config"]["port"], 8123)
            self.assertEqual(result["config"]["restart_required_fields"], ["host", "labels", "model", "port"])
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["interval"], 1.2)
            self.assertEqual(saved["output_dir"], "event-clips")
            self.assertEqual(saved["port"], 8123)
            self.assertNotIn("mode", saved)
            self.assertEqual(engine.output_dir, Path("event-clips"))

    def test_rejects_non_rtsp_source(self):
        response = self.client.post("/api/config", json={"url": "https://example.invalid/camera"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.engine.low_resolution_url, "rtsp://192.0.2.15/substream")

    def test_missing_high_resolution_source_does_not_create_empty_clip(self):
        with TemporaryDirectory() as output:
            engine = Surveillance("rtsp://192.0.2.1/low", SimpleNamespace(threshold=0.5),
                                  0.5, 0.5, 1.0, Path(output))
            engine.record_requested = True
            worker = threading.Thread(target=engine.recording_loop, daemon=True)
            with patch("surveillance.cv2.VideoCapture") as capture_factory, \
                    patch("surveillance.cv2.VideoWriter") as writer_factory:
                worker.start()
                time.sleep(0.05)
                engine.stop_event.set()
                worker.join(timeout=1)
            capture_factory.assert_not_called()
            writer_factory.assert_not_called()
            self.assertEqual(list(Path(output).iterdir()), [])

    def test_recording_uses_high_resolution_stream(self):
        with TemporaryDirectory() as output:
            engine = Surveillance("rtsp://camera/low", SimpleNamespace(threshold=0.5),
                                  0.5, 0.5, 10, Path(output), high_resolution_url="rtsp://camera/high")
            engine.record_requested = True
            engine.last_detection = time.monotonic()
            frames = []

            class HighResolutionCapture:
                def __init__(self, url, *_args):
                    self.url = url
                    self.read_count = 0
                    capture_urls.append(url)

                def isOpened(self):
                    return True

                def read(self):
                    self.read_count += 1
                    frames.append(np.zeros((720, 1280, 3), dtype=np.uint8))
                    if self.read_count == 3:
                        engine.stop_event.set()
                    return True, frames[-1]

                def get(self, _property):
                    return 15.0

                def release(self):
                    pass

            class Writer:
                def __init__(self):
                    self.written = 0
                    self.released = False

                def isOpened(self):
                    return True

                def write(self, _frame):
                    self.written += 1

                def release(self):
                    self.released = True

            capture_urls = []
            writer = Writer()
            with patch("surveillance.cv2.VideoCapture", side_effect=HighResolutionCapture), \
                    patch("surveillance.cv2.VideoWriter", return_value=writer) as writer_factory:
                engine.recording_loop()
            self.assertEqual(capture_urls, ["rtsp://camera/high"])
            self.assertEqual(writer_factory.call_args.args[3], (1280, 720))
            self.assertEqual(writer.written, 3)
            self.assertTrue(writer.released)
            self.assertFalse(engine.recording_active)

    def test_inference_encodes_preview_and_detection_state(self):
        detector = SimpleNamespace(
            threshold=0.5,
            detect=lambda frame: [Detection("person", 0.91, (0.1, 0.2, 0.8, 0.7))],
        )
        engine = Surveillance("", detector, 0.5, 0.2, 10.0, Path("captures"))
        engine.latest_frame = np.zeros((48, 80, 3), dtype=np.uint8)
        worker = threading.Thread(target=engine.inference_loop, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 2
            sequence, jpeg = engine.preview_snapshot()
            while sequence == 0 and time.monotonic() < deadline:
                time.sleep(0.02)
                sequence, jpeg = engine.preview_snapshot()
            self.assertGreater(sequence, 0)
            self.assertTrue(jpeg.startswith(b"\xff\xd8"))
            state = engine.status_snapshot()
            self.assertEqual(state["detections"][0]["label"], "person")
            self.assertTrue(state["recording_requested"])
        finally:
            engine.stop_event.set()
            worker.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
