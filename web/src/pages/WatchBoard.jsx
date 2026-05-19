import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { apiJson } from '../api/client.js'
import StockSearch from '../components/StockSearch.jsx'
import WatchCard from '../components/WatchCard.jsx'
import './WatchBoard.css'

const GROUPS = ['自选', '备选']
const QUICK_QUESTIONS = ['现在怎么看？', '跌破哪里失效？', '企稳看哪里？', '要不要减仓？']

function isTradingTime(now = new Date()) {
  const day = now.getDay()
  if (day === 0 || day === 6) return false
  const minutes = now.getHours() * 60 + now.getMinutes()
  return minutes >= 9 * 60 + 30 && minutes <= 15 * 60
}

function flattenGroups(groups) {
  return groups.flatMap((group) => group.items || [])
}

function priceKey(symbol) {
  return String(symbol || '').replace('.', '')
}

function mergePrice(item, prices) {
  const quote = prices[item.symbol] || prices[priceKey(item.symbol)]
  if (!quote) return item
  return {
    ...item,
    price: quote.price ?? item.price,
    change_pct: quote.change_pct ?? item.change_pct,
    price_data: {
      ...(item.price_data || {}),
      ...quote,
    },
  }
}

function formatPrice(value) {
  const num = Number(value || 0)
  if (!num) return '--'
  return num >= 100 ? num.toFixed(2) : num.toFixed(3).replace(/0$/, '')
}

export default function WatchBoard() {
  const [groups, setGroups] = useState([])
  const [prices, setPrices] = useState({})
  const [selected, setSelected] = useState(null)
  const [fullText, setFullText] = useState('')
  const [detailStatus, setDetailStatus] = useState('idle')
  const [drawerLoading, setDrawerLoading] = useState(false)
  const [reasoningRunning, setReasoningRunning] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [targetGroup, setTargetGroup] = useState('自选')
  const [adding, setAdding] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatMessages, setChatMessages] = useState([])
  const [chatLoading, setChatLoading] = useState(false)
  const [activeDetailTab, setActiveDetailTab] = useState('reasoning')
  const chatLogRef = useRef(null)

  const allItems = useMemo(() => flattenGroups(groups), [groups])
  const displayGroups = useMemo(() => (
    groups.map((group) => ({
      ...group,
      items: (group.items || []).map((item) => mergePrice(item, prices)),
    }))
  ), [groups, prices])

  const loadWatchboard = useCallback(async () => {
    setError('')
    try {
      const json = await apiJson('/api/ai-structure/watchboard')
      setGroups(json.data?.groups || [])
    } catch (err) {
      setError(err.message || '盯盘面板加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const pollPrices = useCallback(async () => {
    const symbols = allItems.map((item) => item.symbol).filter(Boolean)
    if (!symbols.length) return
    try {
      const json = await apiJson(`/api/data/prices?symbols=${symbols.map(encodeURIComponent).join(',')}`)
      setPrices(json.prices || {})
    } catch (err) {
      console.error('盯盘价格刷新失败:', err)
    }
  }, [allItems])

  useEffect(() => {
    loadWatchboard()
  }, [loadWatchboard])

  useEffect(() => {
    if (!allItems.length) return
    const tick = () => {
      if (isTradingTime()) pollPrices()
    }
    tick()
    const timer = setInterval(tick, 5000)
    return () => clearInterval(timer)
  }, [allItems, pollPrices])

  useEffect(() => {
    if (activeDetailTab !== 'chat') return
    window.requestAnimationFrame(() => {
      const node = chatLogRef.current?.lastElementChild
      node?.scrollIntoView({ block: 'end' })
    })
  }, [activeDetailTab, chatMessages, chatLoading])

  const openDetail = async (item) => {
    const current = mergePrice(item, prices)
    setSelected(current)
    setFullText('')
    setDetailStatus('loading')
    setChatMessages([])
    setActiveDetailTab('reasoning')
    setDrawerLoading(true)
    try {
      const json = await apiJson(`/api/ai-structure/unified-reasoning/full/${encodeURIComponent(current.symbol)}`)
      setFullText(json.data?.full_text || '')
      setDetailStatus('ready')
    } catch (err) {
      setFullText('')
      setDetailStatus('missing')
    } finally {
      setDrawerLoading(false)
    }
  }

  const runReasoningForSelected = async () => {
    if (!selected) return
    setReasoningRunning(true)
    setDetailStatus('loading')
    try {
      await apiJson('/api/ai-structure/unified-reasoning/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: [selected.symbol] }),
      })
      const json = await apiJson(`/api/ai-structure/unified-reasoning/full/${encodeURIComponent(selected.symbol)}`)
      setFullText(json.data?.full_text || '')
      setDetailStatus('ready')
      await loadWatchboard()
    } catch (err) {
      setDetailStatus('missing')
      setFullText('')
      setError(err.message || '统一推演生成失败')
    } finally {
      setReasoningRunning(false)
      setDrawerLoading(false)
    }
  }

  const addStock = async (stock) => {
    setAdding(true)
    setError('')
    try {
      await apiJson(`/api/watchlist/groups/${encodeURIComponent(targetGroup)}/stocks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: stock.symbol, name: stock.name }),
      })
      try {
        await apiJson('/api/ai-structure/unified-reasoning/trigger', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbols: [stock.symbol] }),
        })
      } catch (err) {
        console.warn('新增股票后触发统一推演失败:', err)
      }
      await loadWatchboard()
    } catch (err) {
      setError(err.message || '添加股票失败')
    } finally {
      setAdding(false)
    }
  }

  const sendQuestion = async (question = chatInput) => {
    const text = String(question || '').trim()
    if (!text || !selected) return
    setChatLoading(true)
    setActiveDetailTab('chat')
    setChatInput('')
    setChatMessages((prev) => [...prev, { role: 'user', content: text }])
    try {
      const json = await apiJson('/api/ai-structure/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: selected.symbol, question: text }),
      })
      setChatMessages((prev) => [
        ...prev,
        { role: 'assistant', content: json.data?.coach_answer || '暂无回答' },
      ])
    } catch (err) {
      setChatMessages((prev) => [...prev, { role: 'assistant', content: err.message || '问答失败' }])
    } finally {
      setChatLoading(false)
    }
  }

  const chatStatus = chatLoading
    ? { tone: 'active', label: '推演中', detail: '正在结合完整推演与当前价格' }
    : drawerLoading
      ? { tone: 'active', label: '读取中', detail: '正在读取完整推演上下文' }
      : detailStatus === 'ready'
        ? { tone: 'ready', label: '上下文就绪', detail: '基于完整推演回答' }
        : { tone: 'idle', label: '待生成', detail: '可先生成统一推演后再追问' }

  return (
    <div className={`watchboard-page ${selected ? 'has-detail' : ''}`}>
      <div className="watchboard-main">
        <header className="watchboard-toolbar">
          <div>
            <h1>V5 盯盘</h1>
            <p>每张卡只显示一条当前主线。仅供参考，不构成投资建议。</p>
          </div>
          <div className="watchboard-add">
            <StockSearch onSelect={addStock} />
            <select aria-label="添加到分组" value={targetGroup} onChange={(event) => setTargetGroup(event.target.value)}>
              {GROUPS.map((group) => <option key={group}>{group}</option>)}
            </select>
            <span className={isTradingTime() ? 'watchboard-live is-open' : 'watchboard-live'}>
              <i aria-hidden="true" />
              {isTradingTime() ? '盘中' : '非盘中'}
            </span>
          </div>
        </header>

        {error && <div className="watchboard-error">{error}</div>}
        {adding && <div className="watchboard-info">已添加，正在尝试触发统一推演...</div>}
        {loading && <div className="watchboard-empty">正在加载盯盘面板...</div>}

        {!loading && displayGroups.map((group) => (
          <section className="watchboard-section" key={group.type}>
            <div className="watchboard-section-title">
              <h2>{group.name}</h2>
              <span>{group.items?.length || 0} 只</span>
            </div>
            {group.items?.length ? (
              <div className="watchboard-grid">
                {group.items.map((item) => (
                  <WatchCard
                    key={`${group.type}:${item.symbol}`}
                    item={item}
                    currentPrice={item.price}
                    onClick={() => openDetail(item)}
                  />
                ))}
              </div>
            ) : (
              <div className="watchboard-empty">暂无{group.name}票</div>
            )}
          </section>
        ))}
      </div>

      {selected && (
        <aside className="watchboard-drawer" aria-label="盯盘详情">
          <div className="watchboard-drawer-header">
            <div>
              <strong>{selected.name || selected.symbol}</strong>
              <span>{selected.symbol} · {formatPrice(selected.price)}</span>
            </div>
            <button type="button" onClick={() => setSelected(null)}>关闭</button>
          </div>

          <div className="watchboard-scenarios">
            {(selected.reasoning_summary?.scenarios || []).map((scenario, index) => (
              <span key={`${scenario.name}-${index}`}>
                {scenario.name} {scenario.probability} · {scenario.brief}
              </span>
            ))}
          </div>

          <div className="watchboard-detail-tabs" role="tablist" aria-label="盯盘详情内容">
            <button
              type="button"
              role="tab"
              aria-selected={activeDetailTab === 'reasoning'}
              className={activeDetailTab === 'reasoning' ? 'is-active' : ''}
              onClick={() => setActiveDetailTab('reasoning')}
            >
              完整推演
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeDetailTab === 'chat'}
              className={activeDetailTab === 'chat' ? 'is-active' : ''}
              onClick={() => setActiveDetailTab('chat')}
            >
              问答追踪
              {chatMessages.length ? <span>{chatMessages.length}</span> : null}
            </button>
          </div>

          <div className="watchboard-drawer-body">
            {activeDetailTab === 'reasoning' ? (
              drawerLoading ? (
                <div className="watchboard-empty">正在读取完整推演...</div>
              ) : detailStatus === 'missing' ? (
                <div className="watchboard-missing-reasoning">
                  <strong>暂无完整推演</strong>
                  <p>这只票还没有生成 V5 统一推演。卡片可显示实时价格和持仓，但路径主线需要先生成推演。</p>
                  <button type="button" onClick={runReasoningForSelected} disabled={reasoningRunning}>
                    {reasoningRunning ? '生成中...' : '生成统一推演'}
                  </button>
                </div>
              ) : (
                <div className="watchboard-reasoning-content">
                  <ReactMarkdown>{fullText || '暂无完整推演。'}</ReactMarkdown>
                </div>
              )
            ) : (
              <div className="watchboard-chat-panel">
                {chatMessages.length ? (
                  <div className="watchboard-chat-log" aria-live="polite" ref={chatLogRef}>
                    {chatMessages.map((message, index) => (
                      <article key={`${message.role}-${index}`} className={`watchboard-chat-message chat-${message.role}`}>
                        <span>{message.role === 'user' ? '你问' : '教练'}</span>
                        <ReactMarkdown>{message.content}</ReactMarkdown>
                      </article>
                    ))}
                    {chatLoading && (
                      <article className="watchboard-chat-message chat-assistant is-loading">
                        <span>教练</span>
                        <p>正在结合完整推演和当前价格...</p>
                      </article>
                    )}
                  </div>
                ) : (
                  <div className="watchboard-chat-empty">
                    <strong>问一句路径问题</strong>
                    <p>适合问“跌破哪里失效”“企稳看哪里”“现在是机会还是风险”。回答会在这里完整展开。</p>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="watchboard-chat">
            <div className="watchboard-quick">
              {QUICK_QUESTIONS.map((question) => (
                <button key={question} type="button" onClick={() => sendQuestion(question)} disabled={chatLoading}>
                  {question}
                </button>
              ))}
            </div>
            <div className={`watchboard-chat-status is-${chatStatus.tone}`} aria-live="polite">
              <span aria-hidden="true" />
              <strong>{chatStatus.label}</strong>
              <em>{chatStatus.detail}</em>
            </div>
            <form
              className="watchboard-chat-input"
              onSubmit={(event) => {
                event.preventDefault()
                sendQuestion()
              }}
            >
              <input
                value={chatInput}
                onChange={(event) => setChatInput(event.target.value)}
                placeholder={chatLoading ? '推演中，请稍等...' : '问这只票接下来怎么看'}
                disabled={chatLoading}
              />
              <button type="submit" disabled={chatLoading || !chatInput.trim()}>
                {chatLoading ? '...' : '发送'}
              </button>
            </form>
          </div>
        </aside>
      )}
    </div>
  )
}
