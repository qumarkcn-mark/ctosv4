import { useState, useEffect } from 'react'
import { API_BASE } from '../config.js'
import DataLakePanel from './DataLakePanel.jsx'
import './SettingsModal.css'

export default function SettingsModal({ onClose }) {
  const [activeTab, setActiveTab] = useState('settings') // 'settings' | 'lake'
  const [aiNativeProvider, setAiNativeProvider] = useState('deepseek')
  const [apiKey, setApiKey] = useState('')
  const [geminiApiKey, setGeminiApiKey] = useState('')
  const [geminiModel, setGeminiModel] = useState('gemini-2.5-pro')
  const [geminiBaseUrl, setGeminiBaseUrl] = useState('https://generativelanguage.googleapis.com/v1beta/openai/')
  const [aiNativeModel, setAiNativeModel] = useState('')
  const [aiNativeThinkingEnabled, setAiNativeThinkingEnabled] = useState(true)
  const [aiNativeReasoningEffort, setAiNativeReasoningEffort] = useState('high')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    // 桌面开发模式下，强制锁定 user_id=1 
    fetch(`${API_BASE}/auth/user/1/settings`)
      .then(r => r.json())
      .then(data => {
        const settings = data.settings || {}
        if (settings.ai_native_radar_provider) setAiNativeProvider(settings.ai_native_radar_provider)
        if (settings.deepseek_api_key) setApiKey(settings.deepseek_api_key)
        if (settings.gemini_api_key) setGeminiApiKey(settings.gemini_api_key)
        if (settings.gemini_model) setGeminiModel(settings.gemini_model)
        if (settings.gemini_base_url) setGeminiBaseUrl(settings.gemini_base_url)
        if (settings.ai_native_radar_model) setAiNativeModel(settings.ai_native_radar_model)
        if (typeof settings.ai_native_radar_thinking_enabled === 'boolean') {
          setAiNativeThinkingEnabled(settings.ai_native_radar_thinking_enabled)
        }
        if (settings.ai_native_radar_reasoning_effort) setAiNativeReasoningEffort(settings.ai_native_radar_reasoning_effort)
      })
      .catch(e => console.error(e))
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      const res = await fetch(`${API_BASE}/auth/user/1/settings`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          settings: {
            ai_native_radar_provider: aiNativeProvider,
            deepseek_api_key: apiKey,
            gemini_api_key: geminiApiKey,
            gemini_model: geminiModel.trim(),
            gemini_base_url: geminiBaseUrl.trim(),
            ai_native_radar_model: aiNativeModel.trim(),
            ai_native_radar_thinking_enabled: aiNativeThinkingEnabled,
            ai_native_radar_reasoning_effort: aiNativeReasoningEffort
          }
        })
      })
      if (!res.ok) throw new Error('保存失败')
      setMessage({ type: 'success', text: '保存成功！' })
      setTimeout(() => onClose(), 1500)
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="settings-overlay">
      <div className="settings-modal flex flex-col">
        <div className="settings-header">
          <h2>系统设置</h2>
          <button className="settings-close" onClick={onClose}>×</button>
        </div>

        {/* Tab 切换 */}
        <div className="settings-tabs">
          <button
            className={`settings-tab ${activeTab === 'settings' ? 'active' : ''}`}
            onClick={() => setActiveTab('settings')}
          >
            ⚙️ 基础设置
          </button>
          <button
            className={`settings-tab ${activeTab === 'lake' ? 'active' : ''}`}
            onClick={() => setActiveTab('lake')}
          >
            📊 数据湖
          </button>
        </div>
        
        <div className="settings-body">
          {/* Tab 1: 基础设置 */}
          {activeTab === 'settings' && (
            <>
              {loading ? (
                <div className="settings-loading">加载中...</div>
              ) : (
                <div className="settings-form">
                  <div className="form-group">
                    <label>AI Native 模型供应商</label>
                    <div className="settings-segmented" role="tablist" aria-label="AI Native provider">
                      <button
                        type="button"
                        className={aiNativeProvider === 'deepseek' ? 'active' : ''}
                        onClick={() => setAiNativeProvider('deepseek')}
                      >
                        DeepSeek
                      </button>
                      <button
                        type="button"
                        className={aiNativeProvider === 'gemini' ? 'active' : ''}
                        onClick={() => setAiNativeProvider('gemini')}
                      >
                        Gemini
                      </button>
                    </div>
                    <small className="form-hint">只影响 AI Native Free Reasoning 推演，不影响旧雷达。</small>
                  </div>

                  <div className="form-group">
                    <label>DeepSeek API Key</label>
                    <input 
                      type="password"
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder="sk-..." 
                      className="input mono"
                    />
                    <small className="form-hint">此密钥将安全保存在本地用户的独立设定区中，用于支撑 Agent 引擎推演计算。</small>
                  </div>

                  <div className="settings-provider-panel">
                    <div className="form-group">
                      <label>Gemini API Key</label>
                      <input
                        type="password"
                        value={geminiApiKey}
                        onChange={(e) => setGeminiApiKey(e.target.value)}
                        placeholder="AIza..."
                        className="input mono"
                      />
                      <small className="form-hint">用于 Google Gemini OpenAI-compatible Chat Completions。</small>
                    </div>

                    <div className="form-row">
                      <div className="form-group">
                        <label>Gemini 模型</label>
                        <input
                          type="text"
                          value={geminiModel}
                          onChange={(e) => setGeminiModel(e.target.value)}
                          placeholder="gemini-2.5-pro"
                          className="input mono"
                        />
                      </div>
                      <div className="form-group">
                        <label>Gemini Base URL</label>
                        <input
                          type="text"
                          value={geminiBaseUrl}
                          onChange={(e) => setGeminiBaseUrl(e.target.value)}
                          placeholder="https://generativelanguage.googleapis.com/v1beta/openai/"
                          className="input mono"
                        />
                      </div>
                    </div>
                  </div>

                  <div className="form-group">
                    <label>AI Native 推演模型</label>
                    <input
                      type="text"
                      value={aiNativeModel}
                      onChange={(e) => setAiNativeModel(e.target.value)}
                      placeholder="deepseek-v4-pro"
                      className="input mono"
                    />
                    <small className="form-hint">DeepSeek 模式使用。Gemini 模式会使用上方 Gemini 模型。</small>
                  </div>

                  <div className="form-group">
                    <label>AI Native 思考模式</label>
                    <label className="settings-switch">
                      <input
                        type="checkbox"
                        checked={aiNativeThinkingEnabled}
                        onChange={(e) => setAiNativeThinkingEnabled(e.target.checked)}
                      />
                      <span>启用 DeepSeek thinking</span>
                    </label>
                    <select
                      value={aiNativeReasoningEffort}
                      onChange={(e) => setAiNativeReasoningEffort(e.target.value)}
                      className="input mono"
                      disabled={!aiNativeThinkingEnabled}
                    >
                      <option value="high">high</option>
                      <option value="max">max</option>
                    </select>
                    <small className="form-hint">用于 deepseek-v4-pro 的推理强度。一般用 high，复杂样本校准时可切 max。</small>
                  </div>

                  {message && (
                    <div className={`settings-msg ${message.type}`}>
                      {message.text}
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {/* Tab 2: 数据湖概览 */}
          {activeTab === 'lake' && (
            <DataLakePanel />
          )}
        </div>

        {activeTab === 'settings' && (
          <div className="settings-footer">
            <button className="btn" onClick={onClose} disabled={saving}>取消</button>
            <button className="btn btn-primary" onClick={handleSave} disabled={loading || saving}>
              {saving ? '保存中...' : '确认保存'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
