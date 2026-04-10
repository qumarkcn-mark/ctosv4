# 代码审查底线清单 (CT-OS V4.0)

执行 `/review` 时，必须严格按照以下清单对差分代码 (Diff) 进行逐项核对：

## 1. 数据库安全与性能 (DB Safety)
- **SQL 注入防范**: 所有 SQL 查询必须使用参数化绑定（如 `?` 或 `:%s`），绝对禁止用 f-string 或 `+` 拼接变量。
- **SQLite 锁死预防**: `INSERT`/`UPDATE`/`DELETE` 后是否正确执行了 `conn.commit()`？是否在 `finally` 块中确保 `conn.close()` 被调用？
- **异步阻塞陷阱 (Critical)**: **在 `async def` 路由中严禁直接执行耗时的同步代码**！由于项目中使用了同步的 `sqlite3` (`get_connection()`)，如果路由声明为 `async def`，则 DB 执行会卡死整个 asyncio 事件循环主线程。
  - *修复方案*: 将只进行同步 DB 调用的接口改为 `def`（FastAPI 会自动分配到线程池执行）；或使用 `run_in_threadpool`。

## 2. 外部调用的可靠性 (Network & Reliability)
- **超时控制**: 添加 `httpx.AsyncClient` 调用时，是否都设置了合理的 `timeout`（避免永远挂起）？
- **容错处理**: 行情获取 (腾讯 API) 或微信接口请求失败时，是否有 `try/except` 包裹并记录错误日志？是否会导致业务崩溃？
- **失败静默**: 在 Worker 或轮询任务中，网络失败是否会中断整个 `while` 循环？必须在循环内部做好异常消化。

## 3. 业务与架构纪律 (Architecture Constraints)
- **信任边界 (Trust Boundary)**: 用户传递的价格、数量是否有可能为负数（需加校验）？
- **原子性**: 若涉及跨表修改（如既插入 `trades` 又更新持仓），是否包裹在同一事务中一次性 `commit()`？
- **魔法数字**: 止损倍数 (如 3.0)、重仓判定标准 (如 0.20) 是否抽离为了容易修改的常量或配置项，还是硬编码在逻辑深处？
