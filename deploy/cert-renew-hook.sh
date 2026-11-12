#!/bin/sh
# ============================================================================
# Let's Encrypt 续期钩子 —— 把新证书同步给 1Panel 的 OpenResty 并重载
#
# 安装位置：/etc/letsencrypt/renewal-hooks/deploy/chaoxing-reload.sh
# certbot 每次成功续期后会自动执行它（续期任务已由 certbot 自动注册）。
#
# 为什么需要它：证书放在 /etc/letsencrypt 下，而 OpenResty 跑在 Docker 容器里，
# 只能看到挂载进去的 /opt/1panel/apps/openresty/openresty/conf/ssl 目录。
# 不复制的话，续期后容器里还是旧证书，90 天后浏览器就会报错。
#
# 使用前改两处：DOMAIN 换成你的域名，CONTAINER 换成 `docker ps` 里的容器名。
# ============================================================================
set -e

DOMAIN=your.domain.com
SRC=/etc/letsencrypt/live/${DOMAIN}
DST=/opt/1panel/apps/openresty/openresty/conf/ssl/${DOMAIN}
CONTAINER=1Panel-openresty-XXXX

cp "${SRC}/fullchain.pem" "${DST}/fullchain.pem"
cp "${SRC}/privkey.pem" "${DST}/privkey.pem"
chmod 644 "${DST}/fullchain.pem"
chmod 600 "${DST}/privkey.pem"

docker exec "${CONTAINER}" openresty -s reload
