"""学习通协议常量：子系统域名与默认请求头。

这些是**协议事实**，属于"允许留在代码里"的类别（``docs/REFACTOR_PLAN.md`` §5.10）：
它们不因用户、环境或课程而变，改它们等于改实现，应当受类型与测试保护。

各子系统域名集中在此，学习通改版时只需替换这一处。
"""

from __future__ import annotations

# 学习通是多子系统站点：passport（登录）/ i（门户）/ mooc1·mooc1-1（老课）/ mooc2-ans（新课）
DOMAIN_PASSPORT = "https://passport2.chaoxing.com"
DOMAIN_PORTAL = "https://i.chaoxing.com"
DOMAIN_MOOC1 = "https://mooc1.chaoxing.com"
#: 手机 App H5/原生共用的 API 域（真机 2026-11-11 抓包）
DOMAIN_MOOC1_API = "https://mooc1-api.chaoxing.com"
DOMAIN_MOOC1_1 = "https://mooc1-1.chaoxing.com"
DOMAIN_MOOC2_ANS = "https://mooc2-ans.chaoxing.com"
DOMAIN_MOOC2 = "https://mooc2.chaoxing.com"

#: 与桌面浏览器一致的 UA（脚本特征会显著提高风控概率）
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

#: 默认请求头（账号级会话会在此之上覆盖 Referer）
COMMON_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}

#: **视频心跳**（``mooc-ans/multimedia/log``）专用的请求头 —— 实测口径（2026-10-14 用户抓包）：
#: ``Accept: */*`` + ``Content-Type: application/json``，且**不能**带 ``X-Requested-With``/``Origin``。
#: 别拿 :data:`XHR_HEADERS` 套在这条上：那是测验提交的口径，套错了照样 403。
HEARTBEAT_HEADERS = {
    "Accept": "*/*",
    "Content-Type": "application/json",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

#: **XHR 形状**接口的通用请求头（真机 2026-10-13：学习通的 ``mooc-ans/*`` 家族里，
#: **测验提交**这一类按 XHR 校验，缺了 ``X-Requested-With`` 一律回 403/「作业提交失败」）。
#: ⚠️ 别拿它去套**视频心跳**：心跳的实测口径是 :data:`HEARTBEAT_HEADERS`（见上）。
#: 只给浏览器里用 XHR 发的接口用：正常页面加载（HTML）必须保持导航形状，别混进 COMMON_HEADERS。
XHR_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": DOMAIN_MOOC1,
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
