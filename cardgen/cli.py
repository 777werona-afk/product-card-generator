"""Командная строка: python -m cardgen examples/products.json"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .backends import PROVIDERS, GenerationError, make_backend
from .llm import generate_card
from .product import InputError, build_product, parse_json
from .render import to_markdown


def load_env_file() -> None:
    """Читает .env (из текущей папки и из корня проекта). Уже заданные переменные не трогает."""
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value


def _slug(text: str) -> str:
    slug = re.sub(r"[^\w.-]+", "-", text, flags=re.UNICODE).strip("-")
    return slug[:60] or "product"


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise InputError(f"Файл не найден: {path}")
    except UnicodeDecodeError:
        raise InputError(f"Файл {path} должен быть в кодировке UTF-8.")
    except OSError as e:
        raise InputError(f"Не удалось прочитать {path}: {e.strerror}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cardgen",
        description="Черновик карточки товара для B2B-магазина КИП по данным в JSON.",
    )
    p.add_argument("input", help="JSON-файл с товаром или списком товаров, «-» — читать из stdin")
    p.add_argument("-o", "--out-dir", default="out", help="куда сохранить результаты (по умолчанию out/)")
    p.add_argument("--format", choices=["md", "json", "both"], default="both", help="формат файлов результата")
    p.add_argument(
        "--provider",
        choices=["auto", *PROVIDERS],
        default=os.environ.get("CARDGEN_PROVIDER", "auto"),
        help="провайдер LLM; auto — по тому, какой ключ задан (по умолчанию auto)",
    )
    p.add_argument("--model", default=None, help="модель (по умолчанию CARDGEN_MODEL или модель провайдера)")
    p.add_argument("--max-attempts", type=int, default=3, help="сколько раз просить модель исправить ошибки")
    p.add_argument("--dry-run", action="store_true", help="только проверить вход и показать, что уйдёт в модель")
    p.add_argument("--quiet", action="store_true", help="не печатать карточки в stdout")
    return p


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    args = build_parser().parse_args(argv)

    try:
        raw_items = parse_json(_read_input(args.input))
    except InputError as e:
        print(f"Ошибка входных данных: {e}", file=sys.stderr)
        return 2

    backend = None
    if not args.dry_run:
        try:
            backend = make_backend(args.provider, args.model, os.environ)
        except GenerationError as e:
            print(f"Ошибка настройки: {e}", file=sys.stderr)
            return 2
        print(f"Провайдер: {backend.name}, модель: {backend.model}", file=sys.stderr)

    out_dir = Path(args.out_dir)
    failed = 0

    for index, raw in enumerate(raw_items, start=1):
        label = f"[{index}/{len(raw_items)}]"
        try:
            product = build_product(raw)
        except InputError as e:
            print(f"{label} пропущен: {e}", file=sys.stderr)
            failed += 1
            continue

        print(f"{label} {product.name}", file=sys.stderr)
        if args.dry_run:
            print(json.dumps(product.llm_payload(), ensure_ascii=False, indent=2))
            continue

        try:
            card = generate_card(backend, product, max_attempts=args.max_attempts)
        except GenerationError as e:
            print(f"{label} ошибка: {e}", file=sys.stderr)
            failed += 1
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        base = out_dir / f"{index:02d}_{_slug(product.name)}"
        markdown = to_markdown(card)
        if args.format in ("json", "both"):
            base.with_suffix(".json").write_text(
                json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        if args.format in ("md", "both"):
            base.with_suffix(".md").write_text(markdown, encoding="utf-8")
        if not args.quiet:
            print(markdown)
        if card["warnings"]:
            print(f"{label} есть предупреждения, смотрите раздел «Требует ручной проверки»", file=sys.stderr)

    if not args.dry_run and failed < len(raw_items):
        print(f"Готово. Файлы в {out_dir}/", file=sys.stderr)
    return 1 if failed else 0
