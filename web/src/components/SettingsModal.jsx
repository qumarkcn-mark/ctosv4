import { useState, useEffect } from 'react'
import { API_BASE } from '../config.js'
import { apiFetch } from '../api/client.js'
import DataLakePanel from './DataLakePanel.jsx'
import './SettingsModal.css'

const EXPERT_MODE_STORAGE_KEY = 'ctos.expert_mode'

export default function SettingsModal({ onClose }) {
  const [activeTab, setActiveTab] = useState('settings') // 'settings' | 'lake'
  const [aiNativeProvider, setAiNativeProvider] = useState('deepseek')
  const [apiKey, setApiKey] = useState('')
  const [hasDeepseekApiKey, setHasDeepseekApiKey] = useState(false)
  const [qwenApiKey, setQwenApiKey] = useState('')
  const [hasQwenApiKey, setHasQwenApiKey] = useState(false)
  const [qwenBaseUrl, setQwenBaseUrl] = useState('https://dashscope.aliyuncs.com/compatible-mode/v1')
  const [qwenTradeParseModel, setQwenTradeParseModel] = useState('qwen-flash')
  const [qwenScreenshotOcrModel, setQwenScreenshotOcrModel] = useState('qwen-vl-ocr-latest')
  const [geminiApiKey, setGeminiApiKey] = useState('')
  const [hasGeminiApiKey, setHasGeminiApiKey] = useState(false)
  const [geminiModel, setGeminiModel] = useState('gemini-2.5-pro')
  const [geminiBaseUrl, setGeminiBaseUrl] = useState('https://generativelanguage.googleapis.com/v1beta/openai/')
  const [aiNativeModel, setAiNativeModel] = useState('')
  const [aiNativeThinkingEnabled, setAiNativeThinkingEnabled] = useState(true)
  const [aiNativeReasoningEffort, setAiNativeReasoningEffort] = useState('high')
  const [expertMode, setExpertMode] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    apiFetch(`${API_BASE}/auth/me/settings`)
      .then(r => r.json())
      .then(data => {
        const settings = data.settings || {}
        if (settings.ai_native_provider) setAiNativeProvider(settings.ai_native_provider)
        if (settings.deepseek_api_key_configured) setHasDeepseekApiKey(true)
        if (settings.qwen_api_key_configured) setHasQwenApiKey(true)
        if (settings.qwen_base_url) setQwenBaseUrl(settings.qwen_base_url)
        if (settings.qwen_trade_parse_model) setQwenTradeParseModel(settings.qwen_trade_parse_model)
        if (settings.qwen_screenshot_ocr_model) setQwenScreenshotOcrModel(settings.qwen_screenshot_ocr_model)
        if (settings.gemini_api_key_configured) setHasGeminiApiKey(true)
        if (settings.gemini_model) setGeminiModel(settings.gemini_model)
        if (settings.gemini_base_url) setGeminiBaseUrl(settings.gemini_base_url)
        if (settings.ai_native_model) setAiNativeModel(settings.ai_native_model)
        if (typeof settings.ai_native_thinking_enabled === 'boolean') {
          setAiNativeThinkingEnabled(settings.ai_native_thinking_enabled)
        }
        if (settings.ai_native_reasoning_effort) setAiNativeReasoningEffort(settings.ai_native_reasoning_effort)
        if (typeof settings.expert_mode === 'boolean') {
          setExpertMode(settings.expert_mode)
          writeExpertModePreference(settings.expert_mode)
        }
      })
      .catch(e => console.error(e))
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)
    try {
      const nextSettings = {
        ai_native_provider: aiNativeProvider,
        gemini_model: geminiModel.trim(),
        gemini_base_url: geminiBaseUrl.trim(),
        ai_native_model: aiNativeModel.trim(),
        ai_native_thinking_enabled: aiNativeThinkingEnabled,
        ai_native_reasoning_effort: aiNativeReasoningEffort,
        qwen_base_url: qwenBaseUrl.trim(),
        qwen_trade_parse_model: qwenTradeParseModel.trim(),
        qwen_screenshot_ocr_model: qwenScreenshotOcrModel.trim(),
        expert_mode: expertMode
      }
      if (apiKey.trim()) nextSettings.deepseek_api_key = apiKey.trim()
      if (qwenApiKey.trim()) nextSettings.qwen_api_key = qwenApiKey.trim()
      if (geminiApiKey.trim()) nextSettings.gemini_api_key = geminiApiKey.trim()

      const res = await apiFetch(`${API_BASE}/auth/me/settings`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          settings: nextSettings
        })
      })
      if (!res.ok) throw new Error('保存失败')
      writeExpertModePreference(expertMode)
      window.dispatchEvent(new CustomEvent('ctos:expert-mode-change', { detail: { expertMode } }))
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
                    <label>AI 教练显示模式</label>
                    <label className="settings-switch settings-switch-block">
                      <input
                        type="checkbox"
                        checked={expertMode}
                        onChange={(e) => setExpertMode(e.target.checked)}
                      />
                      <span>专家模式：优先显示结构证据和技术边界</span>
                    </label>
                    <small className="form-hint">关闭时优先展示自然语言判断，减少结构术语密度。</small>
                  </div>

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
                    <small className="form-hint">只影响 AI 教练问答，不影响交易记录和提醒。</small>
                  </div>

                  <div className="form-group">
                    <label>DeepSeek API Key</label>
                    <input 
                      type="password"
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder={hasDeepseekApiKey ? '已设置，留空则不修改' : 'sk-...'}
                      className="input mono"
                      autoComplete="new-password"
                    />
                    <small className="form-hint">此密钥将安全保存在本地用户的独立设定区中，用于支撑 Agent 引擎推演计算。</small>
                  </div>

                  <div className="settings-provider-panel">
                    <div className="form-group">
                      <label>Qwen API Key</label>
                      <input
                        type="password"
                        value={qwenApiKey}
                        onChange={(e) => setQwenApiKey(e.target.value)}
                        placeholder={hasQwenApiKey ? '已设置，留空则不修改' : 'sk-...'}
                        className="input mono"
                        autoComplete="new-password"
                      />
                      <small className="form-hint">用于交易文本解析和同花顺截图 OCR；AI 推理仍使用 DeepSeek。</small>
                    </div>

                    <div className="form-row">
                      <div className="form-group">
                        <label>交易解析模型</label>
                        <input
                          type="text"
                          value={qwenTradeParseModel}
                          onChange={(e) => setQwenTradeParseModel(e.target.value)}
                          placeholder="qwen-flash"
                          className="input mono"
                        />
                      </div>
                      <div className="form-group">
                        <label>截图 OCR 模型</label>
                        <input
                          type="text"
                          value={qwenScreenshotOcrModel}
                          onChange={(e) => setQwenScreenshotOcrModel(e.target.value)}
                          placeholder="qwen-vl-ocr-latest"
                          className="input mono"
                        />
                      </div>
                    </div>

                    <div className="form-group settings-inline-url">
                      <label>Qwen Base URL</label>
                      <input
                        type="text"
                        value={qwenBaseUrl}
                        onChange={(e) => setQwenBaseUrl(e.target.value)}
                        placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
                        className="input mono"
                      />
                      <small className="form-hint">后台只允许官方 DashScope OpenAI-compatible endpoint。</small>
                    </div>
                  </div>

                  <div className="settings-provider-panel">
                    <div className="form-group">
                      <label>Gemini API Key</label>
                      <input
                        type="password"
                        value={geminiApiKey}
                        onChange={(e) => setGeminiApiKey(e.target.value)}
                        placeholder={hasGeminiApiKey ? '已设置，留空则不修改' : 'AIza...'}
                        className="input mono"
                        autoComplete="new-password"
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

function writeExpertModePreference(enabled) {
  try {
    localStorage.setItem(EXPERT_MODE_STORAGE_KEY, enabled ? 'true' : 'false')
  } catch {
    // localStorage 只是前端即时偏好缓存，后端 settings_json 仍是持久来源。
  }
}
