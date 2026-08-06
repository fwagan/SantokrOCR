// 计时器区：烘焙/回温/一爆/二爆 纵向排列
// 行数据由 App 依据计时锚点计算（回温修正、一爆冻结、二爆规则在 App 侧），本组件纯展示

export interface TimerRow {
  key: string
  label: string
  /** 当前应显示的秒数（已含修正/冻结逻辑） */
  seconds: number
}

function formatTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const m = Math.floor(s / 60)
  const ss = s % 60
  return `${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
}

interface Props {
  rows: TimerRow[]
}

export default function TimerPanel({ rows }: Props) {
  if (rows.length === 0) return null
  return (
    <div className="timer-panel">
      {rows.map((row) => (
        <div key={row.key} className="timer-row">
          <span className="timer-label">{row.label}</span>
          <span className="timer-value">{formatTime(row.seconds)}</span>
        </div>
      ))}
    </div>
  )
}
