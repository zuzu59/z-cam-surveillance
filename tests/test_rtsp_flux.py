import unittest
from urllib.parse import urlsplit

from tst_RTSP_flux import generate_rtsp_variants


class RtspVariantTests(unittest.TestCase):
    def test_base_url_generates_credential_path_stream_candidates(self):
        variants = generate_rtsp_variants("rtsp://admin:secret123@192.0.2.10:554")

        paths = {urlsplit(url).path for url in variants}
        self.assertIn(
            "/user=admin_password=secret123_channel=1_stream=0.sdp",
            paths,
        )
        self.assertIn(
            "/user=admin_password=secret123_channel=1_stream=1.sdp",
            paths,
        )
        self.assertIn(
            "/user=admin_password=secret123_channel=1_stream=2.sdp",
            paths,
        )
        self.assertIn(
            "/user=admin_password=secret123_channel=1_stream=3.sdp",
            paths,
        )

    def test_embedded_credentials_are_url_encoded_in_generated_path(self):
        variants = generate_rtsp_variants("rtsp://admin:p%40ss%2Fword@192.0.2.10:554")

        self.assertIn(
            "/user=admin_password=p%40ss%2Fword_channel=1_stream=0.sdp",
            {urlsplit(url).path for url in variants},
        )

    def test_without_credentials_does_not_generate_credential_path(self):
        variants = generate_rtsp_variants("rtsp://192.0.2.10:554")

        self.assertFalse(any("/user=" in urlsplit(url).path for url in variants))


if __name__ == "__main__":
    unittest.main()
