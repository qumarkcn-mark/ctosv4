import { useState, useEffect } from 'react'
import { API_BASE } from '../config.js'
import DataLakePanel from './DataLakePanel.jsx'
import './SettingsModal.css'

export default function SettingsModal({ onClose }) {
  const [activeTab, setActiveTab] = useState('settings') // 'settings' | 'lake'
  const [apiKey, setApiKey] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    // 桌面开发模式下，强制锁定 user_id=1 
    fetch(`${API_BASE}/auth/user/1/settings`)
      .then(r => r.json())
      .then(data => {
        if (data.settings && data.settings.deepseek_api_key) {
          setApiKey(data.settings.deepseek_api_key)
        }
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
          settings: { deepseek_api_key: apiKey }
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

