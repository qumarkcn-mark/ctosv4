# Windows Codex Context for CT-OS V4.0

> 给 Windows 电脑上的 Codex 读取。
> 这台 Windows 机器同时装有 Codex、QMT、通达信 TDX。它的任务是作为 CT-OS 的本地行情/交易环境，不是把 CT-OS 变成自动交易机器人。

## 一句话

CT-OS V4.0 是 A 股交易教练。

它帮助用户记录交易、纠正行为、提醒风险和机会。用户仍然在券商 App 或 QMT 里做最终交易决定。CT-OS 默认不下单。

涉及市场判断、交易计划、雷达推演、止损提醒、选股扫描的内容，都必须附带“仅供参考，不构成投资建议”的边界。

## 产品定位

CT-OS 不是交易机器人，也不是券商终端替代品。

它做三件事：

1. **记录**：记录用户交易、持仓、成本、交易理由、买卖纪律。
2. **纠正**：用历史行为发现问题，例如止损不执行、赢的卖太早、逆势补仓、回本陷阱。
3. **提醒**：根据价格、ATR、缠论结构、级别雷达和持仓状态提醒用户注意风险或窗口。

用户真正下单的位置：

- 现在：券商 App / QMT 手动下单。
- 未来 Phase 3：只允许在私有部署里，通过严格的 Execution Intent + Risk Gate + Windows QMT Agent 流程做盘中 T。默认仍然关闭。

## 六把武器

1. **仓位透视镜**：持仓集中度可视化，小票堆积预警。
2. **止损看门狗**：ATR 止损监控，触发推送，记录纪律惩罚数据。
3. **持仓信心锚**：缠论趋势确认，结合 ATR 追踪止损。
4. **级别雷达**：多级别方向检查，当前主链是 `day -> 30 -> 5`，盘中触发链是 `30 -> 5 -> 1`。
5. **推演沙盘**：缠论完全分类推演，展示路径、边界、触发、失效。
6. **趋势顺风**：逆势建仓警告，回本陷阱提醒。

## 当前技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+ / FastAPI / SQLite |
| 前端 | React 19 / Vite |
| 移动端 | 微信小程序 |
| 行情 | BaoStock / Tencent / TDX local / QMT XtQuant |
| QMT 桥 | Windows 本机 FastAPI 服务，默认端口 `8765` |
| 部署 | 腾讯云轻量 / Docker Compose |

## 关键目录

```text
ct-os-v4/
├── server/                  # FastAPI 后端
├── web/                     # React 桌面端
├── miniprogram/             # 微信小程序
├── qmt_bridge/              # Windows QMT 只读行情桥
├── scripts/                 # TDX 导入和同步脚本
├── tests/                   # pytest 测试
├── data/                    # SQLite 数据库和行情湖
├── docs/                    # 架构/契约文档
├── AGENTS.md                # 项目开发规则
├── DESIGN.md                # 设计系统
└── WINDOWS_CODEX_CONTEXT.md # 本文件
```

Windows 上的 Codex 开始工作前，至少先读：

1. `AGENTS.md`
2. `README.md`
3. `docs/DATA_SOURCE_CONTRACT.md`
4. `docs/QMT_EXECUTION_ARCHITECTURE.md`
5. `WINDOWS_CODEX_CONTEXT.md`

如果涉及 UI，还必须读 `DESIGN.md`。

## 数据源边界

CT-OS 最容易出问题的地方是混用行情源。不要乱混。

| 数据源 | 当前角色 | 是否能驱动正式缠论结构 |
|---|---|---|
| BaoStock / K 线事实层 | 正式 CZSC snapshot 输入 | 可以 |
| TDX `.day` | 全市场日线事实源 | 不直接驱动 V5 正式结构 |
| TDX `.lc1` 1分钟 | 本地 1 分钟展示/历史回放补充 | 不可以 |
| Tencent | 当前价、持仓盈亏、轻量预览、普通价格提醒 | 不可以 |
| QMT / XtQuant | Windows 私有只读实时行情，现在只做 preview | 不可以 |
| `data/ctos.db` | 用户、交易、持仓、提醒、策略配置、教练事件 | 不存大行情历史 |

正式规则：

- V5 正式结构只来自 CZSC snapshot，不保留旧 `chan.py` / radar fallback。
- CZSC snapshot 使用 K 线事实层输入，不混用旧结构输出。
- UI 当前价和持仓盈亏可用 Tencent。
- QMT 现在只做只读实时行情桥，不下单。
- TDX 本地 1 分钟只做展示/回放，不确认 V5 正式结构。
- BaoStock 前复权结构价不能拿去当 QMT 委托价。

## 符号格式

CT-OS 内部统一格式：

```text
sh.600519
sz.000001
```

常见外部格式：

| 系统 | 格式 | 示例 |
|---|---|---|
| CT-OS 内部 | `{market}.{code}` | `sh.600519` |
| Tencent | `{market}{code}` | `sh600519` |
| TDX 文件 | `{market}{code}.day` / `{market}{code}.lc1` | `sh600519.day` |
| QMT | `{code}.{MARKET}` | `600519.SH` |

`qmt_bridge/symbols.py` 已经有转换函数。不要再写第二套 symbol 解析逻辑。

## Windows QMT 的角色

Windows 机器上的 QMT 目前是只读行情源。

允许：

- 健康检查
- 实时报价
- 订阅行情
- 读取已闭合的分钟 K 线
- 给前端盘中 preview 使用

禁止：

- 在 Phase 1/2 自动下单
- 让 Strategy、Radar、LLM 直接调用 QMT 下单
- 让 CT-OS Core 直接调用 XtQuant
- 把 QMT 数据伪装成正式 BaoStock 结构
- 对公网暴露 QMT 桥的未鉴权入口

未来如果做私有 Phase 3 盘中 T，唯一允许路径是：

```text
Strategy candidate
  -> Execution Intent
  -> Risk Gate
  -> approved intent
  -> Windows QMT Agent
  -> QMT Adapter
  -> QMT / XtQuant
  -> Execution Audit Log
```

任何 live execution 都必须有 Risk Gate、kill switch、dry-run 资格、审计日志和用户显式授权。

## 启动 QMT 只读桥

在 Windows 项目根目录：

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

如果 Windows 已经安装 QMT/XtQuant，并且 Python 能 import `xtquant`：

```powershell
$env:QMT_BRIDGE_PROVIDER = "xtdata"
uvicorn qmt_bridge.app:app --host 127.0.0.1 --port 8765
```

没有 QMT 或调试接口时，用 fake provider 验证链路：

```powershell
$env:QMT_BRIDGE_PROVIDER = "fake"
uvicorn qmt_bridge.app:app --host 127.0.0.1 --port 8765
```

健康检查：

```powershell
curl http://127.0.0.1:8765/health
curl "http://127.0.0.1:8765/quotes?symbols=sh.600519,sz.000001"
curl "http://127.0.0.1:8765/klines?symbol=sh.600519&period=5m&limit=20"
```

QMT 桥 API：

| Endpoint | 用途 |
|---|---|
| `GET /health` | provider 状态 |
| `POST /subscribe` | 订阅 symbols / periods |
| `GET /quotes?symbols=sh.600519,sz.000001` | 实时报价 |
| `GET /klines?symbol=sh.600519&period=5m&limit=240` | 分钟 K 线 |
| `GET /klines/latest?symbol=sh.600519&period=5m` | 最新 K 线 |

## 连接 CT-OS 后端到 Windows QMT 桥

如果后端也跑在 Windows 本机：

```powershell
$env:QMT_BRIDGE_URL = "http://127.0.0.1:8765"
uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload
```

如果后端跑在 Mac / 云服务器，Windows QMT 桥在局域网：

```bash
QMT_BRIDGE_URL=http://WINDOWS_LAN_IP:8765 uvicorn server.app:app --host 0.0.0.0 --port 8000
```

安全要求：

- 局域网测试可以临时用 `0.0.0.0:8765`，但只允许可信网络访问。
- 不要把 QMT bridge 直接暴露到公网。
- 如果将来要远程连接，先做 agent token、IP 白名单、TLS 或内网隧道。

## TDX 在 Windows 上的角色

TDX 用于本地历史行情文件。

CT-OS 当前读取：

- 日线 `.day`：用于全市场 discovery facts，写入 `data/tdx_lake.db`。
- 1 分钟 `.lc1`：用于 K 线展示和历史回放补充，不能形成正式 CZSC 结构。

常见 TDX 路径形态：

```text
D:\new_tdx\vipdoc\sh\lday\sh600519.day
D:\new_tdx\vipdoc\sz\lday\sz000001.day
D:\new_tdx\vipdoc\sh\minline\sh600519.lc1
D:\new_tdx\vipdoc\sz\minline\sz000001.lc1
```

如果后端跑在 Windows，设置：

```powershell
$env:TDX_VIPDOC = "D:\new_tdx\vipdoc"
```

如果后端跑在 Mac，而 TDX 在 Windows，推荐把 Windows 的 `vipdoc` 共享出来，然后在 Mac 挂载成：

```text
/Volumes/tdx_vipdoc
```

现有脚本默认使用这个路径。

## TDX 日线导入

全量导入：

```bash
python scripts/import_tdx_daily.py
```

盘后增量：

```bash
python scripts/update_tdx_daily.py
```

写入数据库：

```text
data/tdx_lake.db
```

重要边界：

- 只导入 A 股正股。
- TDX 日线只作为 discovery facts。
- AI Native V5 的正式结构快照必须来自 BaoStock K 线 + CZSC 计算。

## 本地开发命令

后端：

```bash
python3.11 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload
```

Windows PowerShell：

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload
```

前端：

```bash
cd web
npm install
npm run dev
```

测试：

```bash
pytest tests/ -v
```

QMT bridge 相关测试：

```bash
pytest tests/test_qmt_bridge_contract.py -v
```

如果某个测试文件不存在，先用 `rg "qmt_bridge|QMT_BRIDGE|xtdata" tests server qmt_bridge` 找当前实际测试名。

## 开发规则

Windows Codex 必须遵守项目根目录 `AGENTS.md`。

高频规则摘要：

- 中文交流，代码和技术术语用英文。
- Bug 修复先调查根因，不要盲修。
- 新功能至少先做工程计划审查。
- 涉及 UI 先读 `DESIGN.md`。
- 原生 SQL，不用 ORM。
- 行情查询、持仓计算、ATR 计算只允许唯一实现。
- 涉及交易建议必须写“仅供参考”。
- 不要手动推送代码，发布走 `/ship`。
- 浏览器 QA 走 gstack `/browse` 或项目指定 QA 流程。

## Windows Codex 接到任务时怎么判断方向

如果用户说：

- “QMT 连不上”：先检查 `qmt_bridge`、`QMT_BRIDGE_PROVIDER`、`xtquant` import、端口 `8765`、`/health`。
- “TDX 数据不对”：先检查 `TDX_VIPDOC`、文件路径、`.day/.lc1` 是否存在、`tdx_lake.db` 和 sync meta。
- “雷达结果不对”：先确认数据源是不是 BaoStock 正式结构，不要用 Tencent/TDX/QMT 代替正式结构。
- “盘中 preview 不更新”：检查 QMT bridge 或 Tencent preview，不要改正式 Chan 结构逻辑。
- “想自动下单”：默认拒绝直接实现，先回到 `docs/QMT_EXECUTION_ARCHITECTURE.md` 和 `docs/EXECUTION_INTENT_CONTRACT.md`，必须做私有 Phase 3 风险门。

## 绝对不要做

- 不要让 LLM 输出直接变成订单。
- 不要让前端按钮直接调用 QMT 下单。
- 不要让 Strategy/Radar 直接 import `xtquant`。
- 不要把 QMT bridge 开到公网裸奔。
- 不要把 Tencent、TDX 1m、QMT preview 数据写成正式缠论结构。
- 不要用前复权结构价做真实委托价。
- 不要为了演示绕过 freshness / stale 检查。

## 当前最重要的架构原则

CT-OS 的价值不在“替用户交易”，而在“让用户变得更守纪律”。

QMT 和 TDX 是 Windows 上的本地能力：

- TDX 给全市场扫描和本地回放提供数据。
- QMT 给私有实时盘中预览提供数据。
- CT-OS Core 负责产品状态、结构推演、行为纠偏和提醒。
- 用户负责最终交易动作。

这条边界守住，项目就不会从交易教练滑成危险的黑盒交易机器人。
