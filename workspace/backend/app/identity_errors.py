# -*- coding: utf-8 -*-
"""Identity failures that callers must tell apart.

Its own module, deliberately. This type is the contract between the provider
code that raises it (app/firebase_auth.py) and the handler that turns it into
a status code (app/main.py), and FastAPI binds exception handlers by CLASS
OBJECT. Defining it in firebase_auth meant that reloading that module — which
the test suite does — minted a new class, silently unbound the handler, and
turned a 503 back into a 500. Nothing reloads modules in production, but a
contract that depends on no one ever doing so is a thin one.
"""


class IdentityUnavailable(Exception):
    """We could not verify the token, as distinct from verifying and refusing it.

    The difference is the whole point. A caller that cannot tell them apart
    answers 401 to a healthy client whose identity provider happened to be
    unreachable, and the client — correctly trusting a 401 — ends the session.
    A student is signed out because Supabase hiccupped. Routes answer 503 for
    this instead; see the handler in app/main.py.
    """
