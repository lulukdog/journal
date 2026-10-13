#!/usr/bin/env bash
# 本地跑这个脚本，把代码同步到服务器并重启服务。
# 用法：bash deploy/deploy.sh
set -euo pipefail

HOST="${JOURNAL_HOST:-journal-server}"
TARGET="/opt/journal"

cd "$(dirname "$0")/.."

# 不用 -a：它会把源文件的 mtime 一起带过去，于是目标文件的时间戳永远不变。
# 两个后果都踩过：
#   1. 浏览器认为 Last-Modified 没变，不重新拉 JS，还在跑旧的
#   2. Python 认为 .pyc 没过期，继续跑旧字节码
# 改成按内容校验传输，目标文件的时间戳就是本次部署的时间。
echo "→ 同步代码到 $HOST:$TARGET"
rsync -rlzc --delete \
  --exclude='.git' --exclude='.venv' --exclude='.superpowers' \
  --exclude='__pycache__' --exclude='.env' --exclude='.env.*' --exclude='*.pyc' \
  ./ "$HOST:$TARGET/"

echo "→ 同步依赖"
ssh "$HOST" "cd $TARGET && .venv/bin/pip install -q -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/"

echo "→ 清字节码缓存"
ssh "$HOST" "find $TARGET -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true"

echo "→ 修正属主"
# .env 必须归 journal，服务才读得到；写两遍是因为 rsync 会把目录属主改掉。
ssh "$HOST" "chown -R journal:journal $TARGET && chown journal:journal $TARGET/.env"

echo "→ 重启服务"
ssh "$HOST" "systemctl restart journal && systemctl is-active journal"

echo "→ 状态"
ssh "$HOST" "systemctl status journal --no-pager -l | head -12"
