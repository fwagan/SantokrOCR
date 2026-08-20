// 火力/风门数值输入弹窗（add_value_event）
// 由 App 条件渲染（dialog 非 null 才挂载），每次打开组件重建 → 内部 state 天然归零
// 确认后由 App 发送 add_value_event（offset+value，offset 在点击时已冻结）
import { useEffect, useState } from 'react'
import { type ValueEventType } from '../api'

interface Props {
  title: ValueEventType
  /** 最近一次调整值（无调整时=入豆初始值），作为弹窗默认输入 */
  defaultValue: number
  /** 是否显示「视为 checkpoint 达成」checkbox（仅 next checkpoint 为对应 manual 事件时） */
  showCheckpointCheck: boolean
  onConfirm: (value: number, achieved: boolean) => void
  onClose: () => void
}

export default function ValueDialog({ title, defaultValue, showCheckpointCheck, onConfirm, onClose }: Props) {
  const [value, setValue] = useState('50')
  const [achieved, setAchieved] = useState(false)

  // 打开时填入最近一次调整值
  useEffect(() => {
    setValue(String(defaultValue))
  }, [defaultValue])

  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3 className="dialog-title">{title} %</h3>
        <input
          className="dialog-input"
          type="number"
          inputMode="decimal"
          min={0}
          max={100}
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        {showCheckpointCheck && (
          <label className="dialog-checkbox">
            <input type="checkbox" checked={achieved} onChange={(e) => setAchieved(e.target.checked)} />
            视为checkpoint达成
          </label>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={() => onConfirm(Number(value), achieved)}>
            确认
          </button>
        </div>
      </div>
    </div>
  )
}
