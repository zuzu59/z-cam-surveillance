import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from surveillance import Detection, Surveillance, label_for_class, load_labels
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
            preview_enabled=True,
        )
        self.client = create_app(self.engine).test_client()

    def test_coco_label_map_uses_zero_based_model_classes(self):
        labels = load_labels("models/coco_labels.txt")
        self.assertEqual(label_for_class(labels, 0), "person")
        self.assertEqual(label_for_class(labels, 1), "bicycle")
        self.assertEqual(label_for_class(labels, 64), "bed")
        self.assertEqual(label_for_class(labels, 90), "")

    def test_dashboard_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Cam\xc3\xa9ra de surveillance", response.data)

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

    def test_new_stream_is_held_in_memory_and_never_returned(self):
        response = self.client.post("/api/config", json={
            "url": "rtsp://192.0.2.20/camera/main",
        })
        self.assertEqual(response.status_code, 200)
        body = self.client.get("/api/state").get_data(as_text=True)
        self.assertNotIn("192.0.2.20", body)
        self.assertTrue(self.engine.settings_snapshot()["stream_configured"])

    def test_rejects_non_rtsp_source(self):
        response = self.client.post("/api/config", json={"url": "https://example.invalid/camera"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.engine.url, "rtsp://192.0.2.15/substream")

    def test_test_mode_encodes_preview_and_detection_state(self):
        detector = SimpleNamespace(
            threshold=0.5,
            detect=lambda frame: [Detection("person", 0.91, (0.1, 0.2, 0.8, 0.7))],
        )
        engine = Surveillance("", detector, 0.5, 0.2, 10.0, Path("captures"), preview_enabled=True)
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
            self.assertTrue(state["recording"])
        finally:
            engine.stop_event.set()
            worker.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
