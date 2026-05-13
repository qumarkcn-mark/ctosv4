import { useCallback, useRef, useState } from 'react'
import StockSearch from '../components/StockSearch.jsx'
import WatchlistPanel from '../components/WatchlistPanel.jsx'
import AIStructureCoachPanel from '../features/aiStructure/AIStructureCoachPanel.jsx'
import AIStructureEvidenceChart from '../features/aiStructure/AIStructureEvidenceChart.jsx'
import { readLastViewedSymbol } from '../utils/symbolStorage.js'
import './AIStructureWorkspace.css'

export default function AIStructureWorkspace({ activeSymbol, activeSymbolName, onSymbolChange }) {
  const [localSymbol, setLocalSymbol] = useState(() => readLastViewedSymbol().symbol)
  const [localName, setLocalName] = useState(() => readLastViewedSymbol().name)
  const [aiEvidenceContext, setAiEvidenceContext] = useState(null)
  const watchlistRef = useRef(null)
  const symbol = activeSymbol ?? localSymbol
  const symbolName = activeSymbolName ?? localName

  const handleSelect = useCallback((stock) => {
    const nextSymbol = stock.symbol
    const nextName = stock.name || stock.symbol
    setAiEvidenceContext(null)
    if (onSymbolChange) {
      onSymbolChange(nextSymbol, nextName)
    } else {
      setLocalSymbol(nextSymbol)
      setLocalName(nextName)
      localStorage.setItem('lastViewedSymbol', nextSymbol)
      localStorage.setItem('lastViewedSymbolName', nextName)
    }
  }, [onSymbolChange])

  return (
    <div className="ai-workspace">
      <aside className="ai-workspace-watchlist">
        <WatchlistPanel ref={watchlistRef} activeSymbol={symbol} onSelect={handleSelect} />
      </aside>

      <section className="ai-workspace-main">
        <div className="ai-workspace-topbar">
          <div className="ai-workspace-search">
            <StockSearch onSelect={handleSelect} />
          </div>
          <div className="ai-workspace-symbol">
            <strong>{symbolName}</strong>
            <span>{symbol}</span>
          </div>
        </div>

        <div className="ai-workspace-body">
          <AIStructureEvidenceChart
            symbol={symbol}
            symbolName={symbolName}
            chartContext={aiEvidenceContext}
          />
          <aside className="ai-workspace-coach">
            <AIStructureCoachPanel
              symbol={symbol}
              symbolName={symbolName}
              onEvidenceContext={setAiEvidenceContext}
            />
          </aside>
        </div>
      </section>
    </div>
  )
}
