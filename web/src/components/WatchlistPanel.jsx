import { useState, useEffect, useCallback, forwardRef, useImperativeHandle, useRef } from 'react'
import './WatchlistPanel.css'

const WatchlistPanel = forwardRef(function WatchlistPanel({ activeSymbol, onSelect }, ref) {
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(true)
  const [collapsed, setCollapsed] = useState({})

  // 新增分组
  const [addingGroup, setAddingGroup] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')

  // 重命名分组
  const [renamingGroup, setRenamingGroup] = useState(null) // group name
  const [renameValue, setRenameValue] = useState('')

  const newGroupInputRef = useRef(null)
  const renameInputRef = useRef(null)

  const fetchWatchlist = async () => {
    try {
      const resp = await fetch('/api/watchlist')
      if (resp.ok) {
        const data = await resp.json()
        setGroups(data)
      }
    } catch (err) {
      console.error('获取自选股失败:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchWatchlist()
  }, [])

  useEffect(() => {
    if (addingGroup) newGroupInputRef.current?.focus()
  }, [addingGroup])

  useEffect(() => {
    if (renamingGroup) renameInputRef.current?.focus()
  }, [renamingGroup])

  const toggleCollapse = (name) => {
    setCollapsed((prev) => ({ ...prev, [name]: !prev[name] }))
  }

  // ─── 新增分组
  const commitAddGroup = async () => {
    const name = newGroupName.trim()
    if (!name) { setAddingGroup(false); setNewGroupName(''); return }
    if (groups.some((g) => g.name === name)) {
      setNewGroupName('')
      setAddingGroup(false)
      return
    }
    
    try {
      await fetch('/api/watchlist/groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      })
      await fetchWatchlist()
    } catch (err) {
      console.error('创建分组失败:', err)
    }
    
    setNewGroupName('')
    setAddingGroup(false)
  }

  // ─── 重命名分组
  const commitRename = async () => {
    const newName = renameValue.trim()
    if (!newName || newName === renamingGroup) { setRenamingGroup(null); return }
    if (groups.some((g) => g.name === newName)) { setRenamingGroup(null); return }
    
    try {
      await fetch(`/api/watchlist/groups/${encodeURIComponent(renamingGroup)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName })
      })
      await fetchWatchlist()
    } catch (err) {
      console.error('重命名分组失败:', err)
    }
    
    setRenamingGroup(null)
  }

  // ─── 删除分组（含股票一起删）
  const deleteGroup = async (name) => {
    if (!window.confirm(`确定删除分组「${name}」及其中所有股票吗？`)) return
    try {
      await fetch(`/api/watchlist/groups/${encodeURIComponent(name)}`, {
        method: 'DELETE'
      })
      await fetchWatchlist()
    } catch (err) {
      console.error('删除分组失败:', err)
    }
  }

  // ─── 添加股票到分组（由父组件通过 ref 调用）
  const addToGroup = useCallback(async (groupName, stock) => {
    // 乐观更新（立即生效体验）
    setGroups((prev) =>
      prev.map((g) => {
        if (g.name === groupName) {
          if (g.stocks.some((s) => s.symbol === stock.symbol)) return g
          return { ...g, stocks: [...g.stocks, stock] }
        }
        // 保证全局唯一：其它分组移除
        const newStocks = g.stocks.filter((s) => s.symbol !== stock.symbol)
        if (newStocks.length === g.stocks.length) return g
        return { ...g, stocks: newStocks }
      })
    )

    try {
      await fetch(`/api/watchlist/groups/${encodeURIComponent(groupName)}/stocks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: stock.symbol, name: stock.name })
      })
      // 不需要 await fetchWatchlist，乐观更新已经显示了
    } catch (err) {
      console.error('添加自选股失败:', err)
      fetchWatchlist() // 如果失败，回滚拉取真实数据
    }
  }, [])

  const removeStock = useCallback(async (groupName, symbol) => {
    // 乐观更新
    setGroups((prev) =>
      prev.map((g) => {
        if (g.name !== groupName) return g
        return { ...g, stocks: g.stocks.filter((s) => s.symbol !== symbol) }
      })
    )
    
    try {
      await fetch(`/api/watchlist/groups/${encodeURIComponent(groupName)}/stocks/${symbol}`, {
        method: 'DELETE'
      })
    } catch (err) {
      console.error('移除自选股失败:', err)
      fetchWatchlist() // 失败回滚
    }
  }, [])

  // ─── 暴露给父组件
  useImperativeHandle(ref, () => ({
    addToGroup,
    getGroupNames: () => groups.map((g) => g.name),
  }), [addToGroup, groups])

  return (
    <div className="watchlist-panel">
      <div className="watchlist-header">
        <span className="watchlist-title">⭐ 自选股</span>
        <button
          className="watchlist-add-group-btn"
          onClick={() => { setAddingGroup(true); setRenamingGroup(null) }}
          title="新建分组"
        >
          +
        </button>
      </div>

      {/* 新增分组输入框 */}
      {addingGroup && (
        <div className="group-add-row">
          <input
            ref={newGroupInputRef}
            className="group-name-input"
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            placeholder="分组名称"
            maxLength={12}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitAddGroup()
              if (e.key === 'Escape') { setAddingGroup(false); setNewGroupName('') }
            }}
          />
          <button className="group-input-ok" onClick={commitAddGroup}>✓</button>
          <button className="group-input-cancel" onClick={() => { setAddingGroup(false); setNewGroupName('') }}>✕</button>
        </div>
      )}

      {groups.map((group) => (
        <div key={group.name} className="watchlist-group">
          {/* 分组 header */}
          {renamingGroup === group.name ? (
            <div className="group-add-row">
              <input
                ref={renameInputRef}
                className="group-name-input"
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                maxLength={12}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitRename()
                  if (e.key === 'Escape') setRenamingGroup(null)
                }}
              />
              <button className="group-input-ok" onClick={commitRename}>✓</button>
              <button className="group-input-cancel" onClick={() => setRenamingGroup(null)}>✕</button>
            </div>
          ) : (
            <div
              className="group-header"
              onClick={() => toggleCollapse(group.name)}
            >
              <span className="group-arrow">{collapsed[group.name] ? '▶' : '▼'}</span>
              <span className="group-name">{group.name}</span>
              <span className="group-count">{group.stocks.length}</span>
              {/* 重命名 / 删除按钮 — hover 时显示 */}
              <span className="group-actions" onClick={(e) => e.stopPropagation()}>
                <button
                  className="group-action-btn rename"
                  title="重命名"
                  onClick={() => { setRenamingGroup(group.name); setRenameValue(group.name) }}
                >
                  ✎
                </button>
                <button
                  className="group-action-btn delete"
                  title="删除分组"
                  onClick={() => deleteGroup(group.name)}
                >
                  ×
                </button>
              </span>
            </div>
          )}

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
