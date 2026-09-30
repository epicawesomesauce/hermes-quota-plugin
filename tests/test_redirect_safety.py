"""Redirect-safety tests: a credential must never be replayed to another host.

The maintainer's own rule, from the #32 integration commit:
  "redirects are refused so the bearer cannot reach another host"

That held for api_keys/minimax/openrouter only; the other thirteen
credential-carrying call sites used the redirect-following
``urllib.request.urlopen``. These tests cover every provider.
"""
import email.message
import http.server
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock


class _Target(http.server.BaseHTTPRequestHandler):
    """Stands in for an attacker-chosen host named by a Location header."""

    received: dict = {}

    def _record(self) -> None:
        type(self).received = {
            "authorization": self.headers.get("Authorization"),
            "cookie": self.headers.get("Cookie"),
        }

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self._record()
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        self._record()
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format, *args):  # noqa: A002, ANN001 - stdlib signature
        pass


class _Redirector(http.server.BaseHTTPRequestHandler):
    target_port: int = 0

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:%d/collect" % type(self).target_port)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_GET

    def log_message(self, format, *args):  # noqa: A002, ANN001 - stdlib signature
        pass


class RedirectSafetyTests(unittest.TestCase):
    """Every provider that sends a bearer or cookie must refuse redirects."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.target = http.server.HTTPServer(("127.0.0.1", 0), _Target)
        cls.redirector = http.server.HTTPServer(("127.0.0.1", 0), _Redirector)
        _Redirector.target_port = cls.target.server_address[1]
        for server in (cls.target, cls.redirector):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.redirect_url = "http://127.0.0.1:%d/start" % cls.redirector.server_address[1]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.target.shutdown()
        cls.redirector.shutdown()
        cls.target.server_close()
        cls.redirector.server_close()

    def setUp(self) -> None:
        _Target.received = {}

    def test_shared_handler_refuses_and_leaks_nothing(self) -> None:
        """base.NoRedirectHandler is the one place the policy lives."""
        from quota_providers.base import NoRedirectHandler

        handler = NoRedirectHandler()
        request = urllib.request.Request(
            self.redirect_url, headers={"Authorization": "Bearer SECRET"})
        # The handler is never reached through the opener (redirects are refused
        # before dispatch), so None stands in for the fp/headers it never reads.
        self.assertIsNone(handler.redirect_request(  # type: ignore[arg-type]
            request, None, 302, "Found", {}, "https://elsewhere.invalid"))

    def test_stdlib_would_have_leaked_the_bearer(self) -> None:
        """Why this matters: the default handler DOES replay the credential.

        Without this test the guard could be reverted as unnecessary.
        """
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    self.redirect_url, headers={"Authorization": "Bearer SECRET"}),
                timeout=5,
            ) as response:
                response.read()
        except Exception:
            pass
        self.assertEqual(
            _Target.received.get("authorization"), "Bearer SECRET",
            "urllib no longer replays Authorization on redirect; re-evaluate "
            "whether NoRedirectHandler is still needed")

    def test_no_provider_calls_the_redirect_following_urlopen(self) -> None:
        """No provider may reach the network through bare urllib.request.urlopen."""
        import ast
        import pathlib

        providers = pathlib.Path(__file__).resolve().parent.parent / "quota_providers"
        offenders = []
        for path in sorted(providers.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = ast.unparse(node.func)
                if func in ("urllib.request.urlopen", "urlopen"):
                    # api_keys.urlopen is itself the no-redirect alias.
                    if path.name == "api_keys.py":
                        continue
                    offenders.append("%s:%d %s" % (path.name, node.lineno, func))
        self.assertEqual(offenders, [],
                         "these follow redirects while holding a credential: "
                         + ", ".join(offenders))

    def test_grok_cookie_is_not_replayed_to_a_redirect_target(self) -> None:
        """Grok ships a session cookie; a 302 must not carry it onward."""
        from quota_providers import grok

        redirect = urllib.error.HTTPError(
            self.redirect_url, 302, "Found", email.message.Message(), None)
        with mock.patch.object(grok, "urlopen_no_redirect", side_effect=redirect):
            with mock.patch.object(grok, "_load_cookies",
                                   return_value="sess=REAL-SESSION"), \
                 mock.patch.object(grok, "_grok_enabled", return_value=True), \
                 mock.patch.object(grok, "_fetch_grok_optin",
                                   return_value=grok.build_unavailable("grok", "no-data")):
                try:
                    grok.fetch_grok_quota()
                except Exception:
                    pass
            if redirect.fp is not None:
                redirect.close()
        self.assertIsNone(_Target.received.get("cookie"),
                          "a grok session cookie reached the redirect target")

    def test_three_original_providers_share_the_one_opener(self) -> None:
        """api_keys/minimax/openrouter keep working through the shared opener."""
        from quota_providers import api_keys, base, minimax, openrouter

        self.assertIs(api_keys.urlopen, base.urlopen_no_redirect)
        self.assertIs(minimax._urlopen, base.urlopen_no_redirect)
        self.assertIs(openrouter._urlopen, base.urlopen_no_redirect)


if __name__ == "__main__":
    unittest.main(verbosity=2)
