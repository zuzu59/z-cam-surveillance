import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import transcode_recordings
from surveillance_media import probe_video_codec


class ExistingRecordingMigrationTests(unittest.TestCase):
    def test_migration_archives_source_and_replaces_with_h264(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg et ffprobe sont nécessaires pour ce test d’intégration.")
        encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                                  capture_output=True, text=True, check=False)
        if "libx264" not in encoders.stdout:
            self.skipTest("FFmpeg ne contient pas l’encodeur libx264.")

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "clip.mp4"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
            if not writer.isOpened():
                self.skipTest("OpenCV ne peut pas créer la vidéo fixture MPEG-4.")
            for value in range(8):
                writer.write(np.full((240, 320, 3), value * 20, dtype=np.uint8))
            writer.release()
            original_size = source.stat().st_size
            self.assertEqual(probe_video_codec(source)[0], "mpeg4")

            with patch("sys.argv", ["transcode_recordings.py", "--directory", str(directory), "--apply"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(transcode_recordings.main(), 0)

            backup = directory / ".originals" / "clip.mp4"
            self.assertEqual(probe_video_codec(source), ("h264", "yuv420p"))
            self.assertEqual(probe_video_codec(backup)[0], "mpeg4")
            self.assertEqual(backup.stat().st_size, original_size)


if __name__ == "__main__":
    unittest.main()
