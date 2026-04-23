import { useState, useRef } from 'react'
import './VoiceInput.css'

/**
 * 自然语言快速录入组件
 * 输入口语化描述 → DeepSeek V3 解析 → 确认卡片填入表单
 *
 * 注：Web Speech API 在 http dev 环境权限受限，改为文字输入模式
 */
export default function VoiceInput({ onFill }) {
  const [text, setText] = useState('')
  const [state, setState] = useState('idle')  // idle | parsing | done | error
  const [parsed, setParsed] = useState(null)
  const [errorMsg, setErrorMsg] = useState('')
  const inputRef = useRef(null)

  const parse = async () => {
    const q = text.trim()
    if (!q) return
    setState('parsing')
    setErrorMsg('')
    try {
      const resp = await fetch('/api/trades/from-text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: q }),
      })
      const data = await resp.json()
      if (data.status === 'ok' && data.parsed) {
        setParsed(data.parsed)
        setState('done')
      } else {
        throw new Error('解析结果为空')
      }
    } catch (err) {
      setErrorMsg(`解析失败：${err.message}`)
      setState('error')
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); parse() }
    if (e.key === 'Escape') handleReset()
  }

  const handleConfirm = () => {
    if (parsed && onFill) {
      onFill(parsed)
      handleReset()
    }
  }

  const handleReset = () => {
    setState('idle')
    setText('')
    setParsed(null)
    setErrorMsg('')
    setTimeout(() => inputRef.current?.focus(), 50)
  }

  const dirLabel = parsed?.direction === 'BUY' ? '买入' : '卖出'
  const dirClass = parsed?.direction === 'BUY' ? 'buy' : 'sell'

  return (
    <div className="voice-input-wrap">
      {/* 输入栏 */}
      <div className={`nlp-input-bar ${state}`}>
        <span className="nlp-icon" title="AI 解析录入">✍️</span>
        <input
          ref={inputRef}
          className="nlp-input"
          type="text"
          value={text}
          onChange={e => { setText(e.target.value); if (state !== 'idle') setState('idle') }}
          onKeyDown={handleKeyDown}
          placeholder="口语描述，如：买100股茅台1780块"
          disabled={state === 'parsing'}
          autoComplete="off"
        />
        {state === 'parsing' ? (
          <span className="nlp-spinner" />
        ) : text.trim() ? (
          <button
            type="button"
            className="nlp-send-btn"
            onClick={parse}
            title="解析（Enter）"
          >
            解析 ↵
          </button>
        ) : null}
      </div>

      {/* 错误 */}
      {state === 'error' && (
        <div className="nlp-error">{errorMsg}</div>
      )}

      {/* 解析结果确认卡片 */}
      {state === 'done' && parsed && (
        <div className="voice-result-card animate-fade-in">
          <div className="voice-result-header">
            <span className="voice-result-text">"{text}"</span>
            <span className="voice-result-conf">置信度 {Math.round((parsed.confidence || 0) * 100)}%</span>
          </div>
          <div className="voice-result-body">
            <span className={`voice-dir-badge ${dirClass}`}>{dirLabel}</span>
            <span className="voice-result-name">{parsed.name || '未识别股票'}</span>
            {parsed.price && <span className="voice-result-field">¥{parsed.price}</span>}
            {parsed.quantity && <span className="voice-result-field">{parsed.quantity}股</span>}
          </div>
          <div className="voice-result-actions">
            <button type="button" className="btn-voice-confirm" onClick={handleConfirm}>
              ✓ 确认填入
            </button>
            <button type="button" className="btn-voice-cancel" onClick={handleReset}>
              ✕ 重新输入
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
