"""登录口令的查看与重设。在服务器上跑。

    python3 /opt/journal/deploy/token.py show         # 查看当前口令
    python3 /opt/journal/deploy/token.py new          # 换一个随机的并重启
    python3 /opt/journal/deploy/token.py set 我的口令   # 设成你记得住的

改完会重启服务，所有设备需要重新登录一次。
"""

from __future__ import annotations

import secrets
import subprocess
import sys
from pathlib import Path

ENV_FILE = Path("/opt/journal/.env")
KEY = "JOURNAL_APP_TOKEN"


def read_lines() -> list[str]:
    if not ENV_FILE.exists():
        raise SystemExit(f"找不到 {ENV_FILE}")
    return ENV_FILE.read_text(encoding="utf-8").splitlines()


def current() -> str | None:
    for line in read_lines():
        if line.startswith(f"{KEY}="):
            return line.split("=", 1)[1]
    return None


def write_token(token: str) -> None:
    token = token.strip()
    if not token:
        raise SystemExit("口令不能是空的。")
    if "\n" in token:
        raise SystemExit("口令不能包含换行。")

    lines = read_lines()
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(f"{KEY}="):
            lines[index] = f"{KEY}={token}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{KEY}={token}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 服务以 journal 用户跑，读不到 root 的文件就会起不来。
    # 这一步很无聊，但漏了就是启不来，所以写进脚本里。
    subprocess.run(["chown", "journal:journal", str(ENV_FILE)], check=True)
    ENV_FILE.chmod(0o600)


def restart() -> None:
    subprocess.run(
        ["systemctl", "restart", "journal"], check=True, capture_output=True
    )
    # 必须 capture_output：不然 systemctl 的输出会漏到 stdout，
    # 把口令污染成两行，拿去做变量就报 JSON 错误。踩过一次。
    result = subprocess.run(
        ["systemctl", "is-active", "journal"], capture_output=True, text=True
    )
    state = result.stdout.strip()
    if state != "active":
        raise SystemExit(f"服务没起来（{state}），看一下 journalctl -u journal")


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else "show"

    if action == "show":
        token = current()
        if token is None:
            print(f"{ENV_FILE} 里没有 {KEY}")
            return 1
        print(token)
        return 0

    if action == "new":
        token = secrets.token_urlsafe(32)
    elif action == "set":
        if len(argv) < 3:
            print("用法: token.py set <你的口令>", file=sys.stderr)
            return 2
        token = argv[2]
    else:
        print(__doc__, file=sys.stderr)
        return 2

    write_token(token)
    restart()
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
