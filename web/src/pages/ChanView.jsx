import { useState, useCallback, useRef, useEffect } from 'react'
import StockSearch from '../components/StockSearch.jsx'
import WatchlistPanel from '../components/WatchlistPanel.jsx'
import KlineChart from '../components/KlineChart.jsx'
import LayerPanel from '../components/LayerPanel.jsx'
import TRadarV2 from '../components/TRadarV2.jsx'
import { loadVisibility, saveVisibility } from '../store/layerState.js'
import './ChanView.css'

export default function ChanView({ activeSymbol, activeSymbolName, onSymbolChange }) {
  // 兼容降级：若父组件未传 props（如旧路由），读本地存储
  const [localSymbol, setLocalSymbol] = useState(
    () => localStorage.getItem('lastViewedSymbol') || 'sh600519'
  )
  const [localName, setLocalName] = useState(
    () => localStorage.getItem('lastViewedSymbolName') || '贵州茅台'
  )

  const symbol = activeSymbol ?? localSymbol
  const symbolName = activeSymbolName ?? localName

  const [layerVisibility, setLayerVisibility] = useState(loadVisibility)
  const [showWatchlistMenu, setShowWatchlistMenu] = useState(false)
  const [groupNames, setGroupNames] = useState([])
  const watchlistRef = useRef(null)

  const handleSelect = useCallback((stock) => {
    const sym = stock.symbol
    const sName = stock.name || stock.symbol
    if (onSymbolChange) {
      onSymbolChange(sym, sName)
    } else {
      setLocalSymbol(sym)
      setLocalName(sName)
      localStorage.setItem('lastViewedSymbol', sym)
      localStorage.setItem('lastViewedSymbolName', sName)
    }
  }, [onSymbolChange])

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
                onClick={() => {
                  setGroupNames(watchlistRef.current?.getGroupNames?.() || [])
                  setShowWatchlistMenu(!showWatchlistMenu)
                }}
                title="加入自选"
              >
                ★
              </button>
              {showWatchlistMenu && (
                <div className="watchlist-dropdown-menu">
                  {groupNames.length === 0 ? (
                    <div className="watchlist-dropdown-item" style={{ color: '#5a5c66', cursor: 'default' }}>暂无分组</div>
                  ) : (
                    groupNames.map((name) => (
                      <div
                        key={name}
                        className="watchlist-dropdown-item"
                        onClick={() => handleAddToWatchlist(name)}
                      >
                        入{name}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
            <LayerPanel visibility={layerVisibility} onChange={handleLayerChange} />
          </div>
        </div>

        {/* 图表 + 雷达并排 */}
        <div className="chan-content-row">
          <KlineChart symbol={symbol} layerVisibility={layerVisibility} />
          <div className="chan-radar-sidebar">
            <TRadarV2 symbol={symbol} />
          </div>
        </div>
      </div>
    </div>
  )
}
