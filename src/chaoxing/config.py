"""全局配置层：分域 Settings。

三层来源（见 ``docs/REFACTOR_PLAN.md`` §5.10）::

    代码默认  →  环境变量 / .env  →  数据库覆盖层（P4 接入）

本模块只负责前两层。**业务代码禁止直接读 os.environ**：一律经 :func:`get_settings`，
这样运行时改配置（P4 的 ``SettingsService``）才有单一的注入点。

环境变量命名
------------
嵌套域用双下划线分隔，例如::

    APP__PORT=8000
    CHAOXING__VIDEO_RATE=1.0
    AI__MODEL=deepseek-chat

顶层键只有一个：``SECRET_KEY``（供 Fernet 加解密账号凭据）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 仓库根目录（``src/chaoxing/config.py`` -> 上溯三级）
BASE_DIR = Path(__file__).resolve().parents[2]

BARE_HOSTS = frozenset({"0.0.0.0", "::", ""})


class AppSettings(BaseModel):
    """进程与 HTTP 服务本身的行为。"""

    name: str = "chaoxing"
    version: str = "2.0.0"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = BASE_DIR / "data"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    #: 机器凭证：给 curl / 运维脚本用（人凭证是 P4 的会话 cookie）
    api_token: str = ""
    #: 显式接受"对外监听且无鉴权"的风险，只应在可信内网里开
    allow_open_access: bool = False
    #: 首个用户之外是否还允许公开注册（内网自用可开，公网部署应关）
    allow_registration: bool = True
    #: 会话 Cookie 是否要求 HTTPS（本地 http 调试必须关，否则浏览器不回传）
    secure_cookies: bool = False
    #: 开发期前端来源（生产同源部署时留空即可）
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    #: 前端产物目录（默认仓库根的 ``web/dist``；测试可指向临时目录）
    web_dist_dir: Path | None = None
    #: orca-cli 可执行文件（留空则在 PATH 上找 ``orca``）—— 协议抓包与网页测试都走它
    orca_bin: str = ""

    @property
    def listens_publicly(self) -> bool:
        """是否监听所有网卡（而非仅回环）。"""
        return self.host in BARE_HOSTS

    @property
    def insecure_exposure(self) -> bool:
        """危险配置：对外监听却没有鉴权。命中时应用拒绝启动。"""
        return self.listens_publicly and not self.api_token and not self.allow_open_access

    def exposure_help(self) -> str:
        """危险配置的自救说明（原样打印给用户）。"""
        return (
            "拒绝启动：HOST 对外监听但没有设置 API_TOKEN。\n"
            "  1) 本机开发：把 APP__HOST 设为 127.0.0.1；或\n"
            "  2) 服务器部署：设置 APP__API_TOKEN（可用 `cx doctor --fix` 生成）；或\n"
            "  3) 确认只在可信内网使用：显式设置 APP__ALLOW_OPEN_ACCESS=true 承担风险。"
        )


class ChaoxingSettings(BaseModel):
    """学习通侧的行为参数（限速、心跳、并发）。"""

    #: 视频进度倍率。**必须是 1.0**：服务端会核对"上报进度 vs 真实经过时间"，
    #: 倍率大于 1 时虽然心跳仍返回 200，但回执永远 ``isPassed:false``（拿不到完成标记）——
    #: 实测：真实节奏打心跳，t=42s/74s 处即翻成 ``isPassed:true``（docs/MOOC_ANS_403.md §六）。
    video_rate: float = Field(default=1.0, gt=0, le=16)
    #: 两次心跳之间的真实等待区间（秒），随机取值形成抖动
    heartbeat_interval_min: float = Field(default=12.0, gt=0)
    heartbeat_interval_max: float = Field(default=20.0, gt=0)
    #: 倍率抖动幅度：每次上报在 rate ± jitter 内波动，长期均值不变
    video_rate_jitter: float = Field(default=0.04, ge=0, lt=1)
    #: 单次上报最大推进秒数，避免出现"拖动进度条"特征
    max_progress_step: float = Field(default=40.0, gt=0)
    #: 单账号内**同时推进的播放单元数**（并发）。1 = 串行（默认，最稳）；
    #: 想要更快就调大 —— 每个单元内部仍是"先视频后随题"的顺序，只是多个单元并行。
    video_concurrency: int = Field(default=1, ge=1, le=8)
    #: 单账号内部请求最小间隔（秒）——账号级限速，不是全局
    request_min_interval: float = Field(default=1.2, ge=0)
    request_timeout: float = Field(default=20.0, gt=0)
    max_retry: int = Field(default=3, ge=0, le=10)
    #: 提交测验前是否要求**每道题**都有答案。默认 True：宁可让任务点失败/重试，
    #: 也不提交空白或部分答案（服务端接受部分答案，但会计 0 分/低分）。
    quiz_require_full_answers: bool = True
    #: 同时刷课的账号数（每账号 1 协程 + 1 连接池）
    #: 实测 4C/4G 跑 20 个账号仍是 I/O 型负载（每个账号 ~1 请求/15s）——
    #: 上限按"设备能否承受"而非拍脑袋定，故放到 64；默认仍保守为 3。
    max_account_concurrency: int = Field(default=3, ge=1, le=64)
    #: 出网代理（例如 http://127.0.0.1:7890），为空表示直连
    proxy: str = ""
    #: 代理池文件（一行一个出口，见 `cx proxy import`）。为空则用 ``data/proxies.txt``；
    #: 文件不存在=空池=全直连 —— 不改变现有行为。
    proxy_pool_file: str = ""
    #: jhao104/proxy_pool sidecar API（如 ``http://127.0.0.1:5010``）；为空不启用。
    #: 启用后按 ``proxy_pool_refresh_s`` 周期拉取 ``/all/?type=https``，
    #: 403 时调 ``/delete/`` 淘汰坏出口；本地仍保留按账号粘性与冷却。
    proxy_pool_api: str = ""
    #: 远端代理池刷新周期（秒）。免费池变化快，但太频繁会拖慢首个请求。
    proxy_pool_refresh_s: float = Field(default=120.0, ge=10.0)
    #: 主动轮换：同一账号累计多少个请求后换出口（0=关闭）。
    #: 平台风控往往在数十个请求后触发；不要等到 403 才换。
    proxy_rotate_requests: int = Field(default=80, ge=0)
    #: 主动轮换：同一账号出口粘性最长时间（秒，0=关闭）。
    #: 与请求数阈值配合，谁先到谁触发；轮换只换出口，不冷却旧出口。
    proxy_rotate_seconds: float = Field(default=1800.0, ge=0)
    #: 调代理池 API 的超时（秒）；不要与学习通请求超时混用。
    proxy_pool_timeout: float = Field(default=5.0, gt=0, le=30)
    #: 某个出口被拒（403）后的冷却秒数：冷却期内不再用它，账号自动换出口
    proxy_cooldown_s: float = Field(default=300.0, gt=0)
    captcha_mode: Literal["manual", "ocr", "platform"] = "manual"

    @model_validator(mode="after")
    def _validate_heartbeat_window(self) -> ChaoxingSettings:
        """心跳区间写反是最典型的配置事故，宁可启动即失败。"""
        if self.heartbeat_interval_min > self.heartbeat_interval_max:
            raise ValueError(
                "CHAOXING__HEARTBEAT_INTERVAL_MIN 不能大于 MAX "
                f"（当前 {self.heartbeat_interval_min} > {self.heartbeat_interval_max}）"
            )
        return self


class AISettings(BaseModel):
    """大模型与检索（默认走 OpenAI 兼容端点，见计划 §4.3）。"""

    provider: Literal["openai", "fake"] = "openai"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    api_key: str = ""
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    timeout: float = Field(default=60.0, gt=0)
    websearch_enabled: bool = True

    @property
    def enabled(self) -> bool:
        """没有 key 时视为未配置（答案引擎会降级为"低置信度"而不是崩）。"""
        return self.provider == "fake" or bool(self.api_key)


class ObsSettings(BaseModel):
    """可观测性四通道（app 日志 / 刷课记录 / 刷题记录 / 行为审计）。"""

    #: 是否输出 JSON 行日志（生产 true，本机排障可 false）
    log_json: bool = True
    #: 日志目录；为空则用 ``app.data_dir / "logs"``
    log_dir: Path | None = None
    audit_enabled: bool = True
    #: SSE 事件环形缓冲长度（断线重连补发用）
    sse_buffer: int = Field(default=500, ge=0, le=10_000)


class Settings(BaseSettings):
    """进程级配置单例（见 :func:`get_settings`）。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    app: AppSettings = AppSettings()
    chaoxing: ChaoxingSettings = ChaoxingSettings()
    ai: AISettings = AISettings()
    obs: ObsSettings = ObsSettings()
    #: 账号凭据的对称加密密钥（Fernet）。为空时由 `cx doctor --fix` 生成。
    secret_key: str = ""

    @property
    def log_dir(self) -> Path:
        """日志目录（``obs.log_dir`` 优先，否则 ``app.data_dir/logs``）。"""
        return self.obs.log_dir or (self.app.data_dir / "logs")

    @property
    def db_path(self) -> Path:
        """SQLite 数据库文件路径。"""
        return self.app.data_dir / "app.db"

    @property
    def web_dist(self) -> Path:
        """前端构建产物目录（存在时由 API 托管，实现同源部署）。"""
        return self.app.web_dist_dir or (BASE_DIR / "web" / "dist")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程内唯一的配置对象。

    :returns: 解析完成的 :class:`Settings`
    """
    return Settings()


def reload_settings() -> Settings:
    """清空缓存并重新解析配置（供配置中心在运行时改配置后调用）。

    :returns: 重新解析后的 :class:`Settings`
    """
    get_settings.cache_clear()
    return get_settings()
