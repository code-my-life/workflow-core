"""The comments of an issue, read page by page from a local fake GitHub that follows its `Link` header."""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

from workflow_core.adapters.github import AppTokens, GitHubClient

COMMENTS = [
    {
        "user": {"login": "code-my-life-factory[bot]" if n % 2 else "inconnu"},
        "body": f"commentaire {n}",
        "html_url": f"u{n}",
    }
    for n in range(1, 231)
]


class FakeGitHub(BaseHTTPRequestHandler):
    pages: ClassVar[list[int]] = []

    def _answer(self, body: object, link: str | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(201 if self.command == "POST" else 200)
        self.send_header("Content-Type", "application/json")
        if link:
            self.send_header("Link", link)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/orgs/code-my-life/installation":
            self._answer({"id": 7})
            return
        query = parse_qs(url.query)
        page, size = int(query.get("page", ["1"])[0]), int(query["per_page"][0])
        FakeGitHub.pages.append(page)
        base = f"http://{self.headers['Host']}{url.path}"
        link = f'<{base}?per_page={size}&page={page + 1}>; rel="next", <{base}?per_page={size}&page=3>; rel="last"'
        self._answer(COMMENTS[(page - 1) * size : page * size], link if page * size < len(COMMENTS) else None)

    def do_POST(self) -> None:
        self._answer({"token": "ghs_t", "expires_at": "2030-01-01T00:00:00Z", "permissions": {}})

    def log_message(self, *args) -> None:
        pass


def test_the_comments_are_read_across_every_page(tmp_path):
    key = tmp_path / "key.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    env_file = tmp_path / "core.env"
    env_file.write_text(f"GITHUB_APP_ID=1\nGITHUB_APP_PRIVATE_KEY_PATH={key}\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeGitHub.pages = []
    try:
        tokens = AppTokens(
            environ={},
            env_file=env_file,
            cache_dir=tmp_path / "state",
            api_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
        comments = GitHubClient(tokens).list_comments("code-my-life/workflow-core", 99)
    finally:
        server.shutdown()

    assert FakeGitHub.pages == [1, 2, 3]
    assert len(comments) == 230
    assert comments[149].body == "commentaire 150"
    assert (comments[0].author, comments[0].url) == ("code-my-life-factory[bot]", "u1")
    assert comments[1].author == "inconnu"
