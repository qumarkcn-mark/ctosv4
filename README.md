# CT-OS V4.0 交易教练

记录交易 · 纠正行为 · 提醒机会

## 产品定位

CT-OS V4.0 是基于缠论的 A 股交易教练平台。用户在券商 App 交易，CT-OS 负责记录、分析和提醒。

## 六把武器

1. **仓位透视镜** — 持仓集中度可视化 + 预警
2. **止损看门狗** — ATR 止损监控 + 推送
3. **持仓信心锚** — 缠论趋势确认 + 追踪止损
4. **级别雷达** — 多级别方向检查
5. **推演沙盘** — 缠论完全分类生成
6. **趋势顺风** — 逆势/回本陷阱警报

## 技术栈

- **后端:** Python 3.11+ / FastAPI / SQLite
- **桌面端:** React 19 / Vite
- **移动端:** 微信小程序
- **部署:** 腾讯云轻量 / Docker

## 快速开始

```bash
# 后端
cd ct-os-v4
cp .env.example .env
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn server.app:app --reload

# 前端
cd web
npm install
npm run dev
```
