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
  /** 入豆瞬间豆温快照（handleCharge 时记录，用于入豆温差）；Task B 接线前可缺失 */
  chargeTemp?: number | null
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
  /** 相对上一真实达成 checkpoint 的间隔偏差秒（正=提前，显示为 -mm:ss）；首条/未达成/被补齐为 null */
  deltaDiffSec: number | null
  /** 是否被 auto 补齐达成（manual 未点击被后方 auto 越过）；真实达成为 false */
  fabricated: boolean
}

export interface DerivedCheckpoints {
  rows: CheckpointState[]
  /** 第一个未达成 checkpoint 的 index；null = 全部达成 */
  nextIndex: number | null
  /** next 的 countdown 剩余秒（remaining）；null = 不显示（首条或已全达成） */
  remaining: number | null
}

export function deriveCheckpoints(checkpoints: Checkpoint[], anchors: AnchorInput, now: number): DerivedCheckpoints {
  // 1. 每行基本状态（锚点 / manualClicks 判定达成）
  const rows: CheckpointState[] = checkpoints.map((cp, i) => {
    const achievedAt = achievedAtOf(cp.event, anchors, i)
    return {
      index: i,
      cp,
      achieved: achievedAt != null,
      achievedAt,
      actualTemp: actualTempOf(cp.event, anchors, i),
      deltaDiffSec: null,
      fabricated: false,
    }
  })

  // 2. auto 自动补齐：auto 达成时，其前方未达成 manual 视为达成（时刻 = auto，无实际温度）
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].achieved || rows[i].cp.type !== 'manual') continue
    for (let j = i + 1; j < rows.length; j++) {
      if (rows[j].achieved && rows[j].cp.type !== 'manual') {
        rows[i].achieved = true
        rows[i].achievedAt = rows[j].achievedAt
        rows[i].fabricated = true
        break
      }
    }
  }

  // 3. delta 偏差：跳过被补齐 manual，用最近真实达成的理想位置差算预期间隔
  let idealPos = 0 // 当前行的理想位置（相对入豆，delta 累加）
  let prevRealIdeal = 0 // 最近真实达成行的理想位置
  let prevRealAt: number | null = null // 最近真实达成行的达成时刻
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i]
    if (i > 0) idealPos += checkpoints[i].delta ?? 0
    if (!r.achieved || r.achievedAt == null || r.fabricated) continue
    if (prevRealAt != null) {
      r.deltaDiffSec = idealPos - prevRealIdeal - (r.achievedAt - prevRealAt) / 1000
    }
    prevRealIdeal = idealPos
    prevRealAt = r.achievedAt
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
    case EVENT_TYPES.CHARGE:
      return a.chargeTemp ?? null
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

/** countdown 显示：remaining ≥ 0 → -mm:ss（倒计时）；< 0 → +mm:ss（已超时），统一 floor 取整 */
export function formatCountdown(remaining: number): string {
  // 统一向负无穷取整（floor）：-1.1→-2, -0.9→-1, 0.5→0, 1.1→1；0 代表正好到点
  const sec = Math.floor(remaining)
  const str = formatMmSs(Math.abs(sec))
  return remaining >= 0 ? `-${str}` : `+${str}`
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
