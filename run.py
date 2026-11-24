"""Prepare and run the local Keitaro workspace."""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import venv

ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info < (3, 11):
        raise SystemExit("Нужен Python 3.11 или новее.")

    parser = argparse.ArgumentParser(description="Запустить локальную панель Keitaro")
    parser.add_argument("--port", type=int, default=8000, help="Порт приложения")
    parser.add_argument("--demo", action="store_true", help="Показать приложение с примерами без API-ключа")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("порт должен быть от 1 до 65535")

    environment = ROOT / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        print("Создаём окружение Python…", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)

    child_env = os.environ.copy()

    def run(*arguments):
        subprocess.run([str(python), *arguments], cwd=ROOT, env=child_env, check=True)

    requirements = ROOT / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = environment / ".requirements.sha256"
    if not stamp.is_file() or stamp.read_text() != digest:
        print("Устанавливаем зависимости…", flush=True)
        run("-m", "pip", "install", "-r", str(requirements))
        stamp.write_text(digest)

    def serve():
        print("Проверяем миграции SQLite…", flush=True)
        run("-m", "alembic", "upgrade", "head")
        print(f"Откройте http://127.0.0.1:{args.port}", flush=True)
        run("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port))

    if args.demo:
        with TemporaryDirectory(prefix="keitaro-demo-") as demo:
            child_env.update(
                KEITARO_DEMO="1",
                CAMPAIGN_PREFIX="test_demo_",
                DATABASE_URL=f"sqlite:///{(Path(demo) / 'demo.db').as_posix()}",
            )
            print("Демо: используется временная база и пример данных.", flush=True)
            serve()
    else:
        serve()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
