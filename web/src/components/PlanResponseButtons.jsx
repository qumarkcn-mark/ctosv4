const RESPONSES = [
  { value: 'ACKNOWLEDGED', label: '已知悉' },
  { value: 'EXECUTED', label: '已执行' },
  { value: 'CONTINUE_WATCHING', label: '继续观察' },
  { value: 'IGNORED', label: '已忽略' },
  { value: 'INVALIDATED', label: '标记失效' },
]

export default function PlanResponseButtons({ disabled, onRespond }) {
  return (
    <div className="plan-response-buttons">
      {RESPONSES.map((item) => (
        <button
          key={item.value}
          type="button"
          disabled={disabled}
          onClick={() => onRespond(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}
