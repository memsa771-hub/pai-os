"""Owner and machine-token authorization boundaries."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.access import verify_workspace_access


class WorkspaceAccessSecurityTests(unittest.TestCase):
    def setUp(self):
        self.workspace = SimpleNamespace(owner_user_id="owner-a", password_hash="machine-secret")
        self.db = MagicMock()

    def test_missing_and_wrong_credentials_are_rejected(self):
        self.assertFalse(verify_workspace_access(self.workspace, None, None, self.db))
        self.assertFalse(verify_workspace_access(self.workspace, "wrong", None, self.db))

    def test_correct_machine_token_works_without_bearer(self):
        self.assertTrue(verify_workspace_access(self.workspace, "machine-secret", None, self.db))

    def test_verified_other_user_cannot_cross_workspace_boundary(self):
        self.db.execute.return_value.scalar_one_or_none.return_value = SimpleNamespace(id="owner-b")
        with patch("app.access.verify_identity_claims", return_value={"email": "b@example.test"}):
            self.assertFalse(verify_workspace_access(self.workspace, None, "Bearer valid-b", self.db))

    def test_verified_owner_works_but_unverified_bearer_does_not(self):
        self.db.execute.return_value.scalar_one_or_none.return_value = SimpleNamespace(id="owner-a")
        with patch("app.access.verify_identity_claims", return_value={"email": "a@example.test"}):
            self.assertTrue(verify_workspace_access(self.workspace, None, "Bearer valid-a", self.db))
        with patch("app.access.verify_identity_claims", return_value=None):
            self.assertFalse(verify_workspace_access(self.workspace, None, "Bearer forged-a", self.db))


if __name__ == "__main__":
    unittest.main()
