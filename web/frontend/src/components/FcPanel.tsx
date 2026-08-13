// 一爆开始温度 + ΔT 显示栏（计时器下方，格式类似温度显示组件）
// ΔT = 当前实时豆温 − 一爆开始温度（一爆结束前动态）；一爆结束后冻结。
// 冻结区分不用文字标签：冻结时 ΔT 数值字体颜色从 ror 红切换为灰色（--text-dim）。

interface Props {
  fcStartTemp: number | null
  deltaT: number | null
  /** 一爆已结束：ΔT 冻结，数值变灰 */
  frozen: boolean
}

// 温度显示格式：保留一位小数，null 显示 '--'
function formatTemperature(v: number | null): string {
  return v != null ? v.toFixed(1) : '--'
}

export default function FcPanel({ fcStartTemp, deltaT, frozen }: Props) {
  return (
    <div className="temp-panel fc-panel">
      <div className="temp-cell">
        <span className="temp-value temp1">{formatTemperature(fcStartTemp)}</span>
        <span className="temp-label">一爆开始 ℃</span>
      </div>
      <div className="temp-cell">
        <span className={`temp-value temp-ror${frozen ? ' temp-frozen' : ''}`}>
          {formatTemperature(deltaT)}
        </span>
        <span className="temp-label">ΔT ℃</span>
      </div>
    </div>
  )
}
