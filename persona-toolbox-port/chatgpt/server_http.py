"""
Persona Toolbox для ChatGPT: тот же MCP-сервер, но по Streamable HTTP и с OAuth.

Берёт все 12 инструментов из исходного бандла (D:\\Persona\\mcp_persona_bundle\\server\\main.py),
чтобы не дублировать код. Читающие помечены readOnlyHint, пишущие нет, поэтому
ChatGPT спрашивает подтверждение перед каждой записью в корпус.
Слушает 127.0.0.1, наружу выходит только через туннель (start_chatgpt.ps1).

Вход: OAuth 2.1 + PKCE, Dynamic Client Registration, один пароль Михаила (oauth_provider.py).

  python server_http.py --set-password   задать или сменить пароль (гасит все подключения)
  python server_http.py --revoke-all     отключить ChatGPT, пароль остаётся
  python server_http.py                  запуск, нужен PERSONA_PUBLIC_URL (https-адрес туннеля)

env: PERSONA_PUBLIC_URL, PERSONA_BUNDLE_DIR, PERSONA_GPT_PORT, PERSONA_GPT_STATE
"""
from __future__ import annotations

import getpass
import importlib.util
import os
import sys
from pathlib import Path

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oauth_provider import SCOPE, PersonaOAuthProvider, set_password  # noqa: E402

BUNDLE_DIR = Path(os.environ.get("PERSONA_BUNDLE_DIR", r"D:\Persona\mcp_persona_bundle"))
PORT = int(os.environ.get("PERSONA_GPT_PORT", "8767"))
STATE_FILE = Path(os.environ.get("PERSONA_GPT_STATE", r"D:\Persona\chatgpt_oauth.json"))

READ_TOOLS = [
    "persona_chat",
    "persona_stats",
    "persona_list_recent",
    "persona_evaluate_vacancy",
    "persona_evaluate_tz",
    "persona_draft_response",
    "persona_analyze",
]
# Пишут в корпус. record_pass ещё и очищает rag-feed/kwork-current-pass/.
WRITE_TOOLS = {
    "persona_remember": False,
    "persona_save_feedback": False,
    "persona_record_response": False,
    "persona_record_session_note": False,
    "persona_record_pass": True,
}


def _load_bundle():
    spec = importlib.util.spec_from_file_location("persona_bundle_main", BUNDLE_DIR / "server" / "main.py")
    if spec is None or spec.loader is None or not (BUNDLE_DIR / "server" / "main.py").exists():
        sys.exit(f"Не найден {BUNDLE_DIR / 'server' / 'main.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(public_url: str) -> FastMCP:
    public_url = public_url.rstrip("/")
    if not public_url.startswith("https://"):
        sys.exit("PERSONA_PUBLIC_URL должен быть https-адресом туннеля")
    bundle = _load_bundle()
    provider = PersonaOAuthProvider(STATE_FILE, public_url)
    server = FastMCP(
        "Persona Toolbox",
        instructions=(
            "Личный RAG Михаила: кейсы, навыки, стиль. Инструменты записи меняют корпус, "
            "вызывай их только когда Михаил прямо попросил. Ответ persona_evaluate_vacancy "
            "может содержать личное, работодателю его не пересылать."
        ),
        host="127.0.0.1",
        port=PORT,
        stateless_http=True,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=f"{public_url}/mcp",
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE],
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
        # Перед сервером туннель со своим Host, а проверка Host не умеет маски.
        # Сервер слушает только 127.0.0.1, доступ режет OAuth.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    server.custom_route("/login", methods=["GET", "POST"])(provider.login)

    for name in READ_TOOLS:
        server.add_tool(getattr(bundle, name), name=name,
                        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    for name, destructive in WRITE_TOOLS.items():
        server.add_tool(getattr(bundle, name), name=name,
                        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=destructive,
                                                    openWorldHint=False))
    return server


def main() -> None:
    if "--set-password" in sys.argv:
        pw = getpass.getpass("Новый пароль для входа ChatGPT: ")
        if len(pw) < 12 or pw != getpass.getpass("Ещё раз: "):
            sys.exit("Пароли не совпали или короче 12 символов")
        set_password(STATE_FILE, pw)
        print(f"Пароль сохранён в {STATE_FILE}, прежние подключения отозваны.")
        return
    if "--revoke-all" in sys.argv:
        PersonaOAuthProvider(STATE_FILE, "https://unused").revoke_all()
        print("Все подключения ChatGPT отозваны.")
        return
    build(os.environ.get("PERSONA_PUBLIC_URL", "")).run(transport="streamable-http")


if __name__ == "__main__":
    main()
