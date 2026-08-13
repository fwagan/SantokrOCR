// 烘焙会话本地持久化：误触刷新时恢复 T0 与计时锚点（localStorage）
//
// 方案 A（本文件）：同一浏览器同一源有效；换设备/清浏览器数据时失效。
// 方案 B（后续跟进）：主进程 get_status 暴露当前烘焙已进行秒数 → 前端反推 T0，
//   对任意刷新/设备生效但 T0 为近似值；app 层计划扩展 get checkpoint，届时可带入 T0。

export interface PersistedSession {
  /** 入豆点击 UTC ms */
  t0: number
  turnaroundStart: number | null
  fcStart: number | null
  fcEnd: number | null
  scStart: number | null
  scEnd: number | null
  /** end ok 后为 true（已结束的会话不恢复） */
  ended: boolean
  lastHeater: number
  lastFan: number
  /** 一爆开始温度（get_temp 查询结果，随会话恢复回显） */
  fcStartTemp: number | null
  /** 一爆结束后冻结的 ΔT；必须持久化——否则 fcEnd 后恢复会话会用"恢复时刻"的当前豆温错误重冻 */
  fcDeltaT: number | null
  /** 写入时间，用于兜底过期会话判定 */
  savedAt: number
}

const SESSION_KEY = 'santokr-mobile-session'
// 兜底：超时长的残留会话（如跨天、跨会话）不恢复，防误用旧 t0
const SESSION_MAX_AGE_MS = 12 * 60 * 60 * 1000

export function readSession(): PersistedSession | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY)
    if (!raw) return null
    const s = JSON.parse(raw) as PersistedSession
    if (typeof s.t0 !== 'number' || !Number.isFinite(s.t0)) return null
    if (Date.now() - s.savedAt > SESSION_MAX_AGE_MS) return null
    return s
  } catch {
    return null
  }
}

export function writeSession(s: PersistedSession): void {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(s))
  } catch {
    // localStorage 不可用（隐私模式/被禁用）时静默降级，功能不受影响
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(SESSION_KEY)
  } catch {
    // ignore
  }
}
