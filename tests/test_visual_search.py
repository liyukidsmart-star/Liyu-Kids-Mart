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

    def test_hf_inference_url_defaults_to_api_inference(self):
        os.environ.pop("HF_INFERENCE_API_URL", None)
        os.environ.pop("HF_CLIP_MODEL", None)
        self.assertEqual(
            vs._hf_inference_url(),
            "https://api-inference.huggingface.co/models/openai/clip-vit-base-patch32"
        )

    def test_hf_inference_url_can_be_overridden(self):
        os.environ["HF_INFERENCE_API_URL"] = "https://custom-hf.example/models"
        os.environ["HF_CLIP_MODEL"] = "laion/CLIP-ViT-B-32"
        self.assertEqual(
            vs._hf_inference_url(),
            "https://custom-hf.example/models/laion/CLIP-ViT-B-32"
        )


if __name__ == "__main__":
    unittest.main()
