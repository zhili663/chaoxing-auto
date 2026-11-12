import { ApiError } from "@/lib/api"

/** `GET /api/settings` 的一项（`value`/`default` 已由服务端脱敏）。 */
export interface SettingItem {
  value: unknown
  default: unknown
  overridden: boolean
  type: string
}

/** 键 → 配置项状态。 */
export type SettingsMap = Record<string, SettingItem>

/** 一项配置的语义规格：标签、单位与边界与 `config.py` 的校验规则对齐。 */
export interface SettingSpec {
  label: string
  hint: string
  unit?: string
  min?: number
  max?: number
  step?: number
  /** 有界、且用户按"相对位置"而非精确值思考的值（倍速）用 Slider + 数字输入。 */
  slider?: boolean
  options?: readonly string[]
  url?: boolean
}

export const SPECS: Record<string, SettingSpec> = {
  "ai.model": { label: "模型", hint: "OpenAI 兼容端点上的模型标识，例如 deepseek-chat" },
  "ai.base_url": {
    label: "接口地址",
    hint: "模型服务根地址（不含 /chat/completions）",
    url: true,
  },
  "ai.timeout": {
    label: "请求超时",
    hint: "单次模型调用最长等待",
    unit: "秒",
    min: 1,
    max: 600,
    step: 1,
  },
  "obs.log_level": {
    label: "日志级别",
    hint: "低于该级别的日志不落盘",
    options: ["DEBUG", "INFO", "WARNING", "ERROR"],
  },
  "obs.sse_buffer": {
    label: "事件缓冲",
    hint: "断线重连补发的事件条数",
    unit: "条",
    min: 0,
    max: 10000,
    step: 100,
  },
  "chaoxing.video_rate": {
    label: "播放倍速",
    hint: "按此倍率推进播放进度，1 为原速",
    unit: "×",
    min: 1,
    max: 16,
    step: 0.5,
    slider: true,
  },
  "chaoxing.heartbeat_interval_min": {
    label: "心跳间隔下限",
    hint: "两次上报间最短真实等待",
    unit: "秒",
    min: 1,
    max: 600,
    step: 1,
  },
  "chaoxing.heartbeat_interval_max": {
    label: "心跳间隔上限",
    hint: "两次上报间最长真实等待，需不小于下限",
    unit: "秒",
    min: 1,
    max: 600,
    step: 1,
  },
  "chaoxing.max_progress_step": {
    label: "单次推进上限",
    hint: "一次上报最多推进的播放秒数，过大容易出现拖动特征",
    unit: "秒",
    min: 1,
    max: 600,
    step: 1,
  },
  "chaoxing.request_min_interval": {
    label: "请求最小间隔",
    hint: "同一账号两次请求之间的最短间隔",
    unit: "秒",
    min: 0,
    max: 60,
    step: 0.1,
  },
  "chaoxing.max_retry": {
    label: "失败重试次数",
    hint: "单次请求失败后的重试上限",
    unit: "次",
    min: 0,
    max: 10,
    step: 1,
  },
  "chaoxing.max_account_concurrency": {
    label: "并发账号数",
    hint: "同时刷课的账号数，过多会吃满带宽与 CPU",
    unit: "个",
    min: 1,
    max: 16,
    step: 1,
  },
  "chaoxing.timeout": {
    label: "请求超时",
    hint: "超星侧单次请求的最长等待",
    unit: "秒",
    min: 1,
    max: 600,
    step: 1,
  },
}

/** 与后端 `preferences.SECRET_KEYS` 同口径：命中即视为密钥，值不回显。 */
const SECRET_MARKERS = ["secret_key", "api_token", "api_key", "password", "token"]

export function isSecret(key: string): boolean {
  const lower = key.toLowerCase()
  return SECRET_MARKERS.some((marker) => lower.includes(marker))
}

/** 数值项：有边界规格，或后端声明的类型就是 int/float。 */
export function isNumericScope(item: SettingItem, spec?: SettingSpec): boolean {
  return spec?.min !== undefined || item.type === "int" || item.type === "float"
}

/** 错误对象 → 给人看的文案（`ApiError.message` 已是后端的中文说明）。 */
export function errorText(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : String(cause)
}

/** 服务端值 → 输入框文本（null/undefined 视为未设置）。 */
export function valueText(value: unknown): string {
  if (value === null || value === undefined) return ""
  if (typeof value === "boolean") return value ? "true" : "false"
  return String(value)
}
