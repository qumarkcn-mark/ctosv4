import { useState } from 'react'
import BehaviorReport from './BehaviorReport.jsx'
import SandTable from './SandTable.jsx'
import AIReviewPanel from './AIReviewPanel.jsx'
import AITrainingReportPanel from './AITrainingReportPanel.jsx'
import './ReviewTrainingPage.css'

const TABS = [
  { id: 'behavior', label: '行为体检' },
  { id: 'ai-review', label: 'AI 推演复盘' },
  { id: 'ai-training', label: 'AI 训练报告' },
  { id: 'training', label: '模拟训练' },
]

export default function ReviewTrainingPage() {
  const [activeTab, setActiveTab] = useState('behavior')

  return (
    <div className="review-training-page">
      <header className="review-training-header">
        <div>
          <h2>复盘训练</h2>
          <p>盘后复盘纪律偏差，训练计划内交易反应。</p>
        </div>
        <div className="review-training-tabs" role="tablist" aria-label="复盘训练视图">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={activeTab === tab.id ? 'is-active' : ''}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </header>

      <section className={`review-training-body review-training-body--${activeTab}`}>
        {activeTab === 'behavior' && <BehaviorReport />}
        {activeTab === 'ai-review' && <AIReviewPanel />}
        {activeTab === 'ai-training' && <AITrainingReportPanel />}
        {activeTab === 'training' && <SandTable />}
      </section>
    </div>
  )
}
