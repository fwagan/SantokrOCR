// 温度显示区：豆温 / 风温 / ROR 大数字（桌面端标签：豆温=temp1、风温=temp2、ROR）
import type { RoastState } from '../api'

const STATE_LABEL: Record<RoastState, string> = {
  idle: '未开始',
  waiting_charge: '等待入豆',
  roasting: '烘焙中',
}

function fmt(v: number | null): string {
  return v != null ? v.toFixed(1) : '--'
}

interface Props {
  temp1: number | null
  temp2: number | null
  ror: number | null
  state: RoastState | null
}

export default function TemperaturePanel({ temp1, temp2, ror, state }: Props) {
  return (
    <div className="temp-panel">
      <div className="temp-cell">
        <span className="temp-value temp1">{fmt(temp1)}</span>
        <span className="temp-label">豆温 ℃</span>
      </div>
      <div className="temp-cell">
        <span className="temp-value temp2">{fmt(temp2)}</span>
        <span className="temp-label">风温 ℃</span>
      </div>
      <div className="temp-cell">
        <span className="temp-value temp-ror">{fmt(ror)}</span>
        <span className="temp-label">ROR ℃/min</span>
      </div>
      <div className="temp-state">{state ? STATE_LABEL[state] : '连接中…'}</div>
    </div>
  )
}
