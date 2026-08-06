// 顶层编排：轮询调度 + 按钮状态机 + T0/计时锚点管理
//
// T0 语义（Phase 3 设计确认）：
// - T0 = 点击入豆瞬间的 UTC 时刻；事件 offset = floor((now−T0)/1000)
// - 点击入豆即乐观设置 T0 并起计时；start 失败（ok:false / 传输错误）回退 T0 归零
// - 回温计时锚点 = T0 + turnaround_offset（服务端 offset 为相对入豆的秒数，轮询延迟自动修正）
// - 一爆/二爆锚点 = T0 + 点击时 offset；end ok 后 ended=true 冻结全部计时器
import { useEffect, useRef, useState } from 'react'
import {
  EVENT_TYPES,
  type AddEventPayload,
  type AddValueEventPayload,
  type EndPayload,
  type EventCommand,
  type RoastState,
  type StartPayload,
} from './api'
import { postEvent } from './api'
import { useNow, useStatus } from './hooks'
import { clearSession, readSession, writeSession } from './session'
import ChargeForm from './components/ChargeForm'
import EventButtons from './components/EventButtons'
import StatusBanner from './components/StatusBanner'
import TemperaturePanel from './components/TemperaturePanel'
import TimerPanel, { type TimerRow } from './components/TimerPanel'
import ValueDialog from './components/ValueDialog'
import './App.css'

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

  // 新会话重置：任何"进入 waiting_charge"的状态转换（含黑障/后台错过 idle→waiting_charge 边沿）
  const prevStateRef = useRef<RoastState | null>(null)
  useEffect(() => {
    const prev = prevStateRef.current
    const cur = status?.state ?? null
    if (cur === 'waiting_charge' && prev !== 'waiting_charge') {
      setT0(null)
      setTurnaroundStart(null)
      setFcStart(null)
      setFcEnd(null)
      setScStart(null)
      setScEnd(null)
      setEnded(false)
      clearSession() // 新烘焙会话，清掉旧会话残留
    }
    // M1 兜底：主进程已停止（end 已处理但响应丢失/黑障），冻结计时器避免无限空转
    if (prev === 'roasting' && cur === 'idle' && t0 != null) {
      setEnded(true)
    }
    prevStateRef.current = cur
  }, [status, t0])

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
      savedAt: Date.now(),
    })
  }, [t0, turnaroundStart, fcStart, fcEnd, scStart, scEnd, ended, lastHeater, lastFan])

  // 统一提交：冻结 payload（offset 在点击瞬间算好）→ postEvent 内部重试
  // 200+ok:false 业务拒绝 / 传输失败 均在 postEvent 层处理；这里只做 UI 反馈
  const submitCommand = async (
    payload: EventCommand,
    onOk: () => void,
    failLabel: string,
  ) => {
    setBusy(true)
    try {
      const resp = await postEvent(payload)
      if (resp.ok) {
        onOk()
      } else {
        setToast(`${failLabel}失败：${resp.error ?? '未知原因'}`)
      }
    } catch (e) {
      setToast(`${failLabel}失败：${e instanceof Error ? e.message : String(e)}`)
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
    setTurnaroundStart(null)
    setFcStart(null)
    setFcEnd(null)
    setScStart(null)
    setScEnd(null)
    setEnded(false)
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
        if (type === EVENT_TYPES.FC_START) setFcStart(anchor)
        else if (type === EVENT_TYPES.FC_END) setFcEnd(anchor)
        else if (type === EVENT_TYPES.SC_START) setScStart(anchor)
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

  const handleValueEvent = (
    dlg: { type: '调整火力' | '调整风门'; offset: number },
    value: number,
  ) => {
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
    const payload: EndPayload = { cmd: 'end', event: { type: EVENT_TYPES.ROAST_END, offset } }
    submitCommand(payload, () => setEnded(true), '记录烘焙结束')
  }

  // ── 计时器行数据（由锚点计算，TimerPanel 纯展示） ──
  const roastSeconds = t0 != null ? (now - t0) / 1000 : 0
  const turnaroundSeconds =
    turnaroundStart != null ? (now - turnaroundStart) / 1000 : null
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
    rows.push({ key: 'turnaround', label: '回温计时', seconds: turnaroundSeconds })
  }
  if (fcStart != null) {
    rows.push({ key: 'fc', label: '一爆计时', seconds: fcSeconds ?? 0 })
  }
  if (fcEnd != null) {
    rows.push({ key: 'sc', label: '二爆计时', seconds: scSeconds ?? 0 })
  }

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

  return (
    <div className="app">
      <StatusBanner error={pollError} toast={toast} busy={busy} />
      {midRoastReload && (
        <div className="banner banner-warn">烘焙进程由其他终端控制，当前终端事件标记不可用</div>
      )}

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
      />

      {t0 != null && (
        <EventButtons
          enabled={buttonsEnabled}
          ended={ended}
          nextCrack={nextCrack}
          onCrack={() => {
            if (nextCrack != null) handleAddEvent(nextCrack)
          }}
          onHeater={() => openValueDialog('调整火力')}
          onFan={() => openValueDialog('调整风门')}
          onEnd={handleEnd}
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
