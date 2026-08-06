// 顶部提示条：连接错误（持久）/ 操作反馈 toast（暂态）/ 发送中
interface Props {
  error: string | null
  toast: string | null
  busy: boolean
}

export default function StatusBanner({ error, toast, busy }: Props) {
  if (error) return <div className="banner banner-error">{error}</div>
  if (toast) return <div className="banner banner-toast">{toast}</div>
  if (busy) return <div className="banner banner-busy">发送中…</div>
  return null
}
