// 后端 API 客户端：GET /api/status 轮询 + POST /api/events 事件发送
//
// 传输层容错（Phase 3 设计确认）：
// - fetch 无默认超时，Wi-Fi 黑障会挂几十秒 → AbortController 5s 超时，超时即判失败
// - 事件 offset 在点击瞬间冻结，重试必须原样重发同一 payload（重算 offset 会产生时间偏差事件）
// - 网络错误 / 超时 / 5xx → 自动重试 3 次（短退避）；200+ok:false 业务拒绝不重试

export type RoastState = 'idle' | 'waiting_charge' | 'roasting'

/** GET /api/status 响应 */
export interface Status {
  temp1: number | null
  temp2: number | null
  ror: number | null
  state: RoastState
  turnaround_offset: number | null
  curve_name: string
}

/** POST /api/events 响应（200 + ok:false = 业务拒绝） */
export interface EventResponse {
  ok: boolean
  error?: string
}

/** GET /api/checkpoints 响应 */
export interface Checkpoint {
  type: 'auto' | 'manual'
  event: string
  temp: number | null
  value: string
  /** 与上一 checkpoint 的理想时间差（秒）；countdown = 上一达成时刻 + delta。首条（入豆）为 null */
  delta: number | null
}

// 事件类型（与 data/types.py EventType 对齐）
export const EVENT_TYPES = {
  CHARGE: '入豆',
  TURNAROUND: '回温',
  FC_START: '一爆开始',
  FC_END: '一爆结束',
  SC_START: '二爆开始',
  SC_END: '二爆结束',
  ROAST_END: '烘焙结束',
  HEATER_ADJUST: '调整火力',
  FAN_ADJUST: '调整风门',
} as const

export interface StartPayload {
  cmd: 'start'
  heater_initial: number
  fan_initial: number
}

export interface AddEventPayload {
  cmd: 'add_event'
  event: { type: string; offset: number }
}

export interface AddValueEventPayload {
  cmd: 'add_value_event'
  event: { type: string; offset: number; value: number }
}

export interface EndPayload {
  cmd: 'end'
  event: { type: string; offset: number }
}

export type EventCommand =
  | StartPayload
  | AddEventPayload
  | AddValueEventPayload
  | EndPayload

const REQUEST_TIMEOUT_MS = 5000
const RETRY_COUNT = 3
const RETRY_BACKOFF_MS = [300, 600, 1200]

export class ApiError extends Error {
  readonly status: number | null
  constructor(message: string, status: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** 带 AbortController 超时的 fetch（5s），避免黑障时请求无限挂起 */
async function request(path: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    return await fetch(path, { ...init, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

export async function getStatus(): Promise<Status> {
  let res: Response
  try {
    res = await request('/api/status')
  } catch (e) {
    // 轮询路径的 fetch 超时/网络错误转友好文案（StatusBanner 直接显示 message）
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError('连接超时')
    }
    if (e instanceof TypeError) {
      throw new ApiError('网络连接失败')
    }
    throw e
  }
  if (!res.ok) {
    throw new ApiError(
      res.status === 502 ? '主进程不可达' : `状态获取失败 (HTTP ${res.status})`,
      res.status,
    )
  }
  return (await res.json()) as Status
}

/**
 * 获取 checkpoint 静态列表（GET /api/checkpoints）
 *
 * 返回 null = 无理想曲线（checkpoints: null）或全部重试失败（调用方静默保留旧缓存）。
 * 网络错误 / 5s 超时 / 5xx 重试 3 次（短退避，同 getTemp 语义）；4xx 不重试直接返回 null。
 */
export async function getCheckpoints(): Promise<Checkpoint[] | null> {
  for (let attempt = 0; attempt <= RETRY_COUNT; attempt++) {
    if (attempt > 0) {
      await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS[attempt - 1] ?? 400))
    }
    try {
      const res = await request('/api/checkpoints')
      if (!res.ok) {
        if (res.status >= 400 && res.status < 500) return null
        continue
      }
      const data = (await res.json()) as { checkpoints: Checkpoint[] | null }
      return data.checkpoints
    } catch {
      // 网络/超时：继续重试
    }
  }
  return null
}

/**
 * 查询 offset（相对入豆秒）时刻的重采样豆温估算（GET /api/temp）
 *
 * 软读：网络错误 / 5s 超时 / 5xx 重试 3 次（短退避，同 postEvent 重试语义）；
 * 200+ok:false（数据不足/超范围）与 4xx（参数错误）不重试，返回 null → 调用方显示 '--'。
 */
export async function getTemp(offset: number): Promise<number | null> {
  for (let attempt = 0; attempt <= RETRY_COUNT; attempt++) {
    if (attempt > 0) {
      await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS[attempt - 1] ?? 400))
    }
    try {
      const res = await request(`/api/temp?offset=${offset}`)
      if (!res.ok) {
        if (res.status >= 400 && res.status < 500) return null // 4xx 不重试
        continue // 5xx/502 视为传输失败，继续重试
      }
      const data = (await res.json()) as { ok?: boolean; temp1?: number | null }
      if (data.ok === false) return null
      return typeof data.temp1 === 'number' && Number.isFinite(data.temp1)
        ? data.temp1
        : null
    } catch {
      // 网络错误/超时：继续重试（全部失败统一返回 null）
    }
  }
  return null
}

/**
 * 发送事件命令（start / add_event / add_value_event / end）
 *
 * - payload 由调用方冻结（offset 在点击瞬间算好），重试原样重发
 * - 200 响应（ok:true / ok:false）直接返回：业务拒绝重试无意义
 * - 4xx（请求参数错）不重试：正常前端不会触发，防御性处理
 * - 网络错误 / 5s 超时 / 5xx：重试，全部失败抛 ApiError
 */
export async function postEvent(payload: EventCommand): Promise<EventResponse> {
  let lastError: unknown = null
  for (let attempt = 0; attempt <= RETRY_COUNT; attempt++) {
    if (attempt > 0) {
      await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS[attempt - 1] ?? 400))
    }
    try {
      const res = await request('/api/events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        return (await res.json()) as EventResponse
      }
      if (res.status >= 400 && res.status < 500) {
        throw new ApiError(`请求错误 (HTTP ${res.status})`, res.status)
      }
      // 5xx（502 主进程不可达 / 500）：传输层失败，本轮重试
      lastError = new ApiError(
        res.status === 502 ? '主进程不可达' : `服务错误 (HTTP ${res.status})`,
        res.status,
      )
    } catch (e) {
      if (e instanceof ApiError) throw e // 4xx 直接抛出，不重试
      lastError =
        e instanceof DOMException && e.name === 'AbortError'
          ? new ApiError('请求超时')
          : e instanceof Error
            ? new ApiError(e.message)
            : new ApiError(String(e))
    }
  }
  throw lastError instanceof Error ? lastError : new ApiError('事件发送失败')
}
