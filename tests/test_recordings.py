import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from surveillance import Surveillance
from surveillance_web import create_app


class RecordingLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "clips"
        self.directory.mkdir()
        engine = Surveillance("", SimpleNamespace(threshold=0.5), 0.5, 0.5, 10,
                              self.directory)
        self.client = create_app(engine).test_client()

    def tearDown(self):
        self.temp.cleanup()

    def create_clip(self, name="clip-test.mp4", data=b"not-a-real-video"):
        path = self.directory / name
        path.write_bytes(data)
        return path

    def test_recordings_page_is_in_navigation(self):
        response = self.client.get("/recordings")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Biblioth\xc3\xa8que", response.data)
        self.assertIn(b"Enregistrements", response.data)
        self.assertIn(b"recordings.css", response.data)
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn('href="/recordings"', home)

    def test_listing_filters_mp4_names_case_insensitively_and_paginates(self):
        for index in range(43):
            self.create_clip(f"archive-{index:03}.MP4")
        self.create_clip("garage-east.mp4")
        self.create_clip("notes.txt")
        (self.directory / "nested").mkdir()
        (self.directory / "nested" / "hidden.mp4").write_bytes(b"nested")

        first_page = self.client.get("/api/recordings?page=0").get_json()
        second_page = self.client.get("/api/recordings?page=1").get_json()
        self.assertEqual(first_page["total"], 44)
        self.assertEqual(len(first_page["files"]), 40)
        self.assertEqual(len(second_page["files"]), 4)
        self.assertEqual(first_page["page_size"], 40)
        self.assertNotIn(str(self.directory), self.client.get("/api/recordings").get_data(as_text=True))

        filtered = self.client.get("/api/recordings?q=GARAGE").get_json()
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["files"][0]["name"], "garage-east.mp4")
        self.assertEqual(self.client.get("/api/recordings?page=-1").status_code, 400)
        self.assertEqual(self.client.get("/api/recordings?q=" + "x" * 161).status_code, 400)

    def test_listing_skips_symlinks(self):
        self.create_clip()
        target = Path(self.temp.name) / "outside.mp4"
        target.write_bytes(b"outside")
        try:
            (self.directory / "outside-link.mp4").symlink_to(target)
        except OSError:
            self.skipTest("Les liens symboliques ne sont pas disponibles.")
        result = self.client.get("/api/recordings").get_json()
        self.assertEqual([item["name"] for item in result["files"]], ["clip-test.mp4"])

    def test_details_include_probe_metadata_without_absolute_paths(self):
        self.create_clip("details.mp4")
        metadata = {
            "duration_seconds": 12.5, "width": 1280, "height": 720,
            "codec": "mpeg4", "frame_rate": "25/1", "bit_rate": 800000,
            "format": "mov,mp4,m4a,3gp,3g2,mj2",
        }
        with patch("surveillance_recordings._probe_media", return_value=metadata):
            response = self.client.get("/api/recordings/details.mp4")
        self.assertEqual(response.status_code, 200)
        details = response.get_json()
        self.assertEqual(details["duration_seconds"], 12.5)
        self.assertEqual(details["width"], 1280)
        self.assertEqual(details["codec"], "mpeg4")
        self.assertNotIn("path", details)
        self.assertNotIn(str(self.directory), response.get_data(as_text=True))

    def test_video_route_supports_byte_ranges_for_seeking(self):
        path = self.create_clip(data=b"0123456789")
        response = self.client.get("/api/recordings/clip-test.mp4/video",
                                   headers={"Range": "bytes=2-5"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"2345")
        try:
            self.assertEqual(response.headers["Content-Range"], "bytes 2-5/10")
            self.assertEqual(response.mimetype, "video/mp4")
            self.assertTrue(path.exists())
        finally:
            response.close()

    def test_rejects_traversal_and_non_mp4_paths(self):
        outside = Path(self.temp.name) / "outside.mp4"
        outside.write_bytes(b"private")
        self.assertIn(self.client.get("/api/recordings/%2E%2E%2Foutside.mp4").status_code, (400, 404))
        self.assertEqual(self.client.get("/api/recordings/../outside.mp4/video").status_code, 404)
        self.assertEqual(self.client.get("/api/recordings/notes.txt/video").status_code, 404)
        self.assertTrue(outside.exists())

    def test_delete_requires_custom_header_and_removes_only_selected_clip(self):
        target = self.create_clip("selected.mp4")
        other = self.create_clip("keep.mp4")
        archive = self.directory / ".originals"
        archive.mkdir()
        archived_original = archive / target.name
        archived_original.hardlink_to(target)
        path = "/api/recordings/selected.mp4"
        denied = self.client.delete(path)
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(target.exists())
        deleted = self.client.delete(path, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json(), {"ok": True, "name": "selected.mp4"})
        self.assertFalse(target.exists())
        self.assertFalse(archived_original.exists())
        self.assertTrue(other.exists())
        missing = self.client.delete(path, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
