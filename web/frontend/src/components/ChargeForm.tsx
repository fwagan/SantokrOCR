// 入豆前表单：初始火力/风门输入（默认 50/50）+ 入豆按钮
// 点击入豆成功后整个表单隐藏（由 App 控制渲染）
interface Props {
  heater: string
  fan: string
  /** 入豆按钮是否禁用（state != waiting_charge 时禁用） */
  disabled: boolean
  busy: boolean
  onHeater: (v: string) => void
  onFan: (v: string) => void
  onCharge: () => void
}

export default function ChargeForm({
  heater,
  fan,
  disabled,
  busy,
  onHeater,
  onFan,
  onCharge,
}: Props) {
  return (
    <div className="charge-form">
      <div className="charge-row">
        <label className="charge-field">
          <span>初始火力 %</span>
          <input
            type="number"
            inputMode="decimal"
            min={0}
            max={100}
            value={heater}
            onChange={(e) => onHeater(e.target.value)}
            disabled={busy}
          />
        </label>
        <label className="charge-field">
          <span>初始风门 %</span>
          <input
            type="number"
            inputMode="decimal"
            min={0}
            max={100}
            value={fan}
            onChange={(e) => onFan(e.target.value)}
            disabled={busy}
          />
        </label>
      </div>
      <button
        type="button"
        className="btn btn-charge"
        onClick={onCharge}
        disabled={disabled || busy}
      >
        入豆
      </button>
    </div>
  )
}
