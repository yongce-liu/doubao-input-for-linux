#!/bin/sh
# 在 Ubuntu 22.04 基线上打包独立运行的 deb；无需在宿主机安装构建依赖。
set -eu
cd "$(dirname "$0")/.."
case "${1:-}" in
    "" | --install) ;;
    *) echo "用法：$0 [--install]" >&2; exit 2 ;;
esac
docker build --file packaging/Dockerfile.binary --target deb-artifact \
    --output type=local,dest=dist .
if [ "${1:-}" = --install ]; then
    version=$(sed -n '1s/.*(\(.*\)).*/\1/p' debian/changelog)
    arch=$(dpkg --print-architecture)
    sudo apt install "./dist/doubao-input_${version}_${arch}.deb"
fi
