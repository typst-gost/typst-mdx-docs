#!/usr/bin/env python3
"""
Скрипт инкрементального перевода документации (.mdx и meta.json) на русский язык.

Особенности:
- Считывает токен Polza API из .env файла в корне проекта.
- system_prompt.txt и translated.json лежат РЯДОМ со скриптом (SCRIPT_DIR).
- Исходники по умолчанию: <project_root>/build/mdx.
- Результат по умолчанию: <project_root>/build/translated_ru.
- Вызывает GET /v2/balance до начала и после завершения работы для расчёта стоимости.
- Модель: deepseek/deepseek-v4.1-flash-1017 (reasoning: {effort: "none"}).
- Рассчитывает SHA-256 хеши исходников и сохраняет их в translated.json рядом со скриптом.
- Защищает структуру meta.json (восстанавливает массив pages без изменений).
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from openai import APIStatusError, APITimeoutError, AsyncOpenAI

# Папка, где физически лежит текущий скрипт
SCRIPT_DIR = Path(__file__).resolve().parent

MODEL_ID = "deepseek/deepseek-v4.1-flash-1017"
API_BASE_URL = "https://polza.ai/api/v1"
BALANCE_V2_URL = "https://polza.ai/api/v2/balance"

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".turbo",
    ".astro",
}


def load_env_file(env_path: Path) -> dict[str, str]:
    """Парсит .env файл без сторонних зависимостей."""
    loaded: dict[str, str] = {}
    if not env_path.is_file():
        return loaded

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and (
            (val.startswith('"') and val.endswith('"'))
            or (val.startswith("'") and val.endswith("'"))
        ):
            val = val[1:-1]
        loaded[key] = val
        if key not in os.environ:
            os.environ[key] = val
    return loaded


def resolve_api_key(project_root: Path) -> str:
    """Ищет ключ Polza API в .env корня проекта или переменных окружения."""
    # Загружаем .env из корня проекта (и как запасной вариант — рядом со скриптом)
    load_env_file(project_root / ".env")
    if SCRIPT_DIR != project_root:
        load_env_file(SCRIPT_DIR / ".env")

    for env_var in ("POLZA_API_KEY", "POLZA_AI_API_KEY", "POLZA_KEY"):
        val = os.environ.get(env_var, "").strip()
        if val:
            return val
    return ""


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_translated_index(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        print(f"[WARN] Не удалось прочитать {cache_path}: {exc}. Создаём новый индекс.")
    return {}


def save_translated_index(cache_path: Path, index: dict[str, Any]) -> None:
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, cache_path)


def get_stored_hash(index: dict[str, Any], rel_key: str) -> str | None:
    entry = index.get(rel_key)
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("hash")
    return None


async def fetch_balance_v2(http: httpx.AsyncClient, api_key: str) -> dict[str, Any]:
    resp = await http.get(
        BALANCE_V2_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def find_doc_files(source_dir: Path, excluded_dirs: set[str]) -> list[Path]:
    """Ищет файлы .mdx и meta.json внутри source_dir."""
    files_list: list[Path] = []
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = sorted(d for d in dirs if d not in excluded_dirs)
        for name in sorted(files):
            lower = name.lower()
            if lower.endswith(".mdx") or lower == "meta.json":
                files_list.append(Path(root) / name)
    return files_list


def sanitize_mdx(source_text: str, translated_text: str, rel_path: str) -> str:
    text = translated_text.strip()

    # Снимаем случайную обёртку ```mdx / ```markdown ... ```
    if not source_text.lstrip().startswith("```"):
        fence_match = re.match(
            r"^```(?:mdx|markdown|md)?\s*\n(.*?)\n```\s*$",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if fence_match:
            text = fence_match.group(1).strip()

    if source_text.lstrip().startswith("---") and not text.startswith("---"):
        print(f"[WARN] {rel_path}: потерян открывающий YAML frontmatter `---`")

    src_imports = re.findall(r"^import\s+.*$", source_text, flags=re.MULTILINE)
    dst_imports = re.findall(r"^import\s+.*$", text, flags=re.MULTILINE)
    if src_imports != dst_imports:
        print(
            f"[WARN] {rel_path}: строки `import` изменились "
            f"(в оригинале {len(src_imports)}, в переводе {len(dst_imports)})"
        )

    return text + "\n"


def process_meta_json(source_text: str, translated_text: str, rel_path: str) -> str:
    """Проверяет валидность JSON и восстанавливает оригинальный массив pages."""
    text = translated_text.strip()
    fence_match = re.match(
        r"^```(?:json)?\s*\n(.*?)\n```\s*$",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        src_obj = json.loads(source_text)
    except Exception as exc:
        print(f"[ERR] Исходный {rel_path} не является валидным JSON: {exc}")
        return source_text

    try:
        dst_obj = json.loads(text)
    except Exception as exc:
        print(f"[WARN] Модель вернула невалидный JSON для {rel_path}: {exc}. Восстанавливаем.")
        dst_obj = dict(src_obj)

    # Гарантируем 100% сохранность массива pages
    if "pages" in src_obj:
        dst_obj["pages"] = src_obj["pages"]

    # Сохраняем все оригинальные ключи, если модель случайно их опустила
    for key, val in src_obj.items():
        if key not in dst_obj:
            dst_obj[key] = val

    return json.dumps(dst_obj, ensure_ascii=False, indent=2) + "\n"


async def translate_one_file(
    client: AsyncOpenAI,
    system_prompt: str,
    source_path: Path,
    source_dir: Path,
    output_root: Path,
    cache_path: Path,
    index: dict[str, Any],
    index_lock: asyncio.Lock,
    sem: asyncio.Semaphore,
    stats: dict[str, Any],
    max_retries: int = 6,
) -> None:
    rel_path = source_path.relative_to(source_dir).as_posix()
    output_path = output_root / rel_path
    source_text = source_path.read_text(encoding="utf-8")
    file_hash = compute_sha256(source_path)
    is_json = source_path.name.lower() == "meta.json"

    user_content = (
        f"Файл: {source_path.name}\n"
        f"Тип: {'meta.json (навигация)' if is_json else 'MDX документация'}\n\n"
        f"{source_text}"
    )

    call_kwargs: dict[str, Any] = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "extra_body": {"reasoning": {"effort": "none"}},
    }

    if is_json:
        call_kwargs["response_format"] = {"type": "json_object"}

    async with sem:
        for attempt in range(max_retries):
            try:
                resp = await client.chat.completions.create(**call_kwargs)
                break
            except APITimeoutError:
                if attempt < max_retries - 1:
                    delay = min(30, 2 ** attempt)
                    print(f"[RETRY] {rel_path}: таймаут, повтор через {delay} с...")
                    await asyncio.sleep(delay)
                    continue
                raise
            except APIStatusError as exc:
                status = exc.status_code
                retryable = status in (408, 429, 500, 502, 503)
                if status == 402 and "резерв" in str(exc).lower():
                    retryable = True

                if retryable and attempt < max_retries - 1:
                    delay = min(60, 2 ** attempt)
                    if exc.response is not None:
                        ra = exc.response.headers.get("Retry-After")
                        if ra and ra.isdigit():
                            delay = max(int(ra), delay)
                    print(f"[RETRY] {rel_path}: HTTP {status}, повтор через {delay} с...")
                    await asyncio.sleep(delay)
                    continue
                raise

        content = resp.choices[0].message.content or ""
        if not content.strip():
            raise RuntimeError(f"Пустой ответ модели для {rel_path}")

        if is_json:
            final_text = process_meta_json(source_text, content, rel_path)
        else:
            final_text = sanitize_mdx(source_text, content, rel_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_text, encoding="utf-8")

        cost_rub = Decimal("0")
        if resp.usage and resp.usage.model_extra:
            raw_cost = resp.usage.model_extra.get("cost_rub", 0)
            cost_rub = Decimal(str(raw_cost or 0))

        async with index_lock:
            index[rel_path] = {
                "hash": file_hash,
                "type": "meta.json" if is_json else "mdx",
                "model": MODEL_ID,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "cost_rub": str(cost_rub),
            }
            save_translated_index(cache_path, index)
            stats["ok"] += 1
            stats["cost_rub"] += cost_rub
            done = stats["ok"] + stats["failed"]
            total = stats["total"]
            print(f"[OK {done}/{total}] {rel_path} ({cost_rub:.4f} ₽)")


async def async_main() -> int:
    parser = argparse.ArgumentParser(
        description="Инкрементальный перевод документации (MDX + meta.json) через Polza.ai"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Корень проекта (по умолчанию текущая рабочая папка, откуда ищется .env и build/)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Папка с исходной документацией (по умолчанию <root>/build/mdx)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Папка для переведённых файлов (по умолчанию <root>/build/translated_ru)",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=None,
        help="Путь к системному промпту (по умолчанию лежит рядом со скриптом: system_prompt.txt)",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Путь к JSON-файлу с хешами (по умолчанию лежит рядом со скриптом: translated.json)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Количество параллельных запросов (по умолчанию 5)",
    )
    args = parser.parse_args()

    project_root = args.root.resolve()

    # 1. Считываем API-ключ из .env в корне проекта
    api_key = resolve_api_key(project_root)
    if not api_key:
        print(
            f"Ошибка: API-ключ не найден. Задайте POLZA_API_KEY в {project_root / '.env'} "
            f"или в переменных окружения системы.",
            file=sys.stderr,
        )
        return 1

    # 2. Пути по умолчанию:
    # prompt и cache лежат РЯДОМ СО СКРИПТОМ (SCRIPT_DIR)
    prompt_path = (args.prompt if args.prompt else (SCRIPT_DIR / "system_prompt.txt")).resolve()
    cache_path = (args.cache if args.cache else (SCRIPT_DIR / "translated.json")).resolve()

    # source и output отталкиваются от project_root
    source_dir = (args.source or (project_root / "build" / "mdx")).resolve()
    output_root = (args.output or (project_root / "build" / "translated_ru")).resolve()

    if not source_dir.exists():
        print(f"Ошибка: директория с документацией не найдена: {source_dir}", file=sys.stderr)
        return 1
    if not prompt_path.exists():
        print(f"Ошибка: файл системного промпта не найден: {prompt_path}", file=sys.stderr)
        return 1

    system_prompt = prompt_path.read_text(encoding="utf-8").strip()
    index = load_translated_index(cache_path)

    all_files = find_doc_files(source_dir, DEFAULT_EXCLUDED_DIRS)
    if not all_files:
        print(f"В директории {source_dir} не найдено .mdx или meta.json файлов.")
        return 0

    to_translate: list[Path] = []
    skipped = 0
    for path in all_files:
        rel_key = path.relative_to(source_dir).as_posix()
        current_hash = compute_sha256(path)
        stored_hash = get_stored_hash(index, rel_key)
        target_file = output_root / rel_key

        if stored_hash == current_hash and target_file.exists():
            skipped += 1
        else:
            to_translate.append(path)

    print(f"Скрипт расположен в:   {SCRIPT_DIR}")
    print(f"Корень проекта:        {project_root}")
    print(f"Индекс хешей:          {cache_path} (рядом со скриптом)")
    print(f"Системный промпт:      {prompt_path} (рядом со скриптом)")
    print(f"Папка-источник:        {source_dir}")
    print(f"Выходная папка:        {output_root}")
    print(f"Всего файлов:          {len(all_files)}")
    print(f"Пропущено (актуально): {skipped}")
    print(f"Требуют перевода:      {len(to_translate)}")

    if not to_translate:
        print("\nВсе файлы актуальны. Работа завершена.")
        return 0

    async with httpx.AsyncClient() as http:
        # 1. Замер баланса ДО генерации
        balance_before = await fetch_balance_v2(http, api_key)
        amount_before = Decimal(str(balance_before["amount"]))
        available_before = Decimal(str(balance_before["available"]))
        spent_before = (
            Decimal(str(balance_before["spentAmount"]))
            if "spentAmount" in balance_before
            else None
        )

        print("\n" + "-" * 55)
        print(
            f"Баланс ДО перевода (GET /v2/balance):\n"
            f"  Общий (amount):    {amount_before:.4f} ₽\n"
            f"  Доступно:          {available_before:.4f} ₽"
        )
        if spent_before is not None:
            print(f"  Потрачено ранее:   {spent_before:.4f} ₽")
        print(f"Модель:              {MODEL_ID}")
        print("-" * 55 + "\n")

        client = AsyncOpenAI(
            base_url=API_BASE_URL,
            api_key=api_key,
            timeout=600.0,
        )

        sem = asyncio.Semaphore(max(1, args.concurrency))
        index_lock = asyncio.Lock()
        stats: dict[str, Any] = {
            "total": len(to_translate),
            "ok": 0,
            "failed": 0,
            "cost_rub": Decimal("0"),
        }

        async def run_safe(p: Path) -> None:
            rel = p.relative_to(source_dir).as_posix()
            try:
                await translate_one_file(
                    client=client,
                    system_prompt=system_prompt,
                    source_path=p,
                    source_dir=source_dir,
                    output_root=output_root,
                    cache_path=cache_path,
                    index=index,
                    index_lock=index_lock,
                    sem=sem,
                    stats=stats,
                )
            except Exception as exc:
                async with index_lock:
                    stats["failed"] += 1
                print(f"[ERR] {rel}: {exc}", file=sys.stderr)

        await asyncio.gather(*(run_safe(p) for p in to_translate))
        await client.close()

        # Ответ /v2/balance кэшируется на 1 сек — выжидаем 2 секунды для сброса резервов
        await asyncio.sleep(2.0)

        # 2. Замер баланса ПОСЛЕ генерации
        balance_after = await fetch_balance_v2(http, api_key)
        amount_after = Decimal(str(balance_after["amount"]))
        available_after = Decimal(str(balance_after["available"]))
        spent_after = (
            Decimal(str(balance_after["spentAmount"]))
            if "spentAmount" in balance_after
            else None
        )

        delta_balance = amount_before - amount_after

        print("\n" + "=" * 55)
        print("ИТОГИ ПЕРЕВОДА")
        print("=" * 55)
        print(f"Успешно обработано: {stats['ok']} из {stats['total']}")
        print(f"Ошибок:             {stats['failed']}")
        print(f"Выходной каталог:   {output_root}")
        print(f"Индекс хешей:       {cache_path}")
        print("-" * 55)
        print(f"Баланс ДО:          {amount_before:.6f} ₽")
        print(f"Баланс ПОСЛЕ:       {amount_after:.6f} ₽")
        print(f"Списано с баланса:  {delta_balance:.6f} ₽ (по GET /v2/balance)")
        if spent_before is not None and spent_after is not None:
            print(f"Прирост spentAmount:{spent_after - spent_before:.6f} ₽")
        print(f"Сумма usage.cost_rub: {stats['cost_rub']:.6f} ₽")
        print("=" * 55)

        return 1 if stats["failed"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))