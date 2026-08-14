// 前端 checkpoint 纯函数逻辑：达成推导 + countdown + 时间格式化
// 与 React 解耦，便于独立验证（spec: 2026-08-14-checkpoint-design §6.2）
import { EVENT_TYPES, type Checkpoint } from './api'

/** App 侧锚点状态（auto 事件达成标志 + 达成时刻/温度 + manual 点击记录） */
export interface AnchorInput {
  t0: number | null
  turnaroundStart: number | null
  turnaroundTemp: number | null
  fcStart: number | null
  fcEnd: number | null
  scStart: number | null
  scEnd: number | null
  endTime: number | null
  endTemp: number | null
  fcStartTemp: number | null
  manualClicks: Record<number, { at: number; temp: number | null }>
}

/** 单个 checkpoint 的显示状态（App 派生后传给 CheckpointPanel 纯渲染） */
export interface CheckpointState {
  index: number
  cp: Checkpoint
  achieved: boolean
  /** 达成时刻（UTC ms）；auto 事件未发生 / manual 未点击且未被 auto 补齐 → null */
  achievedAt: number | null
  /** 达成时的实际豆温；自动补齐的 manual 为 null */
  actualTemp: number | null
  /** 相对回温的理想秒数（回温=0）；回温行本身为 null（不显示） */
  idealRelSec: number | null
  /** 相对回温的实际秒数（回温=0）；回温未达成前为 null（显示 --:--） */
  actualRelSec: number | null
}

export interface DerivedCheckpoints {
  rows: CheckpointState[]
  /** 第一个未达成 checkpoint 的 index；null = 全部达成 */
  nextIndex: number | null
  /** next 的 countdown 剩余秒（remaining）；null = 不显示（首条或已全达成） */
  remaining: number | null
}

export function deriveCheckpoints(
  checkpoints: Checkpoint[],
  anchors: AnchorInput,
  now: number,
): DerivedCheckpoints {
  // 1. 理想相对回温时间：delta 累加得相对入豆绝对秒，再减回温的绝对秒
  const turnaroundIdx = checkpoints.findIndex((c) => c.event === EVENT_TYPES.TURNAROUND)
  const idealAbs: number[] = []
  let acc = 0
  for (let i = 0; i < checkpoints.length; i++) {
    if (i > 0) acc += checkpoints[i].delta ?? 0
    idealAbs.push(acc)
  }
  const turnaroundAbs = turnaroundIdx >= 0 ? idealAbs[turnaroundIdx] : null

  // 2. 每行状态（先按锚点 / manualClicks 判定）
  const rows: CheckpointState[] = checkpoints.map((cp, i) => {
    const achievedAt = achievedAtOf(cp.event, anchors, i)
    let idealRelSec: number | null = null
    if (turnaroundAbs != null && turnaroundIdx >= 0 && i !== turnaroundIdx) {
      idealRelSec = idealAbs[i] - turnaroundAbs
    }
    let actualRelSec: number | null = null
    if (achievedAt != null && anchors.turnaroundStart != null && i !== turnaroundIdx) {
      actualRelSec = (achievedAt - anchors.turnaroundStart) / 1000
    }
    return {
      index: i,
      cp,
      achieved: achievedAt != null,
      achievedAt,
      actualTemp: actualTempOf(cp.event, anchors, i),
      idealRelSec,
      actualRelSec,
    }
  })

  // 3. auto 自动补齐：auto 达成时，其前方未达成 manual 视为达成（时刻 = auto，无实际温度）
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].achieved || rows[i].cp.type !== 'manual') continue
    for (let j = i + 1; j < rows.length; j++) {
      if (rows[j].achieved && rows[j].cp.type !== 'manual') {
        const at = rows[j].achievedAt
        rows[i].achieved = true
        rows[i].achievedAt = at
        if (at != null && anchors.turnaroundStart != null && i !== turnaroundIdx) {
          rows[i].actualRelSec = (at - anchors.turnaroundStart) / 1000
        }
        break
      }
    }
  }

  // 4. next + countdown（恒前缀，相邻锚定：prev = next-1 必已达成）
  const nextIdx = rows.findIndex((r) => !r.achieved)
  const nextIndex = nextIdx >= 0 ? nextIdx : null
  let remaining: number | null = null
  if (nextIndex != null && nextIndex > 0) {
    const prevAt = rows[nextIndex - 1].achievedAt
    if (prevAt != null) {
      remaining = (prevAt + (checkpoints[nextIndex].delta ?? 0) * 1000 - now) / 1000
    }
  }
  return { rows, nextIndex, remaining }
}

/** 依据锚点状态取 checkpoint 的达成时刻（auto 事件未发生 / manual 未点击 → null） */
function achievedAtOf(event: string, a: AnchorInput, i: number): number | null {
  switch (event) {
    case EVENT_TYPES.CHARGE:
      return a.t0
    case EVENT_TYPES.TURNAROUND:
      return a.turnaroundStart
    case EVENT_TYPES.FC_START:
      return a.fcStart
    case EVENT_TYPES.FC_END:
      return a.fcEnd
    case EVENT_TYPES.SC_START:
      return a.scStart
    case EVENT_TYPES.SC_END:
      return a.scEnd
    case EVENT_TYPES.ROAST_END:
      return a.endTime
    case EVENT_TYPES.HEATER_ADJUST:
    case EVENT_TYPES.FAN_ADJUST:
      return a.manualClicks[i]?.at ?? null
    default:
      return null
  }
}

/** 依据锚点状态取 checkpoint 达成时的实际豆温（无记录 → null） */
function actualTempOf(event: string, a: AnchorInput, i: number): number | null {
  switch (event) {
    case EVENT_TYPES.TURNAROUND:
      return a.turnaroundTemp
    case EVENT_TYPES.FC_START:
      return a.fcStartTemp
    case EVENT_TYPES.ROAST_END:
      return a.endTemp
    case EVENT_TYPES.HEATER_ADJUST:
    case EVENT_TYPES.FAN_ADJUST:
      return a.manualClicks[i]?.temp ?? null
    default:
      return null
  }
}

/** countdown 显示：remaining ≥ 0 → -mm:ss（倒计时）；< 0 → +mm:ss（已超时绝对值） */
export function formatCountdown(remaining: number): string {
  const abs = Math.abs(remaining)
  const str = formatMmSs(abs)
  return remaining >= 0 ? `-${str}` : `+${str}`
}

/** 相对回温时间显示：sec<0 → -mm:ss；≥0 → mm:ss */
export function formatRelTime(sec: number): string {
  const abs = Math.abs(sec)
  return sec < 0 ? `-${formatMmSs(abs)}` : formatMmSs(abs)
}

/** countdown 颜色类：>10 默认白；≥-10 绿；<-10 黄（spec §2.3） */
export function countdownColorClass(remaining: number): string {
  if (remaining > 10) return ''
  if (remaining >= -10) return 'cd-green'
  return 'cd-yellow'
}

function formatMmSs(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
