"""Phase 6A — health endpoint and production-settings plumbing."""

from unittest import mock

from django.test import TestCase
from django.urls import reverse

from config.settings import env_bool, env_list


class HealthEndpointTests(TestCase):
    def test_health_returns_ok_without_authentication(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_exposes_nothing_sensitive(self):
        body = self.client.get(reverse("health")).content.decode()
        self.assertEqual(body, '{"status": "ok"}')

    def test_health_reports_database_failure(self):
        with mock.patch("config.views.connection") as fake_connection:
            fake_connection.cursor.side_effect = Exception("db down")
            response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error"})


class EnvHelperTests(TestCase):
    def test_env_bool(self):
        with mock.patch.dict("os.environ", {"X_FLAG": "true"}):
            self.assertTrue(env_bool("X_FLAG"))
        with mock.patch.dict("os.environ", {"X_FLAG": "0"}):
            self.assertFalse(env_bool("X_FLAG", default=True))
        self.assertFalse(env_bool("X_MISSING"))
        self.assertTrue(env_bool("X_MISSING", default=True))

    def test_env_list_parses_comma_separated_origins(self):
        with mock.patch.dict(
            "os.environ", {"X_LIST": " https://a.example.com , https://b.example.com ,"}
        ):
            self.assertEqual(
                env_list("X_LIST"),
                ["https://a.example.com", "https://b.example.com"],
            )
        self.assertEqual(env_list("X_MISSING"), [])
