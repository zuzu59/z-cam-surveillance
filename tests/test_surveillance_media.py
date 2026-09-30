import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from surveillance_media import probe_video_codec, transcode_mp4_to_h264


class BrowserVideoCompatibilityTests(unittest.TestCase):
    def test_converts_mp4v_to_atomic_h264_mp4(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg et ffprobe sont nécessaires pour ce test d’intégration.")
        encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                                  capture_output=True, text=True, check=False)
        if "libx264" not in encoders.stdout:
            self.skipTest("FFmpeg ne contient pas l’encodeur libx264.")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source-mp4v.mp4"
            output = root / ".converted-h264.mp4"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
            if not writer.isOpened():
                self.skipTest("OpenCV ne peut pas créer la vidéo fixture MPEG-4.")
            for value in range(8):
                frame = np.full((240, 320, 3), value * 20, dtype=np.uint8)
                writer.write(frame)
            writer.release()
            self.assertEqual(probe_video_codec(source)[0], "mpeg4")

            self.assertTrue(transcode_mp4_to_h264(source, output))
            self.assertTrue(source.is_file(), "La source doit rester intacte jusqu’à sa publication atomique.")
            self.assertTrue(output.is_file())
            self.assertEqual(probe_video_codec(output), ("h264", "yuv420p"))
            self.assertFalse(list(root.glob("*.tmp.mp4")))


if __name__ == "__main__":
    unittest.main()
