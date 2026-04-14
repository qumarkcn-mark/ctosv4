import { useState, useCallback, useRef } from 'react'
import StockSearch from '../components/StockSearch.jsx'
import WatchlistPanel from '../components/WatchlistPanel.jsx'
import KlineChart from '../components/KlineChart.jsx'
import LayerPanel from '../components/LayerPanel.jsx'
import TRadar from '../components/TRadar.jsx'
import { loadVisibility, saveVisibility } from '../store/layerState.js'
import './ChanView.css'

export default function ChanView() {
  const [symbol, setSymbol] = useState('sh600519')
  const [symbolName, setSymbolName] = useState('贵州茅台')
  const [layerVisibility, setLayerVisibility] = useState(loadVisibility)
  const watchlistRef = useRef(null)

  const handleSelect = useCallback((stock) => {
    setSymbol(stock.symbol)
    setSymbolName(stock.name || stock.symbol)
  }, [])

  const handleLayerChange = useCallback((vis) => {
    setLayerVisibility(vis)
    saveVisibility(vis)
  }, [])

  const handleAddToWatchlist = useCallback(() => {
    watchlistRef.current?.addToGroup('观察', { symbol, name: symbolName })
  }, [symbol, symbolName])

  return (
    <div className="chan-view">
      {/* 左侧自选股面板 */}
      <WatchlistPanel ref={watchlistRef} activeSymbol={symbol} onSelect={handleSelect} />

      {/* 右侧主体 */}
      <div className="chan-main">
        {/* 顶部：搜索 + 股票名称 + 图层控制 */}
        <div className="chan-topbar">
          <div className="chan-search-area">
            <StockSearch onSelect={handleSelect} />
          </div>
          <div className="chan-symbol-info">
            <span className="symbol-name">{symbolName}</span>
            <span className="symbol-code mono">{symbol}</span>
          </div>
          <div className="chan-topbar-actions">
            <button
              className="add-watchlist-btn"
              onClick={handleAddToWatchlist}
              title="加入自选"
            >
              ★
            </button>
            <LayerPanel visibility={layerVisibility} onChange={handleLayerChange} />
          </div>
        </div>

        {/* K 线图表 */}
        <KlineChart symbol={symbol} layerVisibility={layerVisibility} />

        {/* 推演雷达 */}
        <TRadar symbol={symbol} />
      </div>
    </div>
  )
}
