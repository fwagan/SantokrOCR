// 火力/风门数值输入弹窗（add_value_event）
// title 非 null 时显示；确认后由 App 发送 add_value_event（offset+value，offset 在点击时已冻结）
// 默认值取最近一次调整结果（defaultValue，来自 App 的 lastHeater/lastFan）
import { useEffect, useState } from 'react'

interface Props {
  title: '调整火力' | '调整风门' | null
  /** 最近一次调整值（无调整时=入豆初始值），作为弹窗默认输入 */
  defaultValue: number
  onConfirm: (value: number) => void
  onClose: () => void
}

export default function ValueDialog({ title, defaultValue, onConfirm, onClose }: Props) {
  const [value, setValue] = useState('50')

  // 每次打开重置为最近一次调整值
  useEffect(() => {
    if (title) setValue(String(defaultValue))
  }, [title, defaultValue])

  if (!title) return null

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
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={() => onConfirm(Number(value))}>
            确认
          </button>
        </div>
      </div>
    </div>
  )
}
