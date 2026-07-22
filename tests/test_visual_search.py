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

    def test_hf_inference_urls_use_fallback_list(self):
        os.environ.pop("HF_INFERENCE_API_URL", None)
        os.environ["HF_INFERENCE_FALLBACK_URLS"] = "https://alt1.example/models, https://alt2.example/models "
        urls = vs._hf_inference_urls()
        self.assertEqual(urls[0], "https://api-inference.huggingface.co/models")
        self.assertEqual(urls[1:], ["https://alt1.example/models", "https://alt2.example/models"])

    def test_prepare_image_url_for_fetch_rewrites_media_urls(self):
        os.environ.pop("APP_URL", None)
        os.environ.pop("IMAGE_CDN_BASE_URL", None)
        rewritten = vs._prepare_image_url_for_fetch("https://liyu-kids-mart.liyukidsmart.workers.dev/media/abc123")
        self.assertEqual(rewritten, "/media/abc123")


if __name__ == "__main__":
    unittest.main()
