import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cardgen import cli
from cardgen.backends import (
    GenerationError,
    OpenAICompatBackend,
    make_backend,
    parse_json_object,
    pick_provider,
)
from cardgen.llm import generate_card
from cardgen.product import Product


class Script:
    def __init__(self):
        self.responses = []
        self.requests = []
        self.url = ""


@pytest.fixture
def server():
    script = Script()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(size) or b"{}")
            script.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
            if script.responses:
                status, headers, payload = script.responses.pop(0)
            else:
                status, headers, payload = 500, {}, {"error": {"message": "нет сценария"}}
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    script.url = f"http://127.0.0.1:{srv.server_port}/v1"
    yield script
    srv.shutdown()
    srv.server_close()


def ok(content, finish="stop"):
    return 200, {}, {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


CARD = {
    "seo_title": "Термопреобразователь ОВЕН",
    "seo_description": "Слово " * 160,
    "search_queries": ["запрос а", "запрос б", "запрос в"],
    "commercial": {"suitable_for": ["a"], "tasks_solved": ["b"], "manager_notes": "c"},
    "missing_data": ["диапазон"],
}


def make(server, sleeps=None, retries=2):
    sleeps = sleeps if sleeps is not None else []
    return OpenAICompatBackend("t", server.url, "secret-key", "m", max_retries=retries, sleep=sleeps.append)


# ---------------------------------------------------------------- парсинг JSON

def test_parse_plain_fenced_and_think():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('<think>рассуждаю {x}</think>\n{"a": 1}') == {"a": 1}
    assert parse_json_object('Вот ответ: {"a": 1} Готово.') == {"a": 1}


@pytest.mark.parametrize("text", ["", "без json", "{сломан", "[1, 2]"])
def test_parse_garbage_raises(text):
    with pytest.raises(GenerationError):
        parse_json_object(text)


# ---------------------------------------------------------------- HTTP-бэкенд

def test_success_request_shape(server):
    server.responses = [ok(json.dumps(CARD))]
    result = make(server).complete("привет")
    assert result["seo_title"] == CARD["seo_title"]
    req = server.requests[0]
    assert req["path"] == "/v1/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer secret-key"
    assert req["headers"]["User-Agent"] == "cardgen/1.0"
    assert req["body"]["response_format"] == {"type": "json_object"}
    assert req["body"]["messages"][0]["role"] == "system"
    assert "JSON" in req["body"]["messages"][0]["content"]
    assert req["body"]["messages"][1]["content"] == "привет"


def test_fenced_answer_is_accepted(server):
    server.responses = [ok("```json\n" + json.dumps(CARD) + "\n```")]
    assert make(server).complete("x")["seo_title"]


def test_429_retried_with_retry_after(server):
    sleeps = []
    server.responses = [(429, {"Retry-After": "3"}, {"error": {"message": "slow"}}), ok(json.dumps(CARD))]
    assert make(server, sleeps).complete("x")
    assert sleeps == [3.0]


def test_429_without_retry_after_waits_long(server):
    sleeps = []
    server.responses = [(429, {}, {"error": {"message": "slow"}})] * 2 + [ok(json.dumps(CARD))]
    assert make(server, sleeps).complete("x")
    assert sleeps == [10.0, 20.0]  # лимиты бесплатных тарифов действуют минутами


def test_error_inside_200_body_is_retried(server):
    sleeps = []
    server.responses = [(200, {}, {"error": {"message": "Provider returned error", "code": 429}}), ok(json.dumps(CARD))]
    assert make(server, sleeps).complete("x")
    assert len(server.requests) == 2 and sleeps


def test_error_inside_200_body_gives_readable_message(server):
    server.responses = [(200, {}, {"error": {"message": "upstream exploded"}})] * 3
    with pytest.raises(GenerationError, match="upstream exploded"):
        make(server).complete("x")


def test_429_gives_up_with_friendly_message(server):
    server.responses = [(429, {}, {"error": {"message": "slow"}})] * 3
    with pytest.raises(GenerationError, match="лимит"):
        make(server).complete("x")
    assert len(server.requests) == 3


def test_5xx_retried(server):
    server.responses = [(503, {}, {"error": {"message": "down"}}), ok(json.dumps(CARD))]
    assert make(server).complete("x")


@pytest.mark.parametrize(
    "status,expected",
    [(401, "ключ"), (403, "403"), (404, "не найдены"), (418, "418")],
)
def test_error_statuses_are_friendly(server, status, expected):
    server.responses = [(status, {}, {"error": {"message": "boom"}})]
    with pytest.raises(GenerationError, match=expected):
        make(server).complete("x")


def test_400_retries_without_response_format(server):
    server.responses = [(400, {}, {"error": {"message": "response_format unsupported"}}), ok(json.dumps(CARD))]
    assert make(server).complete("x")
    assert "response_format" in server.requests[0]["body"]
    assert "response_format" not in server.requests[1]["body"]


def test_400_twice_fails(server):
    server.responses = [(400, {}, {"error": {"message": "плохо"}})] * 2
    with pytest.raises(GenerationError, match="400"):
        make(server).complete("x")


def test_truncated_answer(server):
    server.responses = [ok('{"a": 1', finish="length")]
    with pytest.raises(GenerationError, match="обрезан"):
        make(server).complete("x")


def test_empty_and_odd_structure(server):
    server.responses = [ok(""), (200, {}, {"unexpected": True})]
    b = make(server)
    with pytest.raises(GenerationError, match="пустой"):
        b.complete("x")
    with pytest.raises(GenerationError, match="структуры"):
        b.complete("x")


def test_connection_refused_is_friendly():
    b = OpenAICompatBackend("t", "http://127.0.0.1:1/v1", "k", "m", max_retries=1, sleep=lambda s: None)
    with pytest.raises(GenerationError, match="соединения"):
        b.complete("x")


def test_full_generation_through_http(server):
    server.responses = [ok(json.dumps(CARD))]
    product = Product(name="Термопреобразователь", manufacturer="ОВЕН")
    card = generate_card(make(server), product, log=lambda m: None)
    assert card["warnings"] == []
    assert card["model"] == "t:m"


# ---------------------------------------------------------------- выбор провайдера

def test_auto_picks_provider_by_key():
    assert pick_provider("auto", {"GROQ_API_KEY": "k"}) == "groq"
    assert pick_provider("auto", {"OPENROUTER_API_KEY": "k"}) == "openrouter"
    assert pick_provider("auto", {"GROQ_API_KEY": "k", "CARDGEN_BASE_URL": "http://x/v1"}) == "custom"


def test_no_keys_message_points_to_free_option():
    with pytest.raises(GenerationError, match="GROQ_API_KEY"):
        pick_provider("auto", {})


def test_unknown_provider():
    with pytest.raises(GenerationError, match="Неизвестный"):
        pick_provider("nope", {})


def test_make_backend_defaults_and_override():
    b = make_backend("auto", None, {"GROQ_API_KEY": "k"})
    assert (b.name, b.model) == ("groq", "openai/gpt-oss-120b")
    b = make_backend("auto", None, {"GROQ_API_KEY": "k", "CARDGEN_MODEL": "other"})
    assert b.model == "other"
    b = make_backend("groq", "cli-model", {"GROQ_API_KEY": "k", "CARDGEN_MODEL": "other"})
    assert b.model == "cli-model"


def test_openrouter_default_model_gets_fallbacks_and_custom_model_does_not():
    b = make_backend("openrouter", None, {"OPENROUTER_API_KEY": "k"})
    models = b._extra_body["models"]
    assert models[0] == b.model
    assert 1 < len(models) <= 3  # OpenRouter отклоняет списки длиннее трёх моделей
    assert b._extra_body["reasoning"] == {"enabled": False}
    b = make_backend("openrouter", "my/model:free", {"OPENROUTER_API_KEY": "k"})
    assert "models" not in b._extra_body
    assert b._extra_body["reasoning"] == {"enabled": False}


def test_extra_body_sent_and_used_model_recorded(server):
    reply = {"model": "other/model:free", "choices": [{"message": {"content": json.dumps(CARD)}, "finish_reason": "stop"}]}
    server.responses = [(200, {}, reply)]
    b = OpenAICompatBackend("t", server.url, "k", "primary:free", extra_body={"models": ["primary:free", "other/model:free"]})
    b.complete("x")
    assert server.requests[0]["body"]["models"] == ["primary:free", "other/model:free"]
    assert b.used_model == "other/model:free"


def test_missing_key_for_explicit_provider():
    with pytest.raises(GenerationError, match="OPENROUTER_API_KEY"):
        make_backend("openrouter", None, {})


def test_custom_requires_model_and_allows_no_key():
    with pytest.raises(GenerationError, match="модель"):
        make_backend("custom", None, {"CARDGEN_BASE_URL": "http://localhost:11434/v1"})
    b = make_backend("custom", None, {"CARDGEN_BASE_URL": "http://localhost:11434/v1", "CARDGEN_MODEL": "llama3"})
    assert b.name == "custom"


def test_anthropic_without_package_is_friendly(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # импорт упадёт с ImportError
    with pytest.raises(GenerationError, match="pip install anthropic"):
        make_backend("anthropic", None, {"ANTHROPIC_API_KEY": "k"})


# ---------------------------------------------------------------- CLI целиком

def test_cli_end_to_end(server, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for var in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "CARDGEN_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CARDGEN_BASE_URL", server.url)
    monkeypatch.setenv("CARDGEN_MODEL", "fake-model")
    server.responses = [ok(json.dumps(CARD)), ok(json.dumps(CARD))]

    src = tmp_path / "in.json"
    src.write_text(
        json.dumps([{"name": "Датчик А", "manufacturer": "ОВЕН"}, {"name": ""}, {"name": "Датчик Б"}], ensure_ascii=False),
        encoding="utf-8",
    )
    code = cli.main([str(src), "-o", str(tmp_path / "out"), "--quiet"])

    assert code == 1  # один товар пропущен из-за пустого name
    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert files == ["01_Датчик-А.json", "01_Датчик-А.md", "03_Датчик-Б.json", "03_Датчик-Б.md"]
    md = (tmp_path / "out" / "01_Датчик-А.md").read_text(encoding="utf-8")
    assert "## Ключевые характеристики" in md and "данных нет" in md
    err = capsys.readouterr().err
    assert "пропущен" in err and "custom" in err


def test_cli_without_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for var in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "CARDGEN_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    src = tmp_path / "in.json"
    src.write_text('{"name": "A"}', encoding="utf-8")
    assert cli.main([str(src)]) == 2
    assert "GROQ_API_KEY" in capsys.readouterr().err
