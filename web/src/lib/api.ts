import ky from "ky"

/**
 * 后端统一的错误形状（见 `chaoxing/api/errors.py`）。
 *
 * 约定：`code` 是机器可读标识（前端据此分支），`message` 是给人看的中文说明 ——
 * 两者都有才够用：只给 code 用户看不懂，只给 message 前端没法判断类型。
 */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    detail?: unknown
  }
}

/** 一次失败的 API 调用。 */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly detail?: unknown

  constructor(code: string, message: string, status: number, detail?: unknown) {
    super(message)
    this.name = "ApiError"
    this.code = code
    this.status = status
    this.detail = detail
  }

  /** 未登录（前端据此跳登录页）。 */
  get isUnauthorized(): boolean {
    return this.status === 401
  }

  /** 权限不足（前端据此提示"没有权限"而不是"出错了"）。 */
  get isForbidden(): boolean {
    return this.status === 403
  }
}

/**
 * 共享的 HTTP 客户端。
 *
 * - `credentials: include` —— 会话是 HttpOnly Cookie，不带它等于没登录；
 * - `retry: 0` —— **写操作绝不自动重试**（一次"创建账号"重试两次就是三个账号）。
 */
export const http = ky.create({
  // ky 2.x 用 `prefix`（1.x 叫 `prefixUrl`）
  prefix: "/api/",
  credentials: "include",
  retry: 0,
  timeout: 120_000,
})

/**
 * 把 ky 抛出的错误翻成 {@link ApiError}。
 *
 * 单独抽出来是因为 ky 的 `beforeError` 钩子签名要求返回 `Error`，
 * 在那里抛自定义异常类型对不上；在调用点统一转换更直白。
 */
export async function toApiError(cause: unknown): Promise<ApiError> {
  const response = (cause as { response?: Response }).response
  if (response) {
    try {
      const body = (await response.clone().json()) as ApiErrorBody
      if (body?.error) {
        return new ApiError(body.error.code, body.error.message, response.status, body.error.detail)
      }
    } catch {
      // 响应不是我们的错误形状（网关错误页等）—— 保留状态码，别把信息吞掉
    }
    return new ApiError("http_error", `请求失败（HTTP ${response.status}）`, response.status)
  }
  return new ApiError("network_error", cause instanceof Error ? cause.message : "网络错误", 0)
}

async function unwrap<T>(promise: Promise<T>): Promise<T> {
  try {
    return await promise
  } catch (cause) {
    throw await toApiError(cause)
  }
}

/** GET 一个 JSON 资源。 */
export function apiGet<T>(path: string, searchParams?: Record<string, unknown>): Promise<T> {
  return unwrap(
    http.get(path, { searchParams: searchParams as Record<string, string> | undefined }).json<T>(),
  )
}

/** POST（写操作，不重试）。 */
export function apiPost<T>(path: string, json?: unknown): Promise<T> {
  return unwrap(http.post(path, json === undefined ? undefined : { json }).json<T>())
}

/** PATCH（写操作，不重试）。 */
export function apiPatch<T>(path: string, json?: unknown): Promise<T> {
  return unwrap(http.patch(path, json === undefined ? undefined : { json }).json<T>())
}

/** PUT（幂等写，不重试）—— 「同一个 key 写多次」用它比 POST 更贴语义。 */
export function apiPut<T>(path: string, json?: unknown): Promise<T> {
  return unwrap(http.put(path, json === undefined ? undefined : { json }).json<T>())
}

/** DELETE。 */
export function apiDelete<T>(path: string): Promise<T> {
  return unwrap(http.delete(path).json<T>())
}
