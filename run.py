"""Dev-запуск сервера: `python run.py [--port 8000] [--no-reload]`.

Зачем отдельный файл: самая частая причина падения на старте — запуск глобальным Python,
где стоит несовместимая версия starlette:

    TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'

Скрипт проверяет окружение и вместо стека Traceback печатает, что делать.
"""
from __future__ import annotations

import argparse
import sys

# fastapi 0.115 требует starlette >=0.37,<0.39
STARLETTE_MIN_MINOR = 37
STARLETTE_MAX_MINOR = 39


def check_environment() -> list[str]:
    """Возвращает список проблем окружения (пустой список — всё хорошо)."""
    try:
        import fastapi
        import starlette
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        return [f"не установлены зависимости ({exc}): pip install -r requirements.txt"]

    problems: list[str] = []
    try:
        major, minor = (int(part) for part in starlette.__version__.split(".")[:2])
    except ValueError:  # pragma: no cover - экзотические версии
        return problems
    if major != 0 or not (STARLETTE_MIN_MINOR <= minor < STARLETTE_MAX_MINOR):
        problems.append(
            f"starlette {starlette.__version__} несовместим с fastapi {fastapi.__version__} "
            f"(нужен starlette 0.37–0.38). Скорее всего запускается глобальный Python, а не .venv."
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Запуск Keitaro Campaign Manager (dev)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true", help="без автоперезагрузки")
    args = parser.parse_args()

    problems = check_environment()
    if problems:
        print("Запуск отменён:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nРешение:\n"
            "  Windows:  .venv\\Scripts\\activate  (и затем: python run.py)\n"
            "  Linux/macOS: source .venv/bin/activate\n"
            "  или сразу интерпретатором venv:\n"
            "  .venv\\Scripts\\python.exe -m uvicorn app.main:app --reload --port 8000",
            file=sys.stderr,
        )
        return 1

    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=not args.no_reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
