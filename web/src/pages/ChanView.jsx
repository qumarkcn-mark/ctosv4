import { useState, useCallback, useRef, useEffect } from 'react'
import StockSearch from '../components/StockSearch.jsx'
import WatchlistPanel from '../components/WatchlistPanel.jsx'
import KlineChart from '../components/KlineChart.jsx'
import LayerPanel from '../components/LayerPanel.jsx'
import TRadarV2 from '../components/TRadarV2.jsx'
import { loadVisibility, saveVisibility } from '../store/layerState.js'
import './ChanView.css'

export default function ChanView() {
  const [symbol, setSymbol] = useState(() => localStorage.getItem('lastViewedSymbol') || 'sh600519')
  const [symbolName, setSymbolName] = useState(() => localStorage.getItem('lastViewedSymbolName') || '贵州茅台')
  const [layerVisibility, setLayerVisibility] = useState(loadVisibility)
  const [showWatchlistMenu, setShowWatchlistMenu] = useState(false)
  const watchlistRef = useRef(null)

  const handleSelect = useCallback((stock) => {
    const sym = stock.symbol
    const sName = stock.name || stock.symbol
    setSymbol(sym)
    setSymbolName(sName)
    localStorage.setItem('lastViewedSymbol', sym)
    localStorage.setItem('lastViewedSymbolName', sName)
  }, [])

  const handleLayerChange = useCallback((vis) => {
    setLayerVisibility(vis)
    saveVisibility(vis)
  }, [])

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (!e.target.closest('.watchlist-dropdown-container')) {
        setShowWatchlistMenu(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleAddToWatchlist = useCallback((groupName) => {
    watchlistRef.current?.addToGroup(groupName, { symbol, name: symbolName })
    setShowWatchlistMenu(false)
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
            <div className="watchlist-dropdown-container">
              <button
                className="add-watchlist-btn"
                onClick={() => setShowWatchlistMenu(!showWatchlistMenu)}
                title="加入自选"
              >
                ★
              </button>
              {showWatchlistMenu && (
                <div className="watchlist-dropdown-menu">
                  <div className="watchlist-dropdown-item" onClick={() => handleAddToWatchlist('观察')}>入观察</div>
                  <div className="watchlist-dropdown-item" onClick={() => handleAddToWatchlist('重仓')}>入重仓</div>
                  <div className="watchlist-dropdown-item" onClick={() => handleAddToWatchlist('短线')}>入短线</div>
                </div>
              )}
            </div>
            <LayerPanel visibility={layerVisibility} onChange={handleLayerChange} />
          </div>
        </div>

        {/* K 线图表 */}
        <KlineChart symbol={symbol} layerVisibility={layerVisibility} />

        {/* 推演雷达 */}
        <TRadarV2 symbol={symbol} />
      </div>
    </div>
  )
}
