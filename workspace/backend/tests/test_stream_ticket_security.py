"""Security invariants for short-lived, owner-scoped stream tickets."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import stream_ticket


class StreamTicketSecurityTests(unittest.TestCase):
    def setUp(self):
        self.workspace = SimpleNamespace(
            id="workspace-a", owner_user_id="user-a", password_hash="test-only-secret"
        )

    def test_owner_ticket_is_valid_only_for_its_workspace_and_scope(self):
        ticket = stream_ticket.mint(self.workspace, "user-a", scopes=[stream_ticket.EVENTS_SCOPE])
        self.assertTrue(stream_ticket.verify(self.workspace, ticket, stream_ticket.EVENTS_SCOPE))
        self.assertFalse(stream_ticket.verify(self.workspace, ticket, stream_ticket.FILES_SCOPE))
        other = SimpleNamespace(id="workspace-b", owner_user_id="user-a", password_hash="test-only-secret")
        self.assertFalse(stream_ticket.verify(other, ticket, stream_ticket.EVENTS_SCOPE))

    def test_nonowner_cannot_mint_or_reuse_a_ticket(self):
        self.assertIsNone(stream_ticket.mint(self.workspace, "user-b"))
        ticket = stream_ticket.mint(self.workspace, "user-a")
        self.workspace.owner_user_id = "user-b"
        self.assertFalse(stream_ticket.verify(self.workspace, ticket, stream_ticket.EVENTS_SCOPE))

    def test_expired_or_tampered_ticket_is_rejected(self):
        with patch.object(stream_ticket.time, "time", return_value=100):
            ticket = stream_ticket.mint(self.workspace, "user-a", ttl_seconds=1)
        with patch.object(stream_ticket.time, "time", return_value=102):
            self.assertFalse(stream_ticket.verify(self.workspace, ticket, stream_ticket.EVENTS_SCOPE))
        self.assertFalse(stream_ticket.verify(self.workspace, ticket + "x", stream_ticket.EVENTS_SCOPE))

    def test_ticket_lifetime_is_clamped(self):
        now = int(time.time())
        ticket = stream_ticket.mint(self.workspace, "user-a", ttl_seconds=86400)
        payload = stream_ticket._unb64(ticket.split(".")[0]).decode()
        expires = int(payload.split(":")[2])
        self.assertLessEqual(expires, now + stream_ticket.TICKET_TTL_SECONDS + 1)


if __name__ == "__main__":
    unittest.main()
