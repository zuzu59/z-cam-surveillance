import json
import stat
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

import cv2
import numpy as np

from app_version import APP_VERSION
from surveillance import Detection, Surveillance, TFLiteDetector, label_for_class, load_labels
from surveillance_config import DEFAULT_CONFIG, DEFAULT_ENABLED_LABELS, validate_config
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
        config_page = self.client.get("/configuration").get_data(as_text=True)
        self.assertIn('form="config-form"', config_page)
        self.assertIn("Sauvegarder et recharger", config_page)
        self.assertIn('id="enabled-labels"', config_page)
        self.assertIn('id="select-all-labels"', config_page)
        self.assertIn('id="clear-labels"', config_page)
        self.assertEqual(APP_VERSION, "0.0.6")
        self.assertIn(APP_VERSION.encode(), self.client.get("/about").data)
        self.assertNotIn(b"MODE TEST", self.client.get("/").data)

    def test_default_configuration_has_no_operating_mode(self):
        self.assertNotIn("mode", DEFAULT_CONFIG)
        self.assertEqual(set(DEFAULT_ENABLED_LABELS), set(DEFAULT_CONFIG["enabled_labels"]))

    def test_legacy_configuration_defaults_to_previous_detection_labels(self):
        legacy_config = DEFAULT_CONFIG.copy()
        legacy_config.pop("enabled_labels")
        migrated = validate_config(legacy_config)
        self.assertEqual(migrated["enabled_labels"], list(DEFAULT_ENABLED_LABELS))
        self.assertIsNot(migrated["enabled_labels"], DEFAULT_CONFIG["enabled_labels"])

    def test_tflite_detector_filters_by_selected_labels(self):
        detector = object.__new__(TFLiteDetector)
        detector.labels = ["person", "bicycle"]
        detector.enabled_labels = frozenset({"bicycle"})
        detector.threshold = 0.5
        detector.width = 16
        detector.height = 16
        detector.input = {"dtype": np.uint8, "index": 0}
        detector.outputs = [{"index": index} for index in range(4)]
        tensors = {
            0: np.array([[[0.1, 0.1, 0.5, 0.5], [0.2, 0.2, 0.6, 0.6]]], dtype=np.float32),
            1: np.array([[0.0, 1.0]], dtype=np.float32),
            2: np.array([[0.95, 0.90]], dtype=np.float32),
            3: np.array([2.0], dtype=np.float32),
        }

        class FakeInterpreter:
            def set_tensor(self, _index, _tensor):
                pass

            def invoke(self):
                pass

            def get_tensor(self, index):
                return tensors[index]

        detector.interpreter = FakeInterpreter()
        found = detector.detect(np.zeros((16, 16, 3), dtype=np.uint8))
        self.assertEqual([detection.label for detection in found], ["bicycle"])
        detector.enabled_labels = frozenset()
        self.assertEqual(detector.detect(np.zeros((16, 16, 3), dtype=np.uint8)), [])

    def test_settings_update_without_restart(self):
        response = self.client.post("/api/config", json={
            "threshold": 0.67,
            "interval": 0.25,
            "no_detection_seconds": 6,
            "recording_enabled": False,
            "enabled_labels": ["bus"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.engine.detector.threshold, 0.67)
        self.assertEqual(self.engine.settings_snapshot()["interval"], 0.25)
        self.assertFalse(self.engine.settings_snapshot()["recording_enabled"])
        self.assertEqual(self.engine.detector.enabled_labels, frozenset({"bus"}))

    def test_recording_frame_count_matches_source_time(self):
        self.assertEqual(self.engine._frame_target_for_elapsed(4.0, 25.0), 100)
        self.assertEqual(self.engine._frame_target_for_elapsed(10.0, 25.0), 250)
        self.assertEqual(self.engine._frame_target_for_elapsed(0.0, 25.0), 1)
        self.assertEqual(self.engine._frame_target_for_elapsed(10.0, 0.0), 1)

    def test_timeout_uses_updated_value_in_the_recording_decision(self):
        self.engine.recording_enabled = True
        self.engine.record_requested = True
        self.engine.no_detection_seconds = 12.0
        self.engine.last_detection = 100.0
        self.assertTrue(self.engine._should_record_unlocked(now=104.0))

        self.engine.update_settings({"no_detection_seconds": 4.0})
        self.assertFalse(self.engine._should_record_unlocked(now=104.0))
        self.assertFalse(self.engine.record_requested)

        self.engine.no_detection_seconds = 0.0
        self.engine.record_requested = True
        self.assertTrue(self.engine._should_record_unlocked(now=10_000.0))

    def test_rejects_non_list_detection_labels_without_partial_update(self):
        response = self.client.post("/api/config", json={"enabled_labels": "person"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.engine.config["enabled_labels"], list(DEFAULT_ENABLED_LABELS))

    def test_rejects_unknown_detection_label_without_partial_update(self):
        response = self.client.post("/api/config", json={"enabled_labels": ["not-a-model-label"]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.engine.config["enabled_labels"], list(DEFAULT_ENABLED_LABELS))

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
                "interval": 0.82,
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
            self.assertEqual(saved["threshold"], 0.66)
            self.assertEqual(saved["interval"], 0.82)
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertTrue(engine.settings_snapshot()["low_stream_configured"])
            self.assertTrue(engine.settings_snapshot()["high_stream_configured"])

    def test_reveal_endpoint_returns_only_the_requested_url(self):
        low_url = "rtsp://viewer:low-test-pass@192.0.2.30/substream"
        high_url = "rtsp://viewer:high-test-pass@192.0.2.30/main"
        engine = Surveillance(low_url, SimpleNamespace(threshold=0.5), 0.5, 0.5, 10,
                              Path("captures"), high_resolution_url=high_url)
        client = create_app(engine).test_client()

        config_body = client.get("/api/config").get_data(as_text=True)
        self.assertNotIn("low-test-pass", config_body)
        self.assertNotIn("high-test-pass", config_body)
        self.assertNotIn(low_url, client.get("/configuration").get_data(as_text=True))
        self.assertEqual(client.post("/api/config/reveal", json={"source": "low"}).status_code, 403)

        headers = {"X-Requested-With": "XMLHttpRequest"}
        low_response = client.post("/api/config/reveal", json={"source": "low"}, headers=headers)
        high_response = client.post("/api/config/reveal", json={"source": "high"}, headers=headers)
        self.assertEqual(low_response.status_code, 200)
        self.assertEqual(low_response.get_json(), {"url": low_url})
        self.assertEqual(high_response.status_code, 200)
        self.assertEqual(high_response.get_json(), {"url": high_url})
        invalid = client.post("/api/config/reveal", json={"source": "other"}, headers=headers)
        self.assertEqual(invalid.status_code, 400)

    def test_configuration_page_saves_all_json_parameters(self):
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "surveillance.json"
            engine = Surveillance("rtsp://camera/low", SimpleNamespace(threshold=0.5),
                                  0.5, 0.5, 10, Path("captures"),
                                  high_resolution_url="rtsp://camera/high",
                                  config_path=config_path, config_data=DEFAULT_CONFIG)
            client = create_app(engine).test_client()
            with patch("surveillance_web._schedule_application_restart") as schedule_restart:
                response = client.post("/api/config", json={
                    "low_resolution_url": "rtsp://camera/low",
                    "high_resolution_url": "rtsp://camera/high",
                    "threshold": 0.7,
                    "interval": 1.2,
                    "no_detection_seconds": 18,
                    "recording_enabled": False,
                    "enabled_labels": ["person", "bus"],
                    "output_dir": "event-clips",
                    "model": "models/custom.tflite",
                    "labels": "models/custom.txt",
                    "host": "127.0.0.1",
                    "port": 8123,
                    "restart_application": True,
                })
                schedule_restart.assert_called_once()
            self.assertEqual(response.status_code, 200)
            result = response.get_json()
            self.assertEqual(result["config"]["output_dir"], "event-clips")
            self.assertEqual(result["config"]["model"], "models/custom.tflite")
            self.assertEqual(result["config"]["host"], "127.0.0.1")
            self.assertEqual(result["config"]["port"], 8123)
            self.assertEqual(result["config"]["enabled_labels"], ["person", "bus"])
            self.assertIn("bus", result["config"]["available_labels"])
            self.assertNotIn("n/a", result["config"]["available_labels"])
            self.assertEqual(result["config"]["restart_required_fields"], ["host", "labels", "model", "port"])
            self.assertTrue(result["restart_scheduled"])
            status = client.get("/api/state").get_json()
            self.assertEqual(result["server_pid"], status["server_pid"])
            self.assertEqual(result["server_instance_id"], status["server_instance_id"])
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(set(saved), set(DEFAULT_CONFIG))
            self.assertEqual(saved["low_resolution_url"], "rtsp://camera/low")
            self.assertEqual(saved["high_resolution_url"], "rtsp://camera/high")
            self.assertEqual(saved["threshold"], 0.7)
            self.assertEqual(saved["interval"], 1.2)
            self.assertEqual(saved["no_detection_seconds"], 18)
            self.assertFalse(saved["recording_enabled"])
            self.assertEqual(saved["output_dir"], "event-clips")
            self.assertEqual(saved["port"], 8123)
            self.assertEqual(saved["enabled_labels"], ["person", "bus"])
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

    def test_recording_uses_high_resolution_stream_and_source_pts(self):
        with TemporaryDirectory() as output:
            engine = Surveillance("rtsp://camera/low", SimpleNamespace(threshold=0.5),
                                  0.5, 0.5, 10, Path(output), high_resolution_url="rtsp://camera/high")
            engine.record_requested = True
            clock = [100.0]
            engine.last_detection = clock[0]
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
                    clock[0] += 0.03  # Decoder drains buffered frames faster than their source PTS.
                    frames.append(np.zeros((720, 1280, 3), dtype=np.uint8))
                    if self.read_count == 3:
                        engine.stop_event.set()
                    return True, frames[-1]

                def get(self, prop):
                    if prop == cv2.CAP_PROP_FPS:
                        return 25.0
                    if prop == cv2.CAP_PROP_POS_MSEC:
                        return (self.read_count - 1) * 100.0
                    return 0.0

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
            with patch("surveillance.time.monotonic", side_effect=lambda: clock[0]), \
                    patch("surveillance.cv2.VideoCapture", side_effect=HighResolutionCapture), \
                    patch("surveillance.cv2.VideoWriter", return_value=writer) as writer_factory, \
                    patch.object(engine, "_finalize_recording") as finalize_recording:
                engine.recording_loop()
                engine.media_transcoder.shutdown(wait=True)
            self.assertEqual(capture_urls, ["rtsp://camera/high"])
            self.assertEqual(writer_factory.call_args.args[3], (1280, 720))
            self.assertEqual(writer_factory.call_args.args[2], 25.0)
            self.assertEqual(writer.written, 5)
            self.assertTrue(writer.released)
            finalize_recording.assert_called_once()
            raw_path, published_path = finalize_recording.call_args.args
            self.assertTrue(raw_path.name.startswith("."))
            self.assertTrue(raw_path.name.endswith(".recording.mp4"))
            self.assertFalse(published_path.name.startswith("."))
            self.assertEqual(published_path.suffix, ".mp4")
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
