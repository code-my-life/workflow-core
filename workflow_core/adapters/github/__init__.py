"""The adapter to GitHub: the core's tokens of the `code-my-life-factory` App, and the issue comments it writes.

The App's id and the path of its private key come from the environment, else from the core's own `.env`
(`~/.config/workflow-core/.env`). The installation token is kept in the core's own cache, `app-token.json` of its
state folder (mode 600, under a lock), and renewed when less than 10 minutes are left. Nothing of the agent runtime
is ever read here: the factory's own cache stays the factory's. Standard library and `openssl` only.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from workflow_core.events import state_dir

API_URL = "https://api.github.com"
ORG = "code-my-life"
APP_ID_VARIABLE = "GITHUB_APP_ID"
KEY_PATH_VARIABLE = "GITHUB_APP_PRIVATE_KEY_PATH"
ENV_FILE = Path("~/.config/workflow-core/.env")
TOKEN_CACHE = "app-token.json"
TOKEN_LOCK = "app-token.lock"
# A cached token with less than this left is renewed.
MIN_LEFT_SECONDS = 600
TIMEOUT_SECONDS = 30


class AppConfigError(Exception):
    """The App cannot be used: a variable missing, or a key not found. The message names the variable."""


class GitHubError(Exception):
    """GitHub answered with an error, or could not be reached (`status` 0)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub {status} : {message}" if status else f"GitHub injoignable : {message}")
        self.status = status


def read_env_file(path: Path) -> dict[str, str]:
    """The `NAME=value` lines of a `.env` file; a missing or unreadable file is empty."""
    values: dict[str, str] = {}
    try:
        lines = path.expanduser().read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        name, sep, value = line.strip().partition("=")
        if sep and name and not name.startswith("#"):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[name.strip()] = value
    return values


def setting(name: str, environ: dict[str, str] | None = None, env_file: Path = ENV_FILE) -> str | None:
    """A variable of the environment, else of the core's `.env`; `None` when empty in both."""
    env = os.environ if environ is None else environ
    return env.get(name) or read_env_file(env_file).get(name) or None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def request(
    url: str, *, method: str = "GET", authorization: str | None = None, body: dict | None = None
) -> tuple[object, dict[str, str]]:
    """One call to GitHub's API: the JSON answer and the headers. Any failure is a `GitHubError`."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if authorization:
        headers["Authorization"] = authorization
    if data is not None:
        headers["Content-Type"] = "application/json"
    call = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(call, timeout=TIMEOUT_SECONDS) as answer:
            raw = answer.read()
            return (json.loads(raw) if raw else None), dict(answer.headers)
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read() or b"{}").get("message") or error.reason
        except ValueError:
            message = error.reason
        raise GitHubError(error.code, str(message)) from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise GitHubError(0, str(getattr(error, "reason", error))) from error


class AppTokens:
    """The installation tokens of the App on the organisation, minted and cached by the core alone."""

    def __init__(
        self,
        *,
        environ: dict[str, str] | None = None,
        env_file: Path = ENV_FILE,
        cache_dir: Path | None = None,
        api_url: str = API_URL,
        org: str = ORG,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.environ = environ
        self.env_file = env_file
        self.cache_dir = cache_dir if cache_dir is not None else state_dir(environ)
        self.api_url = api_url.rstrip("/")
        self.org = org
        self.clock = clock

    @property
    def cache_path(self) -> Path:
        return self.cache_dir / TOKEN_CACHE

    def _jwt(self) -> str:
        app_id = setting(APP_ID_VARIABLE, self.environ, self.env_file)
        if not app_id:
            raise AppConfigError(f"{APP_ID_VARIABLE} absent de l'environnement et de {self.env_file}")
        key_path = setting(KEY_PATH_VARIABLE, self.environ, self.env_file)
        if not key_path:
            raise AppConfigError(f"{KEY_PATH_VARIABLE} absent de l'environnement et de {self.env_file}")
        key = Path(key_path).expanduser()
        if not key.is_file():
            raise AppConfigError(f"{KEY_PATH_VARIABLE} : aucune clé à {key}")
        now = int(self.clock())
        header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = _b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode())
        signed = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(key)],
            input=f"{header}.{payload}".encode(),
            capture_output=True,
        )
        if signed.returncode != 0:
            raise AppConfigError(f"{KEY_PATH_VARIABLE} : la clé à {key} ne signe pas ({signed.stderr.decode()[-200:]})")
        return f"{header}.{payload}.{_b64(signed.stdout)}"

    def mint(self) -> dict:
        """A new installation token: `{"org", "token", "expires_at" (Unix seconds), "permissions"}`."""
        jwt = f"Bearer {self._jwt()}"
        installation, _ = request(f"{self.api_url}/orgs/{self.org}/installation", authorization=jwt)
        granted, _ = request(
            f"{self.api_url}/app/installations/{installation['id']}/access_tokens", method="POST", authorization=jwt
        )
        expires = datetime.fromisoformat(granted["expires_at"].replace("Z", "+00:00")).timestamp()
        return {
            "org": self.org,
            "token": granted["token"],
            "expires_at": expires,
            "permissions": granted.get("permissions") or {},
        }

    def current(self) -> dict:
        """The cached token while more than 10 minutes are left, else a new one, written to the cache."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with (self.cache_dir / TOKEN_LOCK).open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if cached.get("org") == self.org and float(cached["expires_at"]) - self.clock() > MIN_LEFT_SECONDS:
                    return cached
            except (OSError, ValueError, KeyError, TypeError):
                pass
            fresh = self.mint()
            temporary = self.cache_path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(fresh, file)
            temporary.chmod(0o600)
            temporary.replace(self.cache_path)
            return fresh

    def token(self) -> str:
        return self.current()["token"]


class GitHubClient:
    """The calls the core makes to GitHub, under the App's installation token."""

    def __init__(self, tokens: AppTokens) -> None:
        self.tokens = tokens

    def _call(self, path: str, *, method: str = "GET", body: dict | None = None) -> tuple[object, dict[str, str]]:
        return request(
            f"{self.tokens.api_url}{path}", method=method, authorization=f"token {self.tokens.token()}", body=body
        )

    def post_comment(self, repo: str, number: int, body: str) -> str:
        """Comment an issue; the comment's address."""
        answer, _ = self._call(f"/repos/{repo}/issues/{number}/comments", method="POST", body={"body": body})
        return answer.get("html_url", "") if isinstance(answer, dict) else ""

    def repository_readable(self, repo: str) -> bool:
        try:
            self._call(f"/repos/{repo}")
        except GitHubError as error:
            if error.status in (403, 404):
                return False
            raise
        return True
