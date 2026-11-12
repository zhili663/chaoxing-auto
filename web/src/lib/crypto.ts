/**
 * 前端口令哈希：**明文不出浏览器**（后端只接受这个 64 位十六进制值）。
 *
 * 登录、注册、管理员建号三处必须**逐字节一致**：口径一分叉就会造出"能建不能登"的账号。
 * 后端 `utils/security.looks_like_client_secret` 校验的就是它的形态（`^[0-9a-f]{64}$`），
 * 所以这里只留一份实现，别再各处手抄。
 */
export async function hashPassword(password: string): Promise<string> {
  const bytes = new TextEncoder().encode(password)
  const digest = await crypto.subtle.digest("SHA-256", bytes)
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
}
