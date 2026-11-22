"""The Kamal pre-deploy hook, run for real against a fake ssh and a fake docker.

ssh joins its command arguments with spaces and the server's shell parses the result again, so an argument that was
quoted correctly here (SQL such as `count(*)`) is a syntax error over there unless the hook quotes it for that shell.
The fake ssh below does exactly what a server does, `bash -c "$*"`, so a quoting regression fails here instead of
aborting the first real deploy.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".kamal" / "hooks" / "pre-deploy"

FAKE_SSH = """#!/usr/bin/env bash
# Drop ssh options and the host, then run the joined command string through a shell, as the server would.
while [[ $1 == -* ]]; do [[ $1 == -o ]] && shift; shift; done; shift
exec bash -c "$*"
"""

FAKE_DOCKER = """#!/usr/bin/env bash
# Record each call's arguments exactly as received, then answer the hook's two SQL probes.
{ printf '%s\\n' "$@"; echo '---'; } >> "$DOCKER_LOG"
case "$*" in
  *procrastinate_jobs*) echo t ;;
  *"FROM media"*) echo 42 ;;
esac
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run_hook(tmp_path: Path, hook: Path = HOOK, **env: str) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("ssh", FAKE_SSH), ("docker", FAKE_DOCKER)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "docker.log"
    log.write_text("")
    result = subprocess.run(  # noqa: S603 (runs the repository's own hook script)
        [shutil.which("bash") or "/bin/bash", str(hook)],
        cwd=REPO,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
            "KAMAL_HOSTS": "server.example",
            "KAMAL_VERSION": "abc123",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    calls = [call.strip("\n").split("\n") for call in log.read_text().split("---\n") if call.strip()]
    return result, calls


def test_sql_reaches_psql_intact_through_ssh(tmp_path):
    result, calls = run_hook(tmp_path)
    assert result.returncode == 0, result.stderr
    sql_args = [call[call.index("-tAc") + 1] for call in calls if "-tAc" in call]
    assert sql_args == ["SELECT to_regclass('public.procrastinate_jobs') IS NOT NULL", "SELECT count(*) FROM media"]
    assert ["run", "--rm", "--network", "kamal", "--env-file", "/etc/anisync/migrator.env"] == calls[1][:6]
    assert calls[1][6:] == ["localhost:5555/anisync:abc123", "alembic", "upgrade", "head"]


def test_a_rollback_runs_nothing_on_the_server(tmp_path):
    result, calls = run_hook(tmp_path, KAMAL_COMMAND="rollback")
    assert result.returncode == 0 and calls == []
