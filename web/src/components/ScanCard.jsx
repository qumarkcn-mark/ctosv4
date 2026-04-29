import './ScanCard.css'
import { getScanQualityFlags } from '../utils/scanQuality.js'

const STRATEGY_LABEL = {
  war1: '战法一·日线三买',
  war2: '战法二·趋势台阶',
}

const VERDICT_CLASS = {
  支持: 'support',
  中性: 'neutral',
  回避: 'avoid',
}
const STATUS_LABEL = {
  pending: '待分析',
  analyzing: '分析中',
  ready: '已完成',
  failed: '失败',
}

function strategyLabel(item) {
  return item.strategy_name || STRATEGY_LABEL[item.strategy] || item.strategy_id || item.strategy
}

function pct(value) {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function price(value) {
  if (value === null || value === undefined) return '—'
  return Number(value).toFixed(2)
}

export default function ScanCard({ item, onView, onAdd, onDelete, onDetail, busy }) {
  const verdict = item.llm_verdict || '中性'
  const verdictClass = VERDICT_CLASS[verdict] || 'neutral'
  const redFlags = item.llm_red_flags || []
  const pros = item.llm_pros || []
  const cons = item.llm_cons || []
  const qualityFlags = getScanQualityFlags(item)
  const label = strategyLabel(item)

  return (
    <article className={`scan-card scan-card--${verdictClass}`}>
      <div className="scan-card-top">
        <div className="scan-card-title-group">
          <div className="scan-symbol-row">
            <span className="scan-symbol">{item.symbol}</span>
            <span className="scan-strategy">{label}</span>
            <span className={`scan-status scan-status--${item.status || 'ready'}`}>
              {STATUS_LABEL[item.status] || item.status || '已完成'}
            </span>
          </div>
          {qualityFlags.length > 0 && (
            <div className="scan-quality-row">
              {qualityFlags.slice(0, 3).map((flag) => (
                <span key={flag.label} className={`scan-quality scan-quality--${flag.level}`}>{flag.label}</span>
              ))}
            </div>
          )}
          <div className="scan-desc">{item.chan_desc || '结构信号待补充'}</div>
        </div>
        <div className="scan-score">
          <span>{Math.round(item.score || 0)}</span>
          <small>分</small>
        </div>
      </div>

      <div className="scan-metrics">
        <div>
          <span>现价</span>
          <strong>{price(item.close)}</strong>
        </div>
        <div>
          <span>止损</span>
          <strong>{price(item.stop_loss)}</strong>
        </div>
        <div>
          <span>目标</span>
          <strong>{price(item.target)}</strong>
        </div>
        <div>
          <span>赔率</span>
          <strong>{item.rr_ratio ? `1:${Number(item.rr_ratio).toFixed(1)}` : '—'}</strong>
        </div>
        <div>
          <span>ATR</span>
          <strong>{pct(item.atr_pct)}</strong>
        </div>
        <div>
          <span>量比</span>
          <strong>{item.volume_ratio ? Number(item.volume_ratio).toFixed(2) : '—'}</strong>
        </div>
      </div>

      <div className="scan-research">
        <div className="scan-verdict-row">
          <span className={`scan-verdict scan-verdict--${verdictClass}`}>{verdict}</span>
          <span className="scan-summary">{item.llm_summary || '仅技术面通过，待基本面确认'}</span>
        </div>

        {redFlags.length > 0 && (
          <div className="scan-flags">
            {redFlags.slice(0, 3).map((flag, i) => (
              <span key={i}>风险：{flag}</span>
            ))}
          </div>
        )}

        <div className="scan-points">
          {pros.slice(0, 2).map((p, i) => (
            <span key={`p-${i}`} className="scan-point scan-point--pro">{p}</span>
          ))}
          {cons.slice(0, 2).map((c, i) => (
            <span key={`c-${i}`} className="scan-point scan-point--con">{c}</span>
          ))}
        </div>
      </div>

      <div className="scan-actions">
        <button type="button" onClick={() => onDetail(item)} disabled={busy}>
          详情
        </button>
        <button type="button" onClick={() => onView(item)} disabled={busy}>
          看盘
        </button>
        <button type="button" onClick={() => onAdd(item)} disabled={busy}>
          入观察
        </button>
        <button type="button" className="scan-danger-btn" onClick={() => onDelete(item)} disabled={busy}>
          删除
        </button>
      </div>
    </article>
  )
}
