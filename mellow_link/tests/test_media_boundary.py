import importlib
import os
import sys
import unittest


class TestMediaBoundary(unittest.TestCase):
    def setUp(self):
        for name in [
            "mellow_link.services",
            "mellow_link.services.image_service",
            "mellow_link.services.video_service",
            "mellow_link.media.services.image_service",
            "mellow_link.media.services.video_service",
            "mellow_link.routers.generation",
            "mellow_link.routers.media_generation",
        ]:
            sys.modules.pop(name, None)

    def test_services_package_does_not_eager_import_media(self):
        services = importlib.import_module("mellow_link.services")

        self.assertNotIn("mellow_link.services.image_service", sys.modules)
        self.assertNotIn("mellow_link.services.video_service", sys.modules)
        self.assertNotIn("mellow_link.media.services.image_service", sys.modules)
        self.assertNotIn("mellow_link.media.services.video_service", sys.modules)

        _ = services.DocumentRequest
        self.assertNotIn("mellow_link.services.image_service", sys.modules)
        self.assertNotIn("mellow_link.services.video_service", sys.modules)
        self.assertNotIn("mellow_link.media.services.image_service", sys.modules)
        self.assertNotIn("mellow_link.media.services.video_service", sys.modules)

        _ = services.ImageService
        self.assertIn("mellow_link.media.services.image_service", sys.modules)

    def test_legacy_service_shims_still_resolve(self):
        image_service_mod = importlib.import_module("mellow_link.services.image_service")
        video_service_mod = importlib.import_module("mellow_link.services.video_service")

        self.assertTrue(hasattr(image_service_mod, "ImageService"))
        self.assertTrue(hasattr(video_service_mod, "VideoService"))

    def test_media_facade_reports_off_when_disabled(self):
        os.environ["ENABLE_MEDIA_AI"] = "0"
        try:
            from mellow_link.config.settings import clear_settings_cache, get_settings
            clear_settings_cache()
            facade = importlib.import_module("mellow_link.media.facade")
            self.assertFalse(facade.media_enabled(get_settings()))
            self.assertEqual(facade.media_runtime_lines(get_settings()), ["  Media:    DISABLED (ENABLE_MEDIA_AI=0)"])
            self.assertEqual(facade.media_status_snapshot(), {})
        finally:
            os.environ.pop("ENABLE_MEDIA_AI", None)
            from mellow_link.config.settings import clear_settings_cache
            clear_settings_cache()

    def test_generation_routes_are_split_but_paths_preserved(self):
        generation = importlib.import_module("mellow_link.routers.generation")
        media_generation = importlib.import_module("mellow_link.routers.media_generation")

        generation_paths = {route.path for route in generation.router.routes}
        media_paths = {route.path for route in media_generation.router.routes}

        self.assertEqual(generation_paths, {"/generate-document"})
        self.assertEqual(media_paths, {"/generate-image"})

    def test_core_shims_point_to_media_canonical_modules(self):
        core_schemas = importlib.import_module("mellow_link.core.schemas")
        media_schemas = importlib.import_module("mellow_link.media.schemas")

        self.assertIs(core_schemas.ImageRequest, media_schemas.ImageRequest)
        self.assertIs(core_schemas.VideoRequest, media_schemas.VideoRequest)


if __name__ == "__main__":
    unittest.main()
