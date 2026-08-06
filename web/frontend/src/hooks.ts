// 通用 hooks：useStatus（轮询状态）+ useNow（计时器驱动）
import { useEffect, useState } from 'react'
import { getStatus, type Status } from './api'

const POLL_INTERVAL_MS = 1000

/**
 * 轮询 GET /api/status
 *
 * 失败时保留最后一次成功状态（温度不闪空），error 供 StatusBanner 提示连接中断。
 * 轮询天然自带重试（下一轮就是重试），无需额外重试逻辑。
 *
 * 串行排程（L5）：用递归 setTimeout 而非 setInterval——上一轮完成后再排下一轮，
 * 避免慢网络下多个请求并发重叠、旧响应后到覆盖新状态。
 */
export function useStatus(): { status: Status | null; error: string | null } {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    async function poll() {
      if (cancelled) return
      try {
        const s = await getStatus()
        if (!cancelled) {
          setStatus(s)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
      if (!cancelled) timer = setTimeout(poll, POLL_INTERVAL_MS)
    }

    poll()
    return () => {
      cancelled = true
      if (timer != null) clearTimeout(timer)
    }
  }, [])

  return { status, error }
}

/**
 * 计时器驱动：active 期间刷新 now（ms）。
 *
 * tick 用 100ms 而非 1000ms：各计时器按 floor((now − 锚点)/1000) 显示，
 * 若 tick 过粗（1s 且对齐到入豆时刻），所有计时器会在同一 tick 跳秒——
 * 即使锚点未对齐整秒也"锁步"跳动。细 tick 让每个计时器在自身真实秒边界
 * （锚点 + N 秒，±100ms 内）独立跳秒。
 *
 * active 置 false（如 end 后）停 tick，now 保留末值 → 各计时器冻结。
 */
export function useNow(active: boolean): number {
  const [now, setNow] = useState<number>(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const id = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(id)
  }, [active])
  return now
}
