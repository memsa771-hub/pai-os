import asyncio
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from app.routers import shares


class _Rows:
    def scalars(self):
        return self

    def all(self):
        return []


class _Db:
    def execute(self, _statement):
        return _Rows()


class ShareAuthorizationTests(unittest.TestCase):
    def test_complete_authorization_header_reaches_access_verifier(self):
        workspace = SimpleNamespace(id="workspace-id")
        received = []

        def verify(candidate, machine_token, authorization):
            received.append((candidate, machine_token, authorization))
            return True

        with (
            patch.object(shares, "_resolve_workspace", return_value=workspace),
            patch.object(shares, "_verify_workspace_access", side_effect=verify),
        ):
            response = asyncio.run(
                shares.list_shares(
                    network="personal-workspace",
                    db=_Db(),
                    x_workspace_token=None,
                    authorization="Bearer owner-access-token",
                )
            )

        self.assertIsInstance(response, dict)
        self.assertEqual(
            received,
            [(workspace, None, "Bearer owner-access-token")],
        )


if __name__ == "__main__":
    unittest.main()
