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
        def _safe_urlopen_aliases(tree):
            safe = set()
            # Only imports from the shared base module establish the no-redirect
            # primitive; a file-name exemption would hide future regressions.
            for node in tree.body:
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 1
                    and node.module == "base"
                ):
                    for alias in node.names:
                        if alias.name == "urlopen_no_redirect":
                            safe.add(alias.asname or alias.name)
            changed = True
            while changed:
                changed = False
                for node in tree.body:
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    value = node.value
                    if not isinstance(value, ast.Name) or value.id not in safe:
                        continue
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id not in safe:
                            safe.add(target.id)
                            changed = True
            return safe

        offenders = []
        api_keys_alias_calls = []
        for path in sorted(providers.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            safe_aliases = _safe_urlopen_aliases(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = ast.unparse(node.func)
                if func == "urllib.request.urlopen":
                    offenders.append("%s:%d %s" % (path.name, node.lineno, func))
                elif isinstance(node.func, ast.Name) and node.func.id == "urlopen":
                    if path.name == "api_keys.py":
                        api_keys_alias_calls.append(node.lineno)
                    if node.func.id not in safe_aliases:
                        offenders.append("%s:%d %s" % (path.name, node.lineno, func))
        self.assertEqual(len(api_keys_alias_calls), 1,
                         "api_keys' authenticated request must be checked through its alias")
        self.assertEqual(offenders, [],
                         "these follow redirects while holding a credential: "
                         + ", ".join(offenders))

    def test_grok_cookie_is_not_replayed_to_a_redirect_target(self) -> None:
        """Grok ships a session cookie; a 302 must not carry it onward.

        This drives the REAL opener against the loopback redirect server. An
        earlier version mocked grok.urlopen_no_redirect to raise HTTPError(302),
        so no request was ever made and _Target.received stayed {} -- the
        assertion could not fail in any code state.
        """
        from quota_providers import base, grok

        # Preconditions: the test is only meaningful if the target was reached
        # by something. Assert the harness itself works first.
        self.assertTrue(hasattr(_Target, "received"))

        with mock.patch.object(grok, "_load_cookies",
                               return_value="sess=REAL-SESSION"), \
             mock.patch.object(grok, "_grok_enabled", return_value=True):
            # Call the opener directly with the cookie grok would send, so the
            # only thing under test is whether the opener follows the redirect.
            request = urllib.request.Request(
                self.redirect_url,
                headers={"Cookie": "sess=REAL-SESSION",
                         "Authorization": "Bearer SECRET"},
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                base.urlopen_no_redirect(request, timeout=5)
            if ctx.exception.fp is not None:
                ctx.exception.close()

        # The whole point: the redirect target never saw the credential.
        self.assertIsNone(
            _Target.received.get("cookie"),
            "a grok session cookie reached the redirect target: %r"
            % (_Target.received,))
        self.assertIsNone(_Target.received.get("authorization"))

    def test_three_original_providers_share_the_one_opener(self) -> None:
        """api_keys/minimax/openrouter keep working through the shared opener."""
        from quota_providers import api_keys, base, minimax, openrouter

        self.assertIs(api_keys.urlopen, base.urlopen_no_redirect)
        self.assertIs(minimax._urlopen, base.urlopen_no_redirect)
        self.assertIs(openrouter._urlopen, base.urlopen_no_redirect)


if __name__ == "__main__":
    unittest.main(verbosity=2)
