// 温度显示区：豆温 / 风温 / ROR 大数字（桌面端标签：豆温=temp1、风温=temp2、ROR）
import type { RoastState } from '../api'

const STATE_LABEL: Record<RoastState, string> = {
  idle: '未开始',
  waiting_charge: '等待入豆',
  roasting: '烘焙中',
}

// 温度显示格式：保留一位小数，null 显示 '--'
function formatTemperature(v: number | null): string {
  return v != null ? v.toFixed(1) : '--'
}

// 火力/风门为整数百分比（0-100），null 显示 '--'
function formatDial(v: number | null): string {
  return v != null ? String(Math.round(v)) : '--'
}

interface Props {
  temp1: number | null
  temp2: number | null
  ror: number | null
  state: RoastState | null
  heater: number | null
  fan: number | null
}

export default function TemperaturePanel({ temp1, temp2, ror, state, heater, fan }: Props) {
  return (
    <div className="temp-panel">
      <div className="temp-cell">
        <span className="temp-value temp1">{formatTemperature(temp1)}</span>
        <span className="temp-label">豆温 ℃</span>
      </div>
      <div className="temp-cell">
        <span className="temp-value temp2">{formatTemperature(temp2)}</span>
        <span className="temp-label">风温 ℃</span>
      </div>
      <div className="temp-cell">
        <span className="temp-value temp-ror">{formatTemperature(ror)}</span>
        <span className="temp-label">ROR ℃/min</span>
      </div>
      <div className="temp-state">{state ? STATE_LABEL[state] : '连接中…'}</div>
      <div className="temp-dials">
        <div className="temp-cell">
          <span className="temp-dial-value">{formatDial(heater)}</span>
          <span className="temp-label">火力 %</span>
        </div>
        <div className="temp-cell">
          <span className="temp-dial-value">{formatDial(fan)}</span>
          <span className="temp-label">风门 %</span>
        </div>
      </div>
    </div>
  )
}
