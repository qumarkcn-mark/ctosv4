import { useState, useEffect, useCallback, forwardRef, useImperativeHandle } from 'react'
import './WatchlistPanel.css'

const STORAGE_KEY = 'ct_watchlist_v4'

const DEFAULT_GROUPS = [
  { name: '观察', stocks: [
    { symbol: 'sh600519', name: '贵州茅台' },
    { symbol: 'sz000001', name: '平安银行' },
  ]},
  { name: '重仓', stocks: [] },
  { name: '短线', stocks: [] },
]

function loadWatchlist() {
  try {
    const data = localStorage.getItem(STORAGE_KEY)
    return data ? JSON.parse(data) : DEFAULT_GROUPS
  } catch {
    return DEFAULT_GROUPS
  }
}

function saveWatchlist(groups) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(groups))
}

const WatchlistPanel = forwardRef(function WatchlistPanel({ activeSymbol, onSelect }, ref) {
  const [groups, setGroups] = useState(loadWatchlist)
  const [collapsed, setCollapsed] = useState({})

  useEffect(() => {
    saveWatchlist(groups)
  }, [groups])

  const toggleCollapse = (name) => {
    setCollapsed((prev) => ({ ...prev, [name]: !prev[name] }))
  }

  const addToGroup = useCallback((groupName, stock) => {
    setGroups((prev) =>
      prev.map((g) => {
        if (g.name !== groupName) return g
        if (g.stocks.some((s) => s.symbol === stock.symbol)) return g
        return { ...g, stocks: [...g.stocks, stock] }
      })
    )
  }, [])

  const removeStock = useCallback((groupName, symbol) => {
    setGroups((prev) =>
      prev.map((g) => {
        if (g.name !== groupName) return g
        return { ...g, stocks: g.stocks.filter((s) => s.symbol !== symbol) }
      })
    )
  }, [])

  // P0-FIX: 通过 ref 暴露 addToGroup，替代静态属性反模式
  useImperativeHandle(ref, () => ({
    addToGroup,
  }), [addToGroup])

  return (
    <div className="watchlist-panel">
      <div className="watchlist-header">
        <span className="watchlist-title">⭐ 自选股</span>
      </div>
      {groups.map((group) => (
        <div key={group.name} className="watchlist-group">
          <div
            className="group-header"
            onClick={() => toggleCollapse(group.name)}
          >
            <span className="group-arrow">{collapsed[group.name] ? '▶' : '▼'}</span>
            <span className="group-name">{group.name}</span>
            <span className="group-count">{group.stocks.length}</span>
          </div>
          {!collapsed[group.name] && (
            <div className="group-stocks">
              {group.stocks.length === 0 ? (
                <div className="empty-hint">搜索添加</div>
              ) : (
                group.stocks.map((stock) => (
                  <div
                    key={stock.symbol}
                    className={`stock-item ${activeSymbol === stock.symbol ? 'active' : ''}`}
                    onClick={() => onSelect?.(stock)}
                  >
                    <span className="stock-name">{stock.name}</span>
                    <span className="stock-code mono">{stock.symbol}</span>
                    <button
                      className="stock-remove"
                      onClick={(e) => {
                        e.stopPropagation()
                        removeStock(group.name, stock.symbol)
                      }}
                      title="移除"
                    >
                      ×
                    </button>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
})

export default WatchlistPanel
