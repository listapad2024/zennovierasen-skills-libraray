"""
Persona Toolbox для ChatGPT: тот же MCP-сервер, но по Streamable HTTP и только чтение.

Берёт инструменты из исходного бандла (D:\\Persona\\mcp_persona_bundle\\server\\main.py),
чтобы не дублировать код, и публикует только те, что не пишут в корпус.
Слушает 127.0.0.1, наружу выходит только через туннель (start_chatgpt.ps1).

Защита: секретный ключ в пути, `/<key>/mcp`. ChatGPT умеет только OAuth или
«без авторизации», поэтому ключ в URL и есть авторизация. Ключ лежит в
PERSONA_GPT_KEY_FILE (по умолчанию D:\\Persona\\chatgpt_mcp.key), создаётся при первом запуске.

Запуск: python server_http.py  (env PERSONA_BUNDLE_DIR, PERSONA_GPT_PORT, PERSONA_GPT_KEY_FILE)
"""
from __future__ import annotations

import importlib.util
import os
import secrets
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

BUNDLE_DIR = Path(os.environ.get("PERSONA_BUNDLE_DIR", r"D:\Persona\mcp_persona_bundle"))
PORT = int(os.environ.get("PERSONA_GPT_PORT", "8767"))
KEY_FILE = Path(os.environ.get("PERSONA_GPT_KEY_FILE", r"D:\Persona\chatgpt_mcp.key"))

# Только чтение. Пишущие (persona_remember, persona_save_feedback, persona_record_*)
# сюда не попадают ни при каких настройках.
READ_ONLY_TOOLS = [
    "persona_chat",
    "persona_stats",
    "persona_list_recent",
    "persona_evaluate_vacancy",
    "persona_evaluate_tz",
    "persona_draft_response",
    "persona_analyze",
]


def _load_bundle():
    spec = importlib.util.spec_from_file_location("persona_bundle_main", BUNDLE_DIR / "server" / "main.py")
    if spec is None or spec.loader is None:
        sys.exit(f"Не найден {BUNDLE_DIR / 'server' / 'main.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_key() -> str:
    if KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = secrets.token_urlsafe(32)
    KEY_FILE.write_text(key, encoding="utf-8")
    return key


def build() -> FastMCP:
    bundle = _load_bundle()
    key = _load_key()
    server = FastMCP(
        "Persona Toolbox (read-only)",
        instructions=(
            "Личный RAG Михаила: кейсы, навыки, стиль. Только чтение. "
            "Ответ persona_evaluate_vacancy может содержать личное, работодателю его не пересылать."
        ),
        host="127.0.0.1",
        port=PORT,
        streamable_http_path=f"/{key}/mcp",
        stateless_http=True,
        # Host у туннеля меняется при каждом запуске, а проверка Host не умеет
        # маски поддоменов. Сервер слушает только 127.0.0.1, доступ режет ключ в пути.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    for name in READ_ONLY_TOOLS:
        fn = getattr(bundle, name)
        server.add_tool(fn, name=name, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    return server


if __name__ == "__main__":
    build().run(transport="streamable-http")
