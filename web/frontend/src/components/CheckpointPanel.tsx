// CheckpointPanel — checkpoint 列表（纯展示）
//
// 布局（自上而下）：pass(es) 折叠区 → next 恒显(+countdown) → incoming 滚动区
// 数据由 App 经 deriveCheckpoints 派生后传入；本组件不计算达成/时间，仅渲染。
import type { CheckpointState } from '../checkpoint'
import { formatCountdown } from '../checkpoint'
import { EVENT_TYPES } from '../api'

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
            <span className={boxClass(nextRow, true)} />
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
              <span className={boxClass(r, false)} />
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

/** 已达成行：☑ 事件（value） 理想温度（温差） 时间（相对上一真实达成间隔偏差，正=提前） */
function PassedRow({ row }: { row: CheckpointState }) {
  const tempIdeal = row.cp.temp != null ? `${row.cp.temp}℃` : ''
  const tempDiff =
    row.actualTemp != null && row.cp.temp != null
      ? `（${row.actualTemp - row.cp.temp > 0 ? '+' : ''}${(row.actualTemp - row.cp.temp).toFixed(1)}℃）`
      : ''
  const tempStr = `${tempIdeal}${tempDiff}`

  // 时间 = 相对上一真实达成 checkpoint 的间隔偏差（正=提前，显示为 -mm:ss）；入豆不显示
  const timeStr = row.deltaDiffSec != null ? formatCountdown(row.deltaDiffSec) : null
  const showTime = timeStr != null && row.cp.event !== EVENT_TYPES.CHARGE

  // value 仅 manual（调整火力/风门）显示；入豆不显示
  const valueStr = row.cp.type === 'manual' && row.cp.value !== '' ? `(${row.cp.value})` : ''

  return (
    <div className="checkpoint-row checkpoint-passed-row">
      <span className={boxClass(row, false)} />
      <span className="checkpoint-event">{row.cp.event}</span>
      {valueStr !== '' && <span className="checkpoint-value">{valueStr}</span>}
      {tempStr !== '' && <span className="checkpoint-temp">{tempStr}</span>}
      {showTime && <span className="checkpoint-time">{timeStr}</span>}
    </div>
  )
}

/** checkbox 类：真实达成=绿✓；被补齐=黄✓；next=蓝（manual空/auto短横）；incoming=灰（manual空/auto短横） */
function boxClass(row: CheckpointState, isNext: boolean): string {
  if (row.achieved && row.fabricated) return 'checkpoint-box box-fabricated box-check'
  if (row.achieved) return 'checkpoint-box box-done box-check'
  const state = isNext ? 'box-next' : 'box-incoming'
  const mark = row.cp.type === 'manual' ? '' : 'box-dash'
  return `checkpoint-box ${state} ${mark}`.trim()
}
