# proxy_pool 接入说明

> 结论：`jhao104/proxy_pool` **不是进程内插件**，而是 Flask + Redis/SSDB 的独立服务。
> 本项目把它当 **sidecar 代理源**，通过 HTTP API 取快照/删坏出口；不做依赖合并。

## 为什么不直接装进项目 venv

| 事实 | 影响 |
| --- | --- |
| `proxy_pool` 分 `schedule` 与 `server` 两个进程 | 无法作为库直接 import 后“自动爬取” |
| 依赖锁在 Python 3.8–3.11，Flask 2.1.1 / werkzeug<2.2 / gunicorn 19.9 | 与本项目 Python 3.12 + FastAPI 依赖冲突 |
| 需要 Redis 或 SSDB | 项目 SQLite 之外多一个外部服务 |
| API 无鉴权 | 只能监听 `127.0.0.1` 或加防火墙 |

## 启动 sidecar（推荐 Docker）

```bash
cd proxy_pool
docker compose up -d          # proxy_pool:5010 + redis
curl http://127.0.0.1:5010/count/
```

没有 Docker 时，用 **独立的 Python 3.10/3.11 venv** 运行，不要装进本项目 venv：

```bash
python3.10 -m venv .venv-proxypool
.venv-proxypool/bin/pip install -r requirements.txt
.venv-proxypool/bin/python proxyPool.py schedule   # 一个进程
.venv-proxypool/bin/python proxyPool.py server     # 另一个进程
```

## 本项目配置

```dotenv
CHAOXING__PROXY_POOL_API=http://127.0.0.1:5010
CHAOXING__PROXY_POOL_REFRESH_S=120
CHAOXING__PROXY_POOL_TIMEOUT=5
# 主动轮换（0=关闭）：同一账号请求数/粘性秒数，边界处换出口
CHAOXING__PROXY_ROTATE_REQUESTS=80
CHAOXING__PROXY_ROTATE_SECONDS=1800
```

不配置 = 不启用，行为与从前完全一致（文件池或直连）。

## 数据流

```
proxy_pool /all/?type=https
        │  每 refresh_s 秒
        ▼
ProxyPoolClient.fetch_all()          # 只取 https=true，归一成 http://host:port
        ▼
ProxyPool.sync_remote(entries)       # 保留同 URL 的粘性 / 冷却 / 成功计数
        ▼
ChaoxingTransport._client_for()      # 同账号粘性出口
        │
        ├─ 成功：该账号继续用这个出口
        └─ 403：本地冷却 + 下次换出口（**不删远端共享池**；403 多为业务拒绝）
```

关键点：

- **不是 `/get` 随机取**：那会让同一账号每条请求换 IP，反而是风控信号；
  本项目由本地 `ProxyPool` 做账号粘性。
- **主动轮换也不能切在心跳中途**：每次账号 run 开始时换一次；run 内累计
  `CHAOXING__PROXY_ROTATE_REQUESTS` 个请求或粘性超过
  `CHAOXING__PROXY_ROTATE_SECONDS` 后，只在**任务点/播放单元边界**换。
  403 仍走本地冷却；**不调用远端 /delete/**（学习通 403 大量是 ktoken/enc
  过期等业务拒绝，删掉共享出口会误伤其它账号）。主动轮换不冷却旧出口。
- `/all?type=https` 的 `https=true` 只表示“支持 HTTPS CONNECT”；
  httpx 的 `proxy=` 仍写成 `http://ip:port`（TLS-to-proxy 是另一回事）。
- 远端暂时返回空列表时保留旧快照，避免一次抖动清空可用出口。
- 代理池 API 失败时保留旧池；30 秒后重试，不会拖垮刷课。

## 运维命令

```bash
cx proxy remote                 # 查看远端 HTTPS 出口数量与前 10 条
cx proxy remote --save          # 额外写入 data/proxies.txt 作离线兜底
```

## 已知限制

- `proxy_pool` 的校验目标是 `httpbin.org` / `qq.com`，**不是学习通**；
  能过 qq.com 的免费代理仍可能被 `mooc-ans` 403。当前以真实请求作为最终校验：
  一旦 403 就本地冷却；不据此删除远端共享出口（403 不一定是出口问题）。
- 免费代理稳定性/信誉都有限；先用 1 个账号、`--limit 1~3` 小跑验证，
  再扩大批量。不要一上来全量并发。
- API 无鉴权；不要把 5010 暴露到公网。
