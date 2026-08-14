// CheckpointPanel — checkpoint 列表（纯展示）
//
// 布局（自上而下）：pass(es) 折叠区 → next 恒显(+countdown) → incoming 滚动区
// 数据由 App 经 deriveCheckpoints 派生后传入；本组件不计算达成/时间，仅渲染。
import type { CheckpointState } from '../checkpoint'
import { formatRelTime } from '../checkpoint'

interface CheckpointPanelProps {
  rows: CheckpointState[]
  /** 第一个未达成 checkpoint 的 index；null = 全部达成 */
  nextIndex: number | null
  /** next 的 countdown 显示文本（如 "-01:30"）；null = 不显示 */
  countdownText: string | null
  /** countdown 颜色类：''（默认白）| 'cd-green' | 'cd-yellow' */
  countdownColorClass: string
  expanded: boolean
  onToggleExpanded: () => void
  /** 点击 manual next 的 checkbox（仅 next 且 manual 时可用） */
  onManualCheck: (index: number) => void
}

export default function CheckpointPanel({
  rows,
  nextIndex,
  countdownText,
  countdownColorClass,
  expanded,
  onToggleExpanded,
  onManualCheck,
}: CheckpointPanelProps) {
  if (rows.length === 0) return null

  const passed = rows.filter((r) => r.achieved)
  const nextRow = nextIndex != null ? rows[nextIndex] : null
  const incoming = nextIndex != null ? rows.slice(nextIndex + 1) : []

  return (
    <div className="checkpoint-panel">
      <div className="checkpoint-header" onClick={onToggleExpanded}>
        <span className="checkpoint-collapse">{expanded ? '▼' : '▲'}</span>
        <span className="checkpoint-title">Checkpoints</span>
      </div>

      {expanded && (
        <div className="checkpoint-passed">
          {passed.length === 0 && <div className="checkpoint-empty">尚无已达成</div>}
          {passed.map((r) => (
            <PassedRow key={r.index} row={r} />
          ))}
        </div>
      )}

      {nextRow && (
        <div className="checkpoint-next">
          <button
            type="button"
            className="checkpoint-row checkpoint-next-row"
            disabled={nextRow.cp.type !== 'manual'}
            onClick={() => onManualCheck(nextRow.index)}
          >
            <span className="checkpoint-box">{nextRow.cp.type === 'manual' ? '☐' : '☒'}</span>
            <span className="checkpoint-event">{nextRow.cp.event}</span>
            {nextRow.cp.temp != null && <span className="checkpoint-temp">{nextRow.cp.temp}℃</span>}
            {nextRow.cp.value !== '' && <span className="checkpoint-value">({nextRow.cp.value})</span>}
          </button>
          {countdownText != null && (
            <span className={`checkpoint-countdown ${countdownColorClass}`}>{countdownText}</span>
          )}
        </div>
      )}

      {incoming.length > 0 && (
        <div className="checkpoint-incoming">
          {incoming.map((r) => (
            <div key={r.index} className="checkpoint-row checkpoint-incoming-row">
              <span className="checkpoint-box">{r.cp.type === 'manual' ? '☐' : '☒'}</span>
              <span className="checkpoint-event">{r.cp.event}</span>
              {r.cp.temp != null && <span className="checkpoint-temp">{r.cp.temp}℃</span>}
              {r.cp.value !== '' && <span className="checkpoint-value">({r.cp.value})</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** 已达成行：☑ <event> <理想temp>/<实际temp>℃ (<value>) <理想time>/<实际time> */
function PassedRow({ row }: { row: CheckpointState }) {
  const tempIdeal = row.cp.temp != null ? `${row.cp.temp}℃` : ''
  const tempActual = row.actualTemp != null ? `${row.actualTemp}℃` : ''
  const tempStr = tempIdeal && tempActual ? `${tempIdeal}/${tempActual}` : tempIdeal || tempActual

  const timeIdeal = row.idealRelSec != null ? formatRelTime(row.idealRelSec) : ''
  const timeActual = row.actualRelSec != null ? formatRelTime(row.actualRelSec) : '--:--'
  const showTime = row.idealRelSec != null || row.actualRelSec != null

  return (
    <div className="checkpoint-row checkpoint-passed-row">
      <span className="checkpoint-box">☑</span>
      <span className="checkpoint-event">{row.cp.event}</span>
      {tempStr !== '' && <span className="checkpoint-temp">{tempStr}</span>}
      {row.cp.value !== '' && <span className="checkpoint-value">({row.cp.value})</span>}
      {showTime && (
        <span className="checkpoint-time">
          {timeIdeal}
          {timeIdeal !== '' && timeActual !== '' && <span className="checkpoint-time-sep">/</span>}
          {timeActual}
        </span>
      )}
    </div>
  )
}
