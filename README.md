# CT-OS V4.0 交易教练

记录交易 · 纠正行为 · 提醒机会

## 产品定位

CT-OS V4.0 是基于缠论的 A 股交易教练平台。用户在券商 App 交易，CT-OS 负责记录、分析和提醒。

## 六把武器

1. **仓位透视镜** — 持仓集中度可视化 + 预警
2. **止损看门狗** — ATR 止损监控 + 推送
3. **持仓信心锚** — 缠论趋势确认 + 追踪止损
4. **级别雷达** — 多级别走势推演 + A/B/C 完全分类
5. **推演沙盘** — 缠论路径边界、触发、失效条件
6. **趋势顺风** — 逆势/回本陷阱警报

## 雷达与实时数据

当前雷达已经拆成两条链：

```text
主推演链：day -> 30 -> 5
盘中触发链：30 -> 5 -> 1
```

数据源边界：

- BaoStock：正式 CChan 多级别结构源。
- Tencent：当前价、持仓盈亏和轻量预览。
- QMT / XtQuant：Windows 侧只读实时行情网关，给盘中预览使用。
- TDX 本地 1分钟：Kline 展示和历史回放补充源，不确认雷达主推演。

涉及市场判断的内容仅供参考，不构成投资建议。CT-OS 是交易教练，不是交易机器人。

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

# 后端带本地 TDX/QMT 配置示例
TDX_VIPDOC=/Users/markqu/Desktop/tdx_vipdoc_mount \
QMT_BRIDGE_URL=http://192.168.100.157:8765 \
uvicorn server.app:app --host 0.0.0.0 --port 8000

# 前端
cd web
npm install
npm run dev
```
