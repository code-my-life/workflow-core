"""The comments of an issue, read page by page from a local fake GitHub that follows its `Link` header."""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

from workflow_core.adapters.github import AppTokens, GitHubClient, IssueComment, OpenIssue

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


class IssueListGitHub(FakeGitHub):
    """Serves the issue list by pages of 2 with GitHub's `Link` header, and records each query."""

    queries: ClassVar[list[dict]] = []
    ISSUES: ClassVar[list[dict]] = [
        {"number": n, "user": {"login": "code-my-life-factory[bot]"}, "body": "", "state": "open"} for n in range(1, 6)
    ] + [{"number": 9, "pull_request": {}, "user": {"login": "code-my-life-factory[bot]"}}]

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if not url.path.endswith("/issues"):
            super().do_GET()
            return
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        IssueListGitHub.queries.append(query)
        page = int(query.get("page", "1"))
        base = f"http://{self.headers['Host']}{url.path}?{url.query.split('&page=')[0]}"
        more = page * 2 < len(self.ISSUES)
        self._answer(self.ISSUES[(page - 1) * 2 : page * 2], f'<{base}&page={page + 1}>; rel="next"' if more else None)


def test_list_issues_filters_by_creator_and_since_and_follows_every_page(tmp_path):
    key = tmp_path / "key.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    env_file = tmp_path / "core.env"
    env_file.write_text(f"GITHUB_APP_ID=1\nGITHUB_APP_PRIVATE_KEY_PATH={key}\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), IssueListGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    IssueListGitHub.queries = []
    now = 1794500000.0
    try:
        tokens = AppTokens(
            environ={},
            env_file=env_file,
            cache_dir=tmp_path / "state",
            api_url=f"http://127.0.0.1:{server.server_address[1]}",
            clock=lambda: now,
        )
        issues = GitHubClient(tokens).list_issues("code-my-life/workflow-core")
    finally:
        server.shutdown()

    opened = [q for q in IssueListGitHub.queries if q.get("state") == "open"]
    closed = [q for q in IssueListGitHub.queries if q.get("state") == "closed"]
    assert [q.get("page", "1") for q in opened] == ["1", "2", "3"]
    assert [q.get("page", "1") for q in closed] == ["1", "2", "3"]
    assert all(q["creator"] == "code-my-life-factory[bot]" and "since" not in q for q in opened)
    assert all(q["since"] == "2026-11-11T16:13:20Z" and "creator" not in q for q in closed)
    assert sorted(issue["number"] for issue in issues) == [1, 2, 3, 4, 5]


class OpenIssueGitHub(IssueListGitHub):
    """Serves the open issues of a repository by pages of 2, a pull request among them, as GitHub lists them; each
    query is recorded in `IssueListGitHub.queries`."""

    ISSUES: ClassVar[list[dict]] = [
        {
            "number": n,
            "html_url": f"https://github.com/code-my-life/workflow-essai/issues/{n}",
            "title": f"Issue {n}",
            "body": f"Corps {n}" if n != 2 else None,
            "user": {"login": "cddev" if n != 3 else "inconnu"},
            "labels": [{"name": "test"}, {"name": "type:essai"}] if n == 1 else [],
            "state": "open",
        }
        for n in range(1, 5)
    ] + [{"number": 9, "pull_request": {"url": "x"}, "user": {"login": "cddev"}, "state": "open"}]


def test_open_issues_are_read_page_by_page_without_pull_requests(tmp_path):
    key = tmp_path / "key.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    env_file = tmp_path / "core.env"
    env_file.write_text(f"GITHUB_APP_ID=1\nGITHUB_APP_PRIVATE_KEY_PATH={key}\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), OpenIssueGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    IssueListGitHub.queries = []
    try:
        tokens = AppTokens(
            environ={},
            env_file=env_file,
            cache_dir=tmp_path / "state",
            api_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
        issues = GitHubClient(tokens).list_open_issues("code-my-life/workflow-essai")
    finally:
        server.shutdown()

    assert [query.get("page", "1") for query in IssueListGitHub.queries] == ["1", "2", "3"]
    assert all(query["state"] == "open" and query["per_page"] == "100" for query in IssueListGitHub.queries)
    assert [issue.number for issue in issues] == [1, 2, 3, 4]
    assert issues[0] == OpenIssue(
        number=1,
        url="https://github.com/code-my-life/workflow-essai/issues/1",
        title="Issue 1",
        body="Corps 1",
        author="cddev",
        labels=("test", "type:essai"),
    )
    assert (issues[1].body, issues[2].author, issues[3].labels) == ("", "inconnu", ())


class CommentsSinceGitHub(FakeGitHub):
    """Serves an issue's comments from `since` on by pages of 2, with ids, creation times and reaction counts; the
    reactions of a comment by pages of 1; and takes the reactions posted. Each query and each posted reaction is
    recorded."""

    queries: ClassVar[list[tuple[str, dict]]] = []
    posted: ClassVar[list[tuple[str, dict]]] = []
    COMMENTS: ClassVar[list[dict]] = [
        {
            "id": 500 + n,
            "user": {"login": "inconnu"},
            "body": f"/essai valider {n}",
            "html_url": f"c{n}",
            "created_at": f"2026-11-14T10:0{n}:00Z",
            "reactions": {"total_count": 2 if n == 4 else 0},
        }
        for n in range(1, 6)
    ]
    REACTIONS: ClassVar[list[dict]] = [
        {"content": "eyes", "user": {"login": "inconnu"}},
        {"content": "+1", "user": {"login": "code-my-life-factory[bot]"}},
    ]

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/orgs/code-my-life/installation":
            self._answer({"id": 7})
            return
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        CommentsSinceGitHub.queries.append((url.path, query))
        page = int(query.get("page", "1"))
        base = f"http://{self.headers['Host']}{url.path}?{url.query.split('&page=')[0]}"
        if url.path.endswith("/reactions"):
            items, size = self.REACTIONS, 1
        else:
            since = query.get("since", "")
            items, size = [comment for comment in self.COMMENTS if comment["created_at"] >= since], 2
        more = page * size < len(items)
        self._answer(items[(page - 1) * size : page * size], f'<{base}&page={page + 1}>; rel="next"' if more else None)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path.endswith("/reactions"):
            CommentsSinceGitHub.posted.append(
                (url.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            )
        super().do_POST()


def test_comments_are_read_page_by_page_since_the_last_read(tmp_path):
    key = tmp_path / "key.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    env_file = tmp_path / "core.env"
    env_file.write_text(f"GITHUB_APP_ID=1\nGITHUB_APP_PRIVATE_KEY_PATH={key}\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), CommentsSinceGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    CommentsSinceGitHub.queries, CommentsSinceGitHub.posted = [], []
    repo = "code-my-life/workflow-essai"
    try:
        tokens = AppTokens(
            environ={},
            env_file=env_file,
            cache_dir=tmp_path / "state",
            api_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
        client = GitHubClient(tokens)
        every = client.list_comments_since(repo, 7)
        since = client.list_comments_since(repo, 7, "2026-11-14T10:02:00Z")
        client.add_reaction(repo, 504, "confused")
    finally:
        server.shutdown()

    comments = [(path, query) for path, query in CommentsSinceGitHub.queries if path.endswith("/7/comments")]
    assert [(query.get("since"), query.get("page", "1")) for _, query in comments] == [
        (None, "1"),
        (None, "2"),
        (None, "3"),
        ("2026-11-14T10:02:00Z", "1"),
        ("2026-11-14T10:02:00Z", "2"),
    ]
    assert all(query["per_page"] == "100" for _, query in comments)
    assert [comment.id for comment in every] == [501, 502, 503, 504, 505]
    assert [comment.id for comment in since] == [502, 503, 504, 505]
    assert every[0] == IssueComment(
        author="inconnu",
        body="/essai valider 1",
        url="c1",
        id=501,
        created_at="2026-11-14T10:01:00Z",
        app_reactions=(),
    )
    # Only the comment with reactions has them read, page by page, and only the App's are kept.
    reactions = [query.get("page", "1") for path, query in CommentsSinceGitHub.queries if path.endswith("/reactions")]
    assert reactions == ["1", "2"] * 2
    assert every[3].app_reactions == ("+1",)
    assert CommentsSinceGitHub.posted == [(f"/repos/{repo}/issues/comments/504/reactions", {"content": "confused"})]
