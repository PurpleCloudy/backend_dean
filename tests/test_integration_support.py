"""Local configuration shared by the opt-in real-service checks."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def settings():
    local = Path(os.environ.get('TEST_ENV_FILE', str(ROOT / '.env.local')))
    values = dict(line.split('=', 1) for line in local.read_text(encoding='utf8').splitlines()
                  if line and not line.startswith('#') and '=' in line) if local.exists() else {}
    return {**values, **os.environ}


def output_directory(env):
    path = Path(env.get('TEST_RESULTS_DIR', str(ROOT / '.local/test-results')))
    path.mkdir(parents=True, exist_ok=True)
    return path


def compose_arguments(env):
    files = json.loads(env.get('TEST_COMPOSE_FILES', '["compose.yaml","compose.verification.yaml"]'))
    assert isinstance(files, list) and files and all(isinstance(p, str) for p in files)
    return ['compose', '--env-file', env.get('TEST_ENV_FILE', '.env.local'),
            *(value for path in files for value in ('-f', path))]
