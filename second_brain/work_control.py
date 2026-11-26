"""Grant the local user permission to stop one running workspace server."""

from contextlib import contextmanager
import hmac
import os
from pathlib import Path
import secrets
import sys
from threading import Thread


def watch_parent(httpd):
    if os.environ.get("SECOND_BRAIN_PARENT_STDIN") == "1":
        def wait_for_eof():
            try:
                while os.read(0, 4096):
                    pass
            except OSError:
                pass
            httpd.shutdown()

        Thread(target=wait_for_eof, daemon=True).start()


@contextmanager
def enable(httpd):
    instance = secrets.token_hex(16)
    key = secrets.token_bytes(32)
    run = Path.home() / ".second-brain" / "run"
    path = run / (instance + ".key")
    try:
        run.parent.mkdir(mode=0o700, exist_ok=True)
        run.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as error:
        # A read-only user profile can still use the normal foreground CLI.
        print(f"Остановка из Obsidian недоступна: {error}", file=sys.stderr, flush=True)
        yield
        return
    try:
        with os.fdopen(fd, "w", encoding="ascii") as output:
            output.write(key.hex())
        message = f"stop:{instance}:{httpd.server_port}:{httpd.vault_binding}".encode("ascii")
        httpd.work_control = instance
        httpd.stop_proof = hmac.digest(key, message, "sha256").hex()
        yield
    finally:
        path.unlink(missing_ok=True)
