import os
import unittest
from unittest import mock

from app.services import visual_search as vs


class VisualSearchTests(unittest.TestCase):
    def test_missing_pinecone_config_raises_runtime_error(self):
        os.environ.pop("PINECONE_API_KEY", None)
        os.environ.pop("PINECONE_INDEX", None)
        vs._pinecone_client = None
        vs._pinecone_index = None

        with self.assertRaisesRegex(RuntimeError, "PINECONE_API_KEY|PINECONE_INDEX"):
            vs._get_pinecone_index()

    def test_unsupported_hf_provider_raises_specific_error(self):
        os.environ["HF_TOKEN"] = "fake-token"
        with mock.patch.object(vs, "_urllib_request", return_value=(400, b'{"error":"Model not supported by provider hf-inference"}', {})):
            with self.assertRaises(vs.HFEmbeddingUnavailableError):
                vs.embed_image_bytes(b"fake-image", "image/jpeg")


if __name__ == "__main__":
    unittest.main()
