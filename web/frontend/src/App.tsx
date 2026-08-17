// 顶层编排：轮询调度 + 按钮状态机 + T0/计时锚点管理
//
// T0 语义（Phase 3 设计确认）：
// - T0 = 点击入豆瞬间的 UTC 时刻；事件 offset = floor((now−T0)/1000)
// - 点击入豆即乐观设置 T0 并起计时；start 失败（ok:false / 传输错误）回退 T0 归零
// - 回温计时锚点 = T0 + turnaround_offset（服务端 offset 为相对入豆的秒数，轮询延迟自动修正）
// - 一爆/二爆锚点 = T0 + 点击时 offset；end ok 后 ended=true 冻结全部计时器
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import {
  EVENT_TYPES,
  type AddEventPayload,
  type AddValueEventPayload,
  type Checkpoint,
  type EndPayload,
  type EventCommand,
  type RoastState,
  type StartPayload,
} from './api'
import { getCheckpoints, getTemp, postEvent } from './api'
import { useNow, useStatus } from './hooks'
import { clearSession, readSession, writeSession } from './session'
import { countdownColorClass, deriveCheckpoints, formatCountdown } from './checkpoint'
import CheckpointPanel from './components/CheckpointPanel'
import ChargeForm from './components/ChargeForm'
import EventButtons from './components/EventButtons'
import FcPanel from './components/FcPanel'
import StatusBanner from './components/StatusBanner'
import TemperaturePanel from './components/TemperaturePanel'
import TimerPanel, { type TimerRow } from './components/TimerPanel'
import ValueDialog from './components/ValueDialog'
import './App.css'

// get_temp 延迟查询毫秒数：等主进程 resample（~1s 节流重建）追上，保证查询点数据一致
const TEMP_QUERY_DELAY_MS = 2000

/**
 * 调度一次延迟温度查询（普通函数，非 hook）：delayMs 后查 offset 时刻温度，结果非 null 时回调。
 * t0Ref 会话守卫：查询在途期间发生新入豆/重置（t0 变化）则丢弃过期结果。
 * 返回 cancel 函数用于清理定时器（effect 场景调用方在 cleanup 中调用）。
 * 一次性动作，无状态残留——结果由 onResult 直接消费。
 */
function scheduleDelayedTempQuery(
  anchor: number,
  t0: number,
  t0Ref: RefObject<number | null>,
  onResult: (temp: number) => void,
  delayMs = TEMP_QUERY_DELAY_MS,
): () => void {
  const timer = setTimeout(() => {
    getTemp((anchor - t0) / 1000).then((t) => {
      if (t0Ref.current !== t0) return // 会话已变，丢弃过期结果
      if (t != null) onResult(t)
    })
  }, delayMs)
  return () => clearTimeout(timer)
}

export default function App() {
  const { status, error: pollError } = useStatus()

  const [heater, setHeater] = useState('50')
  const [fan, setFan] = useState('50')
  const [t0, setT0] = useState<number | null>(null) // 入豆点击 UTC ms（乐观设置，失败归零）
  const [ended, setEnded] = useState(false) // end ok 后冻结计时器并禁按钮
  const [turnaroundStart, setTurnaroundStart] = useState<number | null>(null)
  const [fcStart, setFcStart] = useState<number | null>(null)
  const [fcEnd, setFcEnd] = useState<number | null>(null)
  const [scStart, setScStart] = useState<number | null>(null)
  const [scEnd, setScEnd] = useState<number | null>(null)
  const [busy, setBusy] = useState(false) // 有请求在途（含重试）时防连点
  const [toast, setToast] = useState<string | null>(null)
  // 火力/风门弹窗：offset 在点击调整按钮瞬间冻结（非发送时），值默认取最近一次调整结果
  const [dialog, setDialog] = useState<{
    type: '调整火力' | '调整风门'
    offset: number
  } | null>(null)
  const [lastHeater, setLastHeater] = useState(50) // 最近一次火力值（默认初始 50）
  const [lastFan, setLastFan] = useState(50)
  // 历史时刻豆温（get_temp 查询结果）：回温温度、一爆开始温度；fcDeltaT 为一爆后冻结值
  const [turnaroundTemp, setTurnaroundTemp] = useState<number | null>(null)
  const [fcStartTemp, setFcStartTemp] = useState<number | null>(null)
  const [fcDeltaT, setFcDeltaT] = useState<number | null>(null)
  // checkpoint（Phase 2+3）：理想曲线静态列表 + 前端自治达成状态
  const [checkpoints, setCheckpoints] = useState<Checkpoint[] | null>(null)
  const [cachedCurveName, setCachedCurveName] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [manualClicks, setManualClicks] = useState<Record<number, { at: number; temp: number | null }>>({})
  const [endTime, setEndTime] = useState<number | null>(null)
  const [endTemp, setEndTemp] = useState<number | null>(null)
  const [chargeTemp, setChargeTemp] = useState<number | null>(null) // 入豆瞬间豆温快照（入豆温差）
  const turnaroundQueryRef = useRef(false) // 本会话是否已查过回温温度（防重复/StrictMode 双跑）
  // 一爆开始/结束的修正查询由事件触发（handleAddEvent 内直接调度），无需 queryRef
  const liveTempRef = useRef<number | null>(null) // 最新轮询豆温（冻结瞬间取快照）
  liveTempRef.current = status?.temp1 ?? null
  // 会话标识：入豆 UTC ms 在会话内恒定，供 scheduleDelayedTempQuery 丢弃跨会话过期结果
  const t0Ref = useRef<number | null>(null)
  t0Ref.current = t0
  // getCheckpoints 在途守卫：防后端掉线时曲线拉取请求叠加（与 getStatus 串行轮询语义对齐）
  const checkpointsFetchingRef = useRef(false)

  const roastActive = t0 != null && !ended
  const now = useNow(roastActive)

  // toast 3s 自动消失
  useEffect(() => {
    if (!toast) return
    const id = setTimeout(() => setToast(null), 3000)
    return () => clearTimeout(id)
  }, [toast])

  // 回温锚点：收到 offset 首次设置（>=0 才生效），T0 + offset 自动修正轮询延迟
  useEffect(() => {
    if (t0 == null) return
    const off = status?.turnaround_offset
    if (off != null && off >= 0 && turnaroundStart == null) {
      setTurnaroundStart(t0 + off * 1000)
    }
  }, [status, t0, turnaroundStart])

  // 回温温度：turnaroundStart 首次非 null 时延迟查询（状态驱动 → effect 内调度）
  // queryRef 防重复（StrictMode 双跑只查一次），cleanup 清理定时器
  useEffect(() => {
    if (t0 == null || turnaroundStart == null) return
    if (turnaroundQueryRef.current) return
    turnaroundQueryRef.current = true
    const anchor = turnaroundStart
    const t0AtSchedule = t0
    const cancel = scheduleDelayedTempQuery(anchor, t0AtSchedule, t0Ref, (temp) => {
      setTurnaroundTemp(temp)
    })
    return () => {
      cancel()
      turnaroundQueryRef.current = false
    }
  }, [t0, turnaroundStart, t0Ref, turnaroundQueryRef])

  // ΔT 冻结：fcEnd 或 ended 首次出现时用最近轮询豆温冻结一次（幂等，函数式更新）
  // - ended 也算冻结触发：一爆开始后、一爆结束前手动 end（fcEnd 为 null）也应冻结
  // - 依赖 status?.temp1：冻结瞬间若轮询断档（temp1=null），等下一轮恢复后再冻
  // - fcDeltaT != null 守卫：恢复会话已持久化冻结值时跳过，避免用"当前"豆温错误重冻
  useEffect(() => {
    if ((fcEnd == null && !ended) || fcStartTemp == null || fcDeltaT != null) return
    const live = liveTempRef.current
    if (live != null) setFcDeltaT((prev) => prev ?? live - fcStartTemp)
  }, [fcEnd, ended, fcStartTemp, fcDeltaT, status?.temp1])

  // 重置烘焙会话的计时锚点/温度/查询防重（t0 与火力/风门由调用方单独处理）
  // setter 与 ref 稳定，useCallback 依赖 [] 即可，便于在 effect 中作为稳定依赖引用
  const resetRoastState = useCallback(() => {
    setTurnaroundStart(null)
    setFcStart(null)
    setFcEnd(null)
    setScStart(null)
    setScEnd(null)
    setEnded(false)
    setTurnaroundTemp(null)
    setFcStartTemp(null)
    setFcDeltaT(null)
    setManualClicks({})
    setEndTime(null)
    setEndTemp(null)
    setChargeTemp(null)
    turnaroundQueryRef.current = false
  }, [])

  // 新会话重置：任何"进入 waiting_charge"的状态转换（含黑障/后台错过 idle→waiting_charge 边沿）
  const prevStateRef = useRef<RoastState | null>(null)
  useEffect(() => {
    const prev = prevStateRef.current
    const cur = status?.state ?? null
    if (cur === 'waiting_charge' && prev !== 'waiting_charge') {
      setT0(null)
      resetRoastState()
      clearSession() // 新烘焙会话，清掉旧会话残留
    }
    // M1 兜底：主进程已停止（end 已处理但响应丢失/黑障），冻结计时器避免无限空转
    if (prev === 'roasting' && cur === 'idle' && t0 != null) {
      setEnded(true)
    }
    prevStateRef.current = cur
  }, [status, t0, resetRoastState])

  // 非 roasting 状态下，curve_name 变化时拉取 checkpoint 列表覆盖缓存
  useEffect(() => {
    if (status == null || status.state === 'roasting') return
    const name = status.curve_name
    if (!name) {
      // 服务端无理想曲线（未加载或已清除）：清空本地缓存
      setCheckpoints(null)
      setCachedCurveName('')
      return
    }
    if (name === cachedCurveName || checkpointsFetchingRef.current) return
    checkpointsFetchingRef.current = true
    getCheckpoints().then((cps) => {
      checkpointsFetchingRef.current = false
      if (cps == null) return // 网络失败：保持旧缓存与缓存名，下轮轮询自然重试
      setCheckpoints(cps)
      setCachedCurveName(name)
    })
  }, [status, cachedCurveName])

  // 恢复持久化会话：误触刷新后，若主进程仍在 roasting 且有未结束会话 → 恢复 T0 与计时锚点
  const restoreCheckedRef = useRef(false)
  useEffect(() => {
    if (restoreCheckedRef.current || status == null) return
    restoreCheckedRef.current = true
    const s = readSession()
    if (status.state === 'roasting' && s != null && !s.ended) {
      setT0(s.t0)
      setTurnaroundStart(s.turnaroundStart)
      setFcStart(s.fcStart)
      setFcEnd(s.fcEnd)
      setScStart(s.scStart)
      setScEnd(s.scEnd)
      setLastHeater(s.lastHeater)
      setLastFan(s.lastFan)
      setFcStartTemp(s.fcStartTemp ?? null)
      setFcDeltaT(s.fcDeltaT ?? null)
      setTurnaroundTemp(s.turnaroundTemp ?? null)
      setChargeTemp(s.chargeTemp ?? null)
      setManualClicks(s.manualClicks ?? {})
      setCheckpoints((s.checkpoints as Checkpoint[] | null) ?? null)
      setCachedCurveName(s.cachedCurveName ?? '')
      // 恢复的会话：fcStartTemp/fcDeltaT 已持久化则直接显示（事件驱动查询天然不重查）；
      // fcDeltaT 未持久化（fcEnd 点击时轮询断档、冻结未完成）→ 补一次一爆结束修正查询
      if (s.fcEnd != null && s.fcDeltaT == null && s.fcStartTemp != null) {
        const fcStartTemp = s.fcStartTemp
        scheduleDelayedTempQuery(s.fcEnd, s.t0, t0Ref, (temp) => {
          setFcDeltaT(temp - fcStartTemp)
        })
      }
      setToast('已恢复烘焙会话')
    } else {
      clearSession() // 无有效会话（首次进入 / 非 roasting），清理残留
    }
  }, [status])

  // 持久化会话：t0 有效时随锚点/火力变化写入，供误触刷新恢复
  useEffect(() => {
    if (t0 == null) return
    writeSession({
      t0,
      turnaroundStart,
      fcStart,
      fcEnd,
      scStart,
      scEnd,
      ended,
      lastHeater,
      lastFan,
      fcStartTemp,
      fcDeltaT,
      turnaroundTemp,
      chargeTemp,
      manualClicks,
      checkpoints,
      cachedCurveName,
      savedAt: Date.now(),
    })
  }, [
    t0,
    turnaroundStart,
    fcStart,
    fcEnd,
    scStart,
    scEnd,
    ended,
    lastHeater,
    lastFan,
    turnaroundTemp,
    chargeTemp,
    fcStartTemp,
    fcDeltaT,
    manualClicks,
    checkpoints,
    cachedCurveName,
  ])

  // 统一提交：冻结 payload（offset 在点击瞬间算好）→ postEvent 内部重试
  // 200+ok:false 业务拒绝 / 传输失败 均在 postEvent 层处理；这里只做 UI 反馈
  const submitCommand = async (payload: EventCommand, onOk: () => void, failLabel: string, onError?: () => void) => {
    setBusy(true)
    try {
      const resp = await postEvent(payload)
      if (resp.ok) {
        onOk()
      } else {
        setToast(`${failLabel}失败：${resp.error ?? '未知原因'}`)
        onError?.()
      }
    } catch (e) {
      setToast(`${failLabel}失败：${e instanceof Error ? e.message : String(e)}`)
      onError?.()
    } finally {
      setBusy(false)
    }
  }

  const handleCharge = async () => {
    if (busy) return
    const h = Number(heater)
    const f = Number(fan)
    if (!Number.isFinite(h) || !Number.isFinite(f) || h < 0 || h > 100 || f < 0 || f > 100) {
      setToast('火力/风门需为 0-100 的数字')
      return
    }
    const chargeT0 = Date.now() // 点击瞬间 UTC（点击就动，失败再归零）
    setT0(chargeT0)
    setLastHeater(h) // 入豆初始值即当前火力/风门，作为后续弹窗默认基准
    setLastFan(f)
    resetRoastState() // 先清空旧会话（含 chargeTemp null）
    setChargeTemp(liveTempRef.current) // 再快照入豆豆温（避免被 reset 覆盖）
    setBusy(true)
    try {
      const payload: StartPayload = { cmd: 'start', heater_initial: h, fan_initial: f }
      const resp = await postEvent(payload)
      if (!resp.ok) {
        setT0(null) // 业务拒绝（流程问题）：归零，表单回来
        clearSession()
        setToast(`入豆失败：${resp.error ?? '未知原因'}`)
      }
    } catch (e) {
      setT0(null) // 网络/超时/5xx 全部重试失败：归零
      clearSession()
      setToast(`入豆失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const handleAddEvent = (type: string) => {
    if (t0 == null || busy) return
    const base = t0
    const offset = (Date.now() - base) / 1000 // 点击瞬间冻结（ms 精度，不取整）
    const payload: AddEventPayload = { cmd: 'add_event', event: { type, offset } }
    submitCommand(
      payload,
      () => {
        const anchor = base + offset * 1000
        if (type === EVENT_TYPES.FC_START) {
          setFcStart(anchor)
          // 本地缓存兜底：点击瞬间的当前豆温立即显示，2s 后查询修正
          setFcStartTemp(liveTempRef.current)
          scheduleDelayedTempQuery(anchor, base, t0Ref, (temp) => {
            setFcStartTemp(temp) // 修正为准确值
          })
        } else if (type === EVENT_TYPES.FC_END) {
          setFcEnd(anchor)
          // 冻结由 effect 触发（fcEnd 非 null）；2s 后查询一爆结束时刻温度修正冻结值
          if (fcStartTemp != null) {
            scheduleDelayedTempQuery(anchor, base, t0Ref, (temp) => {
              setFcDeltaT(temp - fcStartTemp) // 修正冻结值
            })
          }
        } else if (type === EVENT_TYPES.SC_START) setScStart(anchor)
        else if (type === EVENT_TYPES.SC_END) setScEnd(anchor)
      },
      `记录${type}`,
    )
  }

  const openValueDialog = (type: '调整火力' | '调整风门') => {
    if (t0 == null || busy) return
    // offset 在点击调整按钮瞬间冻结（ms 精度）；用户确认输入期间的耗时不计入事件时间
    setDialog({ type, offset: (Date.now() - t0) / 1000 })
  }

  const handleValueEvent = (dlg: { type: '调整火力' | '调整风门'; offset: number }, value: number) => {
    if (t0 == null || busy) return
    setDialog(null)
    if (!Number.isFinite(value) || value < 0 || value > 100) {
      setToast(`${dlg.type}需为 0-100 的数字`)
      return
    }
    const payload: AddValueEventPayload = {
      cmd: 'add_value_event',
      event: { type: dlg.type, offset: dlg.offset, value },
    }
    submitCommand(
      payload,
      () => {
        // 调整成功后缓存最新值，作为下次弹窗默认值
        if (dlg.type === '调整火力') setLastHeater(value)
        else setLastFan(value)
      },
      `记录${dlg.type}`,
    )
  }

  const handleEnd = () => {
    if (t0 == null || busy) return
    const base = t0
    const offset = (Date.now() - base) / 1000 // ms 精度
    // 达成时刻以前端点击瞬间为准（乐观），不等待 app 回复；失败回退
    const endAt = Date.now()
    setEndTime(endAt)
    setEndTemp(liveTempRef.current)
    const payload: EndPayload = { cmd: 'end', event: { type: EVENT_TYPES.ROAST_END, offset } }
    submitCommand(
      payload,
      () => setEnded(true),
      '记录烘焙结束',
      () => {
        setEndTime(null)
        setEndTemp(null)
      },
    )
  }

  const handleManualCheck = (index: number) => {
    if (derived == null || derived.nextIndex !== index) return
    const row = derived.rows[index]
    if (row == null || row.achieved || row.cp.type !== 'manual') return
    setManualClicks((prev) => ({ ...prev, [index]: { at: Date.now(), temp: liveTempRef.current } }))
  }

  // ── 计时器行数据（由锚点计算，TimerPanel 纯展示） ──
  const roastSeconds = t0 != null ? (now - t0) / 1000 : 0
  const turnaroundSeconds = turnaroundStart != null ? (now - turnaroundStart) / 1000 : null
  const fcSeconds =
    fcStart != null
      ? fcEnd != null
        ? (fcEnd - fcStart) / 1000 // 一爆结束后冻结
        : (now - fcStart) / 1000
      : null
  const scSeconds =
    fcEnd != null
      ? scStart != null
        ? scEnd != null
          ? (scEnd - scStart) / 1000
          : (now - scStart) / 1000
        : 0 // 一爆结束、二爆未开始：显示 00:00
      : null

  const rows: TimerRow[] = [{ key: 'roast', label: '烘焙计时', seconds: roastSeconds }]
  if (turnaroundSeconds != null) {
    rows.push({
      key: 'turnaround',
      label: '回温计时',
      seconds: turnaroundSeconds,
      temp: turnaroundTemp,
    })
  }
  if (fcStart != null) {
    rows.push({ key: 'fc', label: '一爆计时', seconds: fcSeconds ?? 0 })
  }
  if (fcEnd != null) {
    rows.push({ key: 'sc', label: '二爆计时', seconds: scSeconds ?? 0 })
  }

  // ΔT 显示值：一爆结束前动态（当前实时豆温 − 一爆开始温度），一爆结束后冻结
  const liveTemp = status?.temp1 ?? null
  const deltaTFrozen = fcEnd != null || ended
  const liveDeltaT = liveTemp != null && fcStartTemp != null ? liveTemp - fcStartTemp : null
  const displayedDeltaT = deltaTFrozen ? fcDeltaT : liveDeltaT

  const chargeEnabled = status?.state === 'waiting_charge' && !busy
  const buttonsEnabled = status?.state === 'roasting' && t0 != null && !ended && !busy
  const midRoastReload = status?.state === 'roasting' && t0 == null
  // 线性事件按钮：按已记录锚点派生当前应显示的"下一个"（一爆开始→一爆结束→二爆开始→二爆结束）
  const nextCrack =
    scEnd != null
      ? null
      : scStart != null
        ? EVENT_TYPES.SC_END
        : fcEnd != null
          ? EVENT_TYPES.SC_START
          : fcStart != null
            ? EVENT_TYPES.FC_END
            : EVENT_TYPES.FC_START

  // ── checkpoint 派生：达成/时间/countdown（达成状态前端自治） ──
  const derived = checkpoints
    ? deriveCheckpoints(
        checkpoints,
        {
          t0,
          turnaroundStart,
          turnaroundTemp,
          fcStart,
          fcEnd,
          scStart,
          scEnd,
          endTime,
          endTemp,
          fcStartTemp,
          manualClicks,
          chargeTemp,
        },
        now,
      )
    : null
  const nextCountdownText = derived != null && derived.remaining != null ? formatCountdown(derived.remaining) : null
  const nextCountdownColor = derived != null && derived.remaining != null ? countdownColorClass(derived.remaining) : ''

  return (
    <div className="app">
      <StatusBanner error={pollError} toast={toast} busy={busy} />
      {midRoastReload && <div className="banner banner-warn">烘焙进程由其他终端控制，当前终端事件标记不可用</div>}

      {t0 == null ? (
        <ChargeForm
          heater={heater}
          fan={fan}
          disabled={!chargeEnabled}
          busy={busy}
          onHeater={setHeater}
          onFan={setFan}
          onCharge={handleCharge}
        />
      ) : (
        <TimerPanel rows={rows} />
      )}

      <TemperaturePanel
        temp1={status?.temp1 ?? null}
        temp2={status?.temp2 ?? null}
        ror={status?.ror ?? null}
        state={status?.state ?? null}
        heater={lastHeater}
        fan={lastFan}
      />

      {fcStart != null && <FcPanel fcStartTemp={fcStartTemp} deltaT={displayedDeltaT} frozen={deltaTFrozen} />}

      {t0 != null && (
        <EventButtons
          disabled={!buttonsEnabled}
          ended={ended}
          endDisabled={turnaroundStart == null && fcStart == null}
          nextCrack={nextCrack}
          onCrack={() => {
            if (nextCrack != null) handleAddEvent(nextCrack)
          }}
          onHeater={() => openValueDialog('调整火力')}
          onFan={() => openValueDialog('调整风门')}
          onEnd={handleEnd}
        />
      )}

      {derived != null && derived.rows.length > 0 && (
        <CheckpointPanel
          rows={derived.rows}
          nextIndex={derived.nextIndex}
          countdownText={nextCountdownText}
          countdownColorClass={nextCountdownColor}
          expanded={expanded}
          onToggleExpanded={() => setExpanded((v) => !v)}
          onManualCheck={handleManualCheck}
        />
      )}

      <ValueDialog
        title={dialog?.type ?? null}
        defaultValue={dialog?.type === '调整火力' ? lastHeater : lastFan}
        onConfirm={(v) => {
          if (dialog) handleValueEvent(dialog, v)
        }}
        onClose={() => setDialog(null)}
      />
    </div>
  )
}
