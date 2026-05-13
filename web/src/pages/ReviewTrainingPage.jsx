import BehaviorReport from './BehaviorReport.jsx'
import './ReviewTrainingPage.css'

export default function ReviewTrainingPage() {
  return (
    <div className="review-training-page">
      <header className="review-training-header">
        <div>
          <h2>复盘训练</h2>
          <p>盘后复盘纪律偏差，训练计划内交易反应。</p>
        </div>
      </header>

      <section className="review-training-body review-training-body--behavior">
        <BehaviorReport />
      </section>
    </div>
  )
}
