// 事件按钮区：按 state 禁用（enabled 由 App 按 roasting+未结束+非发送中 计算）
// 布局（联调确认）：
//   L1 调整火力 / 调整风门（add_value_event，弹窗）
//   L2 线性事件按钮之一（一爆开始/结束、二爆开始/结束 依次只显示下一个；add_event）
//   L3 烘焙结束（end）
interface Props {
  enabled: boolean
  ended: boolean
  /** 当前应显示的线性事件类型（一爆开始/结束、二爆开始/结束）；null=四个已完成 */
  nextCrack: string | null
  onCrack: () => void
  onHeater: () => void
  onFan: () => void
  onEnd: () => void
}

export default function EventButtons({
  enabled,
  ended,
  nextCrack,
  onCrack,
  onHeater,
  onFan,
  onEnd,
}: Props) {
  return (
    <div className="event-buttons">
      {ended && <div className="ended-note">烘焙已结束</div>}
      <div className="btn-grid">
        <button type="button" className="btn btn-warn" onClick={onHeater} disabled={!enabled}>
          调整火力
        </button>
        <button type="button" className="btn btn-warn" onClick={onFan} disabled={!enabled}>
          调整风门
        </button>
        {nextCrack != null && (
          <button type="button" className="btn btn-crack btn-wide" onClick={onCrack} disabled={!enabled}>
            {nextCrack}
          </button>
        )}
        <button type="button" className="btn btn-danger btn-wide" onClick={onEnd} disabled={!enabled}>
          烘焙结束
        </button>
      </div>
    </div>
  )
}
