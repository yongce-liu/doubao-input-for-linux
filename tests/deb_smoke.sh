#!/bin/sh
# 仅在一次性 Ubuntu 容器中运行：安装、启动 CLI、检查权限和卸载。
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
useradd -m tester
SUDO_USER=tester apt-get install -y --no-install-recommends "$1"
test -x /usr/bin/doubao-input
test -f /usr/lib/systemd/user/doubao-input.service
test ! -e /etc/systemd/user/default.target.wants/doubao-input.service
id -nG tester | grep -qw input
runuser -u tester -- /usr/bin/doubao-input --help
runuser -u tester -- /usr/bin/doubao-input devices
runuser -u tester -- /usr/bin/doubao-input status
# 缺浏览器时友好失败，而非依赖系统 Python/gi 才能启动。
if runuser -u tester -- /usr/bin/doubao-input login > /tmp/login-output 2>&1; then
    echo "预期无浏览器的干净镜像登录失败" >&2
    exit 1
fi
grep -q 'Chrome/Chromium' /tmp/login-output
apt-get purge -y doubao-input
test ! -e /usr/bin/doubao-input
test ! -e /usr/lib/systemd/user/doubao-input.service
echo 'deb 安装、CLI、用户权限、无默认自启和卸载检查通过'
