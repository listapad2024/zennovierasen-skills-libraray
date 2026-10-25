"""
OAuth-сервер для Persona Toolbox: один пользователь (Михаил), вход по паролю.

ChatGPT регистрируется сам (Dynamic Client Registration), открывает /login,
Михаил вводит пароль, ChatGPT получает access token (1 ч) и refresh token (30 дней).

Хранилище: JSON-файл (PERSONA_GPT_STATE, по умолчанию D:\\Persona\\chatgpt_oauth.json).
В нём хэш пароля, зарегистрированные клиенты и sha256 от токенов, сами токены не хранятся.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

SCOPE = "persona"
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
CODE_TTL = 300
PENDING_TTL = 600
MAX_FAILS = 5
LOCK_SEC = 15 * 60

# Куда разрешено отдавать код. Чужой клиент с другим redirect_uri не зарегистрируется.
DEFAULT_REDIRECT_HOSTS = "chatgpt.com,chat.openai.com"


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> dict:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return {"salt": salt.hex(), "hash": digest.hex()}


def check_password(password: str, stored: dict) -> bool:
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(stored["salt"]), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), stored["hash"])


class PersonaOAuthProvider:
    def __init__(self, state_file: Path, issuer_url: str):
        self.state_file = state_file
        self.issuer_url = issuer_url.rstrip("/")
        self.redirect_hosts = {
            h.strip().lower()
            for h in os.environ.get("PERSONA_GPT_REDIRECT_HOSTS", DEFAULT_REDIRECT_HOSTS).split(",")
            if h.strip()
        }
        self.state = self._load()
        if not self.state.get("password"):
            raise SystemExit("Пароль не задан: python server_http.py --set-password")
        # Короткоживущее держим в памяти: перезапуск просто попросит войти заново.
        self.pending: dict[str, tuple[float, str, AuthorizationParams]] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.fails = 0
        self.locked_until = 0.0

    # ─── хранилище ─────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self.state_file.exists():
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        else:
            data = {}
        for key in ("clients", "access", "refresh"):
            data.setdefault(key, {})
        return data

    def _save(self) -> None:
        now = time.time()
        for key in ("access", "refresh"):
            self.state[key] = {k: v for k, v in self.state[key].items() if v["expires_at"] > now}
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_file)

    # ─── клиенты ───────────────────────────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = self.state["clients"].get(client_id)
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            host = (urlparse(str(uri)).hostname or "").lower()
            if urlparse(str(uri)).scheme != "https" or host not in self.redirect_hosts:
                raise RegistrationError("invalid_redirect_uri", f"redirect_uri не разрешён: {uri}")
        self.state["clients"][client_info.client_id] = client_info.model_dump(mode="json")
        self._save()

    # ─── авторизация ───────────────────────────────────────────────────────

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
        req_id = secrets.token_urlsafe(24)
        self.pending[req_id] = (now + PENDING_TTL, client.client_id, params)
        return f"{self.issuer_url}/login?req={req_id}"

    def _login_page(self, req_id: str, error: str = "") -> HTMLResponse:
        err = f'<p style="color:#b00">{html.escape(error)}</p>' if error else ""
        body = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Persona</title></head>
<body style="font-family:system-ui;max-width:360px;margin:15vh auto;padding:0 16px">
<h2>Persona Toolbox</h2><p>Доступ ChatGPT к личной базе. Введи пароль.</p>{err}
<form method="post"><input type="hidden" name="req" value="{html.escape(req_id)}">
<input type="password" name="password" autofocus required style="width:100%;padding:8px">
<button style="margin-top:12px;padding:8px 16px">Разрешить</button></form></body></html>"""
        return HTMLResponse(body, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    async def login(self, request: Request) -> Response:
        if request.method == "GET":
            req_id = request.query_params.get("req", "")
            if req_id not in self.pending:
                return HTMLResponse("Запрос устарел, начни подключение в ChatGPT заново.", status_code=400)
            return self._login_page(req_id)

        form = await request.form()
        req_id = str(form.get("req", ""))
        entry = self.pending.get(req_id)
        if not entry or entry[0] < time.time():
            return HTMLResponse("Запрос устарел, начни подключение в ChatGPT заново.", status_code=400)
        if time.time() < self.locked_until:
            return self._login_page(req_id, "Слишком много попыток, подожди 15 минут.")
        if not check_password(str(form.get("password", "")), self.state["password"]):
            self.fails += 1
            if self.fails >= MAX_FAILS:
                self.fails = 0
                self.locked_until = time.time() + LOCK_SEC
            return self._login_page(req_id, "Неверный пароль.")

        self.fails = 0
        del self.pending[req_id]
        _, client_id, params = entry
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [SCOPE],
            expires_at=time.time() + CODE_TTL,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state), 302)

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        code = self.codes.get(authorization_code)
        if not code or code.client_id != client.client_id or code.expires_at < time.time():
            return None
        return code

    # ─── токены ────────────────────────────────────────────────────────────

    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.state["access"][_sha(access)] = {
            "client_id": client_id, "scopes": scopes, "expires_at": now + ACCESS_TTL, "resource": resource,
            "refresh": _sha(refresh),
        }
        self.state["refresh"][_sha(refresh)] = {
            "client_id": client_id, "scopes": scopes, "expires_at": now + REFRESH_TTL, "resource": resource,
        }
        self._save()
        return OAuthToken(
            access_token=access, expires_in=ACCESS_TTL, scope=" ".join(scopes), refresh_token=refresh,
        )

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> OAuthToken:
        if self.codes.pop(authorization_code.code, None) is None:
            raise TokenError("invalid_grant", "код уже использован")
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token: str) -> RefreshToken | None:
        raw = self.state["refresh"].get(_sha(refresh_token))
        if not raw or raw["client_id"] != client.client_id or raw["expires_at"] < time.time():
            return None
        return RefreshToken(token=refresh_token, client_id=raw["client_id"], scopes=raw["scopes"],
                            expires_at=raw["expires_at"])

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        old = self.state["refresh"].pop(_sha(refresh_token.token), None)
        if old is None:
            raise TokenError("invalid_grant", "refresh token отозван")
        # Ротация: старый access, выданный вместе с этим refresh, тоже гасим.
        self.state["access"] = {
            k: v for k, v in self.state["access"].items() if v.get("refresh") != _sha(refresh_token.token)
        }
        return self._issue(client.client_id, scopes or refresh_token.scopes, old.get("resource"))

    async def load_access_token(self, token: str) -> AccessToken | None:
        raw = self.state["access"].get(_sha(token))
        if not raw or raw["expires_at"] < time.time():
            return None
        return AccessToken(token=token, client_id=raw["client_id"], scopes=raw["scopes"],
                           expires_at=raw["expires_at"], resource=raw.get("resource"))

    async def revoke_token(self, token) -> None:
        h = _sha(token.token)
        self.state["access"].pop(h, None)
        self.state["refresh"].pop(h, None)
        self.state["access"] = {k: v for k, v in self.state["access"].items() if v.get("refresh") != h}
        self._save()

    def revoke_all(self) -> None:
        self.state["access"], self.state["refresh"], self.state["clients"] = {}, {}, {}
        self._save()


def set_password(state_file: Path, password: str) -> None:
    data = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    data["password"] = hash_password(password)
    # Новый пароль = все прежние подключения недействительны.
    data["access"], data["refresh"], data["clients"] = {}, {}, {}
    state_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
