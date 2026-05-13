import './AIStructureEvidenceChart.css'

const WIDTH = 960
const HEIGHT = 520
const PAD = { top: 28, right: 88, bottom: 34, left: 54 }

export default function AIStructureEvidenceChart({ symbol, symbolName, chartContext }) {
  const klines = Array.isArray(chartContext?.klines) ? chartContext.klines : []
  const overlays = chartContext?.overlays || {}
  const lines = Array.isArray(overlays.lines) ? overlays.lines : []
  const candles = normalizeKlines(klines)
  const priceRange = buildPriceRange(candles, overlays)
  const plotWidth = WIDTH - PAD.left - PAD.right
  const plotHeight = HEIGHT - PAD.top - PAD.bottom

  const xAt = (index) => PAD.left + (candles.length <= 1 ? plotWidth : (index / (candles.length - 1)) * plotWidth)
  const yAt = (price) => PAD.top + ((priceRange.max - price) / (priceRange.max - priceRange.min || 1)) * plotHeight
  const candleWidth = Math.max(4, Math.min(12, plotWidth / Math.max(candles.length, 32) * 0.55))
  const gridPrices = makeGridPrices(priceRange)

  return (
    <section className="ai-evidence-chart">
      <header className="ai-evidence-chart__head">
        <div>
          <span>AI 证据图</span>
          <strong>{symbolName || symbol}</strong>
        </div>
        <em>{chartContext?.level ? `${chartContext.level} 级别` : '等待回答'}</em>
      </header>

      <div className="ai-evidence-chart__canvas">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="AI structure evidence chart">
          <rect className="ai-evidence-bg" x="0" y="0" width={WIDTH} height={HEIGHT} rx="0" />

          {gridPrices.map((price) => (
            <g key={price}>
              <line className="ai-evidence-grid" x1={PAD.left} y1={yAt(price)} x2={WIDTH - PAD.right} y2={yAt(price)} />
              <text className="ai-evidence-axis" x={WIDTH - PAD.right + 12} y={yAt(price) + 4}>{formatPrice(price)}</text>
            </g>
          ))}

          {overlays.active_center && (
            <rect
              className="ai-evidence-center"
              x={PAD.left}
              y={Math.min(yAt(Number(overlays.active_center.zg)), yAt(Number(overlays.active_center.zd)))}
              width={plotWidth}
              height={Math.abs(yAt(Number(overlays.active_center.zd)) - yAt(Number(overlays.active_center.zg))) || 3}
            />
          )}

          {candles.map((bar, index) => {
            const x = xAt(index)
            const up = bar.close >= bar.open
            return (
              <g key={`${bar.time || index}-${index}`} className={up ? 'ai-candle ai-candle--up' : 'ai-candle ai-candle--down'}>
                <line x1={x} y1={yAt(bar.high)} x2={x} y2={yAt(bar.low)} />
                <rect
                  x={x - candleWidth / 2}
                  y={Math.min(yAt(bar.open), yAt(bar.close))}
                  width={candleWidth}
                  height={Math.max(2, Math.abs(yAt(bar.close) - yAt(bar.open)))}
                />
              </g>
            )
          })}

          {lines.map((line) => (
            <EvidenceLine key={line.evidence_id || `${line.role}-${line.price}`} item={line} y={yAt(Number(line.price))} />
          ))}
        </svg>

        {!candles.length && (
          <div className="ai-evidence-chart__empty">
            <strong>等待 AI 回答引用证据</strong>
            <span>这里只展示 V5 chart-context 返回的轻量证据，不加载旧结构入口。</span>
          </div>
        )}
      </div>
    </section>
  )
}

function EvidenceLine({ item, y }) {
  return (
    <g className={`ai-evidence-line ai-evidence-line--${item.role || 'default'}`}>
      <line x1={PAD.left} y1={y} x2={WIDTH - PAD.right} y2={y} />
      <text x={WIDTH - PAD.right + 12} y={y - 7}>{item.label || item.role}</text>
      <text x={WIDTH - PAD.right + 12} y={y + 12}>{formatPrice(item.price)}</text>
    </g>
  )
}

function normalizeKlines(klines) {
  return klines
    .map((item) => {
      const close = numberOr(item.close, item.price)
      const open = numberOr(item.open, close)
      const high = numberOr(item.high, Math.max(open, close))
      const low = numberOr(item.low, Math.min(open, close))
      return { ...item, open, high, low, close }
    })
    .filter((item) => item.close > 0 && item.high > 0 && item.low > 0)
    .slice(-160)
}

function buildPriceRange(candles, overlays) {
  const prices = []
  candles.forEach((item) => prices.push(item.high, item.low))
  ;(overlays.lines || []).forEach((item) => prices.push(Number(item.price)))
  const center = overlays.active_center
  if (center) prices.push(Number(center.zg), Number(center.zd))
  const valid = prices.filter((item) => Number.isFinite(item) && item > 0)
  if (!valid.length) return { min: 0, max: 1 }
  const min = Math.min(...valid)
  const max = Math.max(...valid)
  const pad = Math.max((max - min) * 0.12, max * 0.006, 0.01)
  return { min: min - pad, max: max + pad }
}

function makeGridPrices(range) {
  const step = (range.max - range.min) / 4
  return [0, 1, 2, 3, 4].map((index) => range.min + step * index)
}

function numberOr(value, fallback) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function formatPrice(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '--'
  return parsed >= 100 ? parsed.toFixed(1) : parsed.toFixed(2)
}
