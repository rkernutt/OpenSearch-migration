"""
Optional: load repository-root .env into os.environ when python-dotenv is installed.
Call bootstrap_env.load() at the start of scripts that read secrets from the environment.
"""
from pathlib import Path


def load() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parent
    env_path = root / ".env"
    if env_path.is_file():
        load_dotenv(env_path)
