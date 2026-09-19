"""Production API surface and safe-response regression tests."""

import unittest

from fastapi.testclient import TestClient

from app.main import IS_PRODUCTION, app


class ProductionAppSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not IS_PRODUCTION:
            raise unittest.SkipTest("run with APP_ENV=production")
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_public_surface_is_minimal(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        for path in ("/", "/docs", "/redoc", "/openapi.json"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_security_headers_and_cors(self):
        response = self.client.get("/health", headers={"origin": "https://app.placement-ai.com"})
        self.assertEqual(response.headers.get("access-control-allow-origin"), "https://app.placement-ai.com")
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(response.headers.get("content-security-policy"), "frame-ancestors 'none'")
        other = self.client.get("/health", headers={"origin": "https://attacker.example"})
        self.assertNotIn("access-control-allow-origin", other.headers)

    def test_unexpected_error_is_sanitized(self):
        path = "/_security_test_unexpected_error"

        async def fail():
            raise RuntimeError("private database details must not reach the caller")

        app.add_api_route(path, fail)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["message"], "Internal server error")
        self.assertNotIn("private database details", response.text)
        self.assertIn("x-request-id", response.headers)


if __name__ == "__main__":
    unittest.main()
