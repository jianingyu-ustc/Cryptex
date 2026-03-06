# 现货自动交易子系统 (Spot Auto Trading)

`spot/` 子系统默认 `dry-run`，支持统一决策引擎在三种模式复用：

- `scan`: 单次扫描
- `monitor`: 实时轮询 dry-run/live
- `backtest`: 历史回测（最少 3 年窗口）

同时新增 GA 参数优化模式：`--optimize-ga`。

## 1. 统一决策引擎

核心入口：`spot/strategy.py` -> `SpotDecisionEngine.decide(context, params)`

- `DecisionContext`（定义于 `spot/models.py`）包含：
  - symbol、当前 bar OHLCV
  - 最近 N 根 klines
  - 24h quote volume
  - 仓位/组合状态（entry/stop/max、现金、净值、日初净值）
  - 执行成本快照（`fee_bps` / `slippage_bps`，用于入场成本门槛判断）
  - 衍生数据快照与序列（与 spot bar 对齐）：
    - `funding_rate` / `funding_rate_series`
    - `premium_kline_series` / `premium_close`
    - `mark_kline_series` / `mark_price_close`
  - `decision_timing`（`on_close` / `intrabar`）
- `StrategyParams`（定义于 `spot/config.py`）支持结构约束修复：
  - `slow_ma_len >= 2 * fast_ma_len`
  - `trail_atr_k >= atr_k`
  - `rsi_buy_min < rsi_buy_max`
  - `band_atr_k >= 0`、`ma_breakout_band >= 0`，且两者不会同时为 0（自动修复）
  - `cost_buffer_k > 0`

所有 BUY/HOLD/SELL 都输出 `reasons` 原因链，回测与实时 dry-run 共用同一套逻辑。

衍生数据对齐规则（backtest / monitor / live 统一）：

- 以 spot bar 时间为主时钟
- mark/premium kline：最近时间点匹配并 forward-fill
- funding rate：按 funding_time 最近匹配并 forward-fill
- 若某类衍生数据暂无可用来源，会在 `derivatives_state_ok` 中标记 `missing=...`，策略降级为“仅对可用数据生效”的同一路径逻辑

## 2. 策略逻辑（趋势回撤入场 + ATR 风控）

实现文件：`spot/strategy.py`

### 2.1 入场 BUY

需同时满足：

- 趋势过滤：`fast_ma > slow_ma`
- 回撤入场：最近价格回踩 `fast_ma` 附近（`pullback_tol`）
- 带宽确认（二选一）：
  - `close >= fast_ma + band_atr_k * ATR`
  - `close >= fast_ma * (1 + ma_breakout_band)`
- 成本门槛过滤：预计可捕捉空间必须覆盖双边成本与缓冲
  - `expected_edge = max(ATR/close, (fast_ma-slow_ma)/close)`
  - `required_edge = 2*(fee_bps+slippage_bps)/10000 * cost_buffer_k + funding_rate*funding_cost_buffer_k + min_edge_over_cost`
  - 仅当 `expected_edge >= required_edge` 且 `ATR/close >= min_atr_pct` 才允许开仓
- Derivatives State Gate（仅使用 Funding / Premium / Mark）：
  - Mark/spot 偏离过滤：`abs(mark-spot)/spot <= max_mark_spot_gap_pct`
  - GA 约束偏离过滤：`abs(mark-spot)/spot <= max_mark_spot_diverge`
  - Premium 过热过滤（二选一）：
    - `abs(premium_close) <= premium_abs_entry_max`
    - `premium_z in [premium_z_entry_min, premium_z_entry_max]`
  - Premium 极值上限：`abs(premium_close) <= premium_abs_max`
  - Funding 拥挤上限：`funding_rate <= funding_long_max`
- RSI 区间：`rsi_buy_min <= RSI <= rsi_buy_max`
- 市场状态过滤：优先 `ADX(14) >= adx_min`，否则使用趋势强度 proxy
- 流动性过滤：`24h quote volume >= min_24h_quote_volume`

### 2.2 出场 SELL

任一触发：

- ATR 初始止损：`price <= stop_price`，`stop_price = entry - atr_k * ATR`
- ATR 追踪止盈：`price <= max_price - trail_atr_k * ATR`
- 趋势转弱：`fast_ma < slow_ma and RSI <= rsi_sell_min`
- 衍生增强（参数化，可关闭）：
  - 紧急离场：`abs(mark-spot)/spot >= max_mark_spot_gap_exit`
  - 过热减仓：盈利状态下若 `funding_rate` 与 `|premium|` 同时超过阈值，则触发 `overheat_derisk_exit`

### 2.3 指标解释（本策略使用）

- `MA（移动平均）`  
  - 定义：`fast_ma` 和 `slow_ma` 分别是快慢均线。  
  - 作用：判断方向性趋势。`fast_ma > slow_ma` 视为上行趋势，`fast_ma < slow_ma` 视为趋势转弱。  
  - 参数：`fast_ma_len`、`slow_ma_len`。

- `RSI（相对强弱指数）`  
  - 定义：衡量一段窗口内上涨与下跌强度的振荡指标，取值区间 `[0, 100]`。  
  - 作用：做入场区间过滤与弱势离场过滤。  
  - 入场：`rsi_buy_min <= RSI <= rsi_buy_max`。  
  - 出场：`RSI <= rsi_sell_min`（配合 `fast_ma < slow_ma`）。  
  - 参数：`rsi_len`、`rsi_buy_min/max`、`rsi_sell_min`。

- `ATR（平均真实波幅）`  
  - 定义：衡量波动率，不判断方向，只反映“波动有多大”。  
  - 作用：把止损/追踪止盈与市场波动绑定，避免固定百分比过紧或过松。  
  - 初始止损：`entry - atr_k * ATR`。  
  - 追踪止盈：`max_price - trail_atr_k * ATR`。  
  - 参数：`atr_len`、`atr_k`、`trail_atr_k`。

- `ADX（平均趋向指数）`  
  - 定义：衡量趋势强弱（非方向），数值越高代表趋势越强。  
  - 作用：市场状态过滤，避免在无趋势或震荡期频繁试错。  
  - 条件：`ADX >= adx_min` 才允许开仓。  
  - 参数：`adx_len`、`adx_min`。

- `趋势强度 proxy`  
  - 定义：`abs(fast_ma - slow_ma) / close`。  
  - 作用：当 ADX 不可用时，作为趋势强度替代过滤条件。  
  - 条件：`trend_strength >= trend_strength_min`。  
  - 参数：`trend_strength_min`。

- `24h quote volume（24小时成交额）`  
  - 定义：过去 24h 的成交额估计值。  
  - 作用：流动性过滤，避免成交稀疏标的导致滑点和执行偏差扩大。  
  - 条件：`quote_volume_24h >= min_24h_quote_volume`。  
  - 参数：`min_24h_quote_volume`。

- `Breakout Band（二选一确认）`
  - ATR 带宽：`close >= fast_ma + band_atr_k*ATR`
  - 百分比带宽：`close >= fast_ma*(1+ma_breakout_band)`
  - 优点：
    - ATR 带宽：自适应波动率，在高波动阶段减少“假突破”噪声
    - 百分比带宽：尺度稳定、易解释，跨品种对比直观
  - 缺点：
    - ATR 带宽：ATR 突增时阈值抬高，可能错过早段趋势
    - 百分比带宽：不感知实时波动，极端波动下容易过松或过紧
  - 兼容性：`confirm_breakout` 仍保留为历史参数别名，会映射到 `ma_breakout_band`

- `入场成本门槛参数`
  - `min_edge_over_cost`：在成本之上要求的最小额外优势
  - `cost_buffer_k`：对双边成本的安全缓冲倍数
  - `min_atr_pct`：最低波动率门槛（`ATR/close`）
  - `funding_cost_buffer_k`：funding 对 required_edge 的放大系数
  - reasons：若不通过，会输出如  
    - `min_atr_pct_fail:atr=...<min=...`  
    - `edge_over_cost_fail:expected=...,required=...,cost=...,funding=...,buffer=...`

- `Derivatives Gate reasons`（示例）
  - `mark_spot_gap_fail:...`
  - `mark_spot_diverge_fail:...`
  - `premium_extreme_fail:...`
  - `premium_overheat_fail:...`
  - `funding_too_high_fail:...`

补充：GA 优化只会搜索上述指标相关参数，不会改变指标定义和 BUY/SELL 规则结构。

## 3. 执行、成本与风控

实现文件：`spot/execution.py`、`spot/models.py`、`spot/config.py`

- 风险定仓：
  - `risk_amount = equity * risk_per_trade_pct`
  - `qty = risk_amount / (entry - stop)`
  - `usdt_per_trade` 作为 notional 上限
  - 含义：先根据账户净值和单笔可承受亏损比例，算出“这笔交易最多愿意亏多少钱”；再结合入场价与止损价之间的距离，反推出可买数量。
  - 目的：止损越远，仓位会自动变小；止损越近，仓位才允许变大，避免单笔交易把组合风险放大。
  - 约束关系：即使按止损距离推导出的仓位很大，仍会被 `usdt_per_trade` 截断，防止在低波动或超紧止损场景下出现名义仓位异常放大。
- 成本模型：
  - `fee_bps`（双边手续费）
  - `slippage_bps`（BUY 正滑点，SELL 反滑点）
  - 含义：`fee_bps` 模拟交易所手续费，`slippage_bps` 模拟挂不到理想价格、实际成交偏离信号价的执行损耗。
  - 作用：回测统计中的收益、净值、已实现盈亏都会扣掉这两类成本，因此它们直接决定“毛收益是否还能落成净收益”。
  - 方向解释：BUY 使用更高成交价，SELL 使用更低成交价，这样处理是为了让回测对真实执行更保守，而不是把信号价当成总能成交的理想价格。
- 组合风控：
  - `max_total_exposure_pct` 含义：限制组合总持仓市值占净值的比例，避免多个标的一起开仓后把账户暴露堆得过高。
  - `daily_loss_limit_pct` 含义：限制单日可承受亏损；一旦触发，当天停止继续冒险，优先保留本金和次日再战能力。
  - `cooldown_bars` 含义：某标的平仓后，必须等待若干 bar 才允许再次开仓，用来压制“刚止损又立刻重进”的震荡期过度交易。
  - `max_daily_trades` 含义：限制每天允许完成的交易次数，防止策略在噪声行情里高频试错，把 edge 全部磨损在手续费和滑点上。

统计口径保留并扩展：`equity/return/cumpnl` + `fees/slippage/exposure/daily loss`

- 统计项含义：
  - `equity`：账户实时净值 = 现金 + 持仓按最新价格估值后的市值。
  - `return`：相对初始资金或区间起点的收益率，用于判断整体赚钱能力。
  - `cumpnl`：累计盈亏金额，便于直接看到策略到底赚了/亏了多少 USDT。
  - `fees/slippage`：累计手续费与滑点损耗，用于评估成本是否已经吞噬策略 edge。
  - `exposure`：当前组合持仓暴露比例，用于判断仓位是否过满。
  - `daily loss`：当日累计亏损及其是否触发风控，用于监控策略是否进入“当天不该继续打”的状态。

## 4. 回测与 dry-run 运行示例（合并版）

### 4.1 单次扫描（默认 dry-run）

```bash
# --scan: 单次扫描（默认 dry-run）
python -m spot.main --scan
```

### 4.2 实时 dry-run（每 30 秒扫描）

```bash
# --monitor: 持续监控模式
# --auto-execute: 自动执行交易信号（非 --live 时仍为模拟成交）
# --interval 30: 每 30 秒轮询一次（用于检测是否出现新闭合 bar）
# --symbols: 指定要扫描的交易对
python -m spot.main --monitor --auto-execute \
  --interval 30 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT
```

频率说明（已对齐）：

- 回测：每根闭合 bar 决策一次（例如 `15m` 即每 15 分钟一次）
- monitor dry-run：按 `--interval` 轮询，但仅在“出现新闭合 bar”时决策一次
- monitor live：与 dry-run 相同，仅在“出现新闭合 bar”时决策一次
- 因此三种模式的决策频率统一由 `--kline-interval` 决定，`--interval` 只影响检查新 bar 的及时性与 API 轮询频率

### 4.3 三年完整回测（不睡眠，尽快跑完）

```bash
# --backtest: 启用历史回测
# --backtest-start/--backtest-end: 回测时间窗（UTC）
# --kline-interval 15m: 按 15 分钟 bar 决策
# --decision-timing on_close: 每根 bar 收盘时做决策
# --backtest-sleep 0: 不休眠，尽快跑完
# --symbols: 参与回测的交易对
python -m spot.main --backtest \
  --backtest-start 2023-03-04 \
  --backtest-end 2026-03-03 \
  --kline-interval 15m \
  --decision-timing on_close \
  --backtest-sleep 0 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT
```

### 4.4 实盘（需显式开启）

```bash
# --live: 开启真实下单；不加该参数默认 dry-run
python -m spot.main --monitor --auto-execute --live
```

## 5. best_params 导入/导出

- 导出当前运行参数：

```bash
python -m spot.main --scan --export-best-params ./spot/best_params_runtime.json
```

- 导入参数到 backtest / dry-run：

```bash
python -m spot.main --backtest --best-params-file ./spot/best_params_runtime.json
python -m spot.main --monitor --best-params-file ./spot/best_params_runtime.json
```

规则：

- `--optimize-ga` 启用时，`--best-params-file` 会被忽略
- GA 会从随机种群开始搜索（可用 `--seed` 保证可复现）

## 6. GA 参数优化（walk-forward + OOS）

新增文件：`spot/optimizer.py`

- 参数空间：类型/范围/离散集合 + `repair()` 约束修复
- 新增可搜索参数：`band_atr_k`、`ma_breakout_band`、`min_edge_over_cost`、`cost_buffer_k`、`min_atr_pct`、`max_mark_spot_diverge`、`premium_abs_max`、`funding_long_max`、`funding_cost_buffer_k`
- GA 主循环：初始化、评估、选择、交叉、变异、精英保留
- 默认 walk-forward：`train 2y + test 3m` 滚动 OOS
- 多目标 fitness：收益、Sharpe/Sortino、回撤、交易行为、成本占比、稳定性、最差窗口、DSR proxy
- 新增惩罚项：
  - `trades_per_year`（高换手惩罚）
  - `avg_hold_bars`（持仓过短惩罚）
  - `cost_ratio = (fees + slippage) / abs(gross_pnl)`（成本侵蚀惩罚）
- 硬约束（违背直接极差 fitness）：
  - `trades_per_day` 上限
  - `avg_hold_bars` 下限
  - `cost_ratio` 上限
- 研究纪律层：自动切分“训练窗口 + 封存终检窗口（不调参）”

示例命令：

```bash
# --optimize-ga: 启用遗传算法优化
# --walkforward-*: walk-forward 切窗设置（2y 训练 + 3m 测试 + 3m 步长）
# --ga-pop-size/--ga-generations: 控制种群规模与进化代数
# --ga-search-risk: 允许搜索风险参数（仓位、暴露、日内损失阈值等）
# --fitness-weights: 自定义 fitness 权重
# --ga-final-test-days: 封存终检窗口长度（不参与调参）
# --export-best-params: 导出最优参数到 JSON
python -m spot.main --optimize-ga \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --backtest-start 2020-03-03 \
  --backtest-end 2026-03-03 \
  --ga-pop-size 24 \
  --ga-generations 12 \
  --ga-mutation-rate 0.18 \
  --ga-crossover-rate 0.75 \
  --ga-elitism-k 3 \
  --walkforward-train 730 \
  --walkforward-test 90 \
  --walkforward-step 90 \
  --ga-final-test-days 120 \
  --seed 42 \
  --fitness-weights ann_return=1,sharpe=0.8,max_drawdown=1.1,stability=0.8 \
  --ga-search-risk \
  --ga-output-dir ./spot/ga_runs \
  --export-best-params ./spot/best_params_ga.json
```

输出文件：

- `generation_topk.csv`: 每代 top-k 与指标
- `best_params.json`: 最佳候选完整参数（strategy/risk/execution）
- `run_meta.json`: 复现元信息（symbols、时间窗、seed、GA 参数、weights、成本参数）
- `cost_sensitivity_curve.csv`: 成本敏感性曲线（0.5x/1x/2x fee+slippage）
- `worst_window_report.json`: 训练期最差 OOS 窗口详情
- `final_validation_report.json`: 封存终检通过/失败与判定理由

### 6.1 `--optimize-ga` 过程详解（如何找到最优参数）

命令执行后，优化器会按下面流程运行：

1. 参数空间初始化  
   在 `spot/optimizer.py` 的 `ParameterSpace` 中定义可搜索参数（窗口、阈值、可选风险参数、可选成本参数），并对每个候选参数执行 `repair()` 约束修复：  
   - `slow_ma_len >= 2 * fast_ma_len`  
   - `trail_atr_k >= atr_k`  
   - `rsi_buy_min < rsi_buy_max`  
   这样可避免无效参数进入回测。

2. 研究纪律切窗：训练 + 封存终检  
   在用户给定总窗口内，先留出 `--ga-final-test-days` 作为终检窗口（完全不调参），其余部分才用于 walk-forward 训练/OOS 评估。

3. 生成训练期 walk-forward 窗口（样本外为主）  
   使用 `--walkforward-train/--walkforward-test/--walkforward-step` 切分时间区间，例如默认 `730d train + 90d test`。  
   每个候选参数不是只跑一次整段回测，而是要在多个 OOS 窗口上评估并聚合分数，降低过拟合。

4. 初始化种群（Population）  
   根据 `--ga-pop-size` 随机生成第一代候选参数。  
   `--seed` 固定时，初始种群与进化过程可复现。

5. 候选评估（核心耗时阶段）  
   每个候选参数都会在所有 walk-forward OOS 窗口上运行完整回测。  
   回测复用同一套实盘/回测决策引擎（`SpotDecisionEngine`），不会出现“回测逻辑与实盘逻辑分叉”。

6. 计算 fitness（多目标加权 + 硬约束）  
   设 walk-forward 的 OOS 窗口集合为 `W`，窗口数为 `N = |W|`。每个窗口 `i` 先计算以下指标（实现见 `spot/optimizer.py::_run_window_backtest`）：  
   - `annual_return_i = ((1 + total_return_i/100)^(365/test_days_i) - 1) * 100`（若 `total_return_i <= -100`，则记为 `-100`）  
   - `win_rate_i = wins_i / sells_i * 100`（无 SELL 则 0）  
   - `profit_factor_i = gross_profit_i / gross_loss_i`（若 `gross_loss_i == 0`：有盈利记 `3.0`，否则 `0.0`）  
   - `avg_hold_bars_i`：按每次 `BUY->SELL` 的持有 bar 数均值  
   - `cost_ratio_i = (fees_i + slippage_i) / max(abs(gross_pnl_i), 1e-9)`，其中 `gross_pnl_i = realized_pnl_i + fees_i + slippage_i`  
   - `trades_per_year_i = sells_i / test_days_i * 365`，`trades_per_day_i = sells_i / test_days_i`  
   然后做跨窗口聚合（实现见 `spot/optimizer.py::_fitness_from_windows`）：  
   - `avg_x = mean(x_i)`（对收益、Sharpe、Sortino、回撤、胜率、PF、持仓、成本、交易频率等）  
   - `stability_std = pstdev(oos_return_i)`  
   - `worst_window = min(oos_return_i)`  
   - `dsr_proxy = avg_sharpe - 0.5 * pstdev(sharpe_i)`  
   软惩罚项定义：  
   - `trade_year_penalty = max(0, (avg_trades_per_year - target_trades_per_year) / max(1, target_trades_per_year))`  
   - `hold_penalty = max(0, (target_avg_hold_bars - avg_hold_bars) / max(1, target_avg_hold_bars))`  
   - `cost_penalty = max(0, avg_cost_ratio - 0.35)`  
   - `stability_penalty = max(0, stability_std / 10)`  
   - `worst_penalty = abs(min(0, worst_window))`  
   - `dsr_penalty = max(0, -dsr_proxy)`  
   正向项（`positive`）：  
   - `w_ann*avg_ann_return + w_sharpe*avg_sharpe*10 + w_sortino*avg_sortino*10 + w_win*avg_win_rate + w_pf*min(avg_pf, 5)*20`  
   负向项（`negative`）：  
   - `w_mdd*avg_max_drawdown + w_trade*trade_year_penalty*120 + w_hold*hold_penalty*120 + w_cost*cost_penalty*120 + w_stability*stability_penalty*100 + w_worst*worst_penalty + w_dsr*dsr_penalty*100`  
   最终：  
   - `fitness = positive - negative`  
   - 权重来自 `--fitness-weights`（默认：`ann_return=1.0, sharpe=0.8, sortino=0.4, max_drawdown=1.0, win_rate=0.2, profit_factor=0.2, trade_count=0.15, holding=0.15, cost_ratio=0.6, stability=0.7, worst_window=0.8, dsr_proxy=0.3`）  
   硬约束（任何一条违反都直接判极差 fitness）：  
   - `max(trades_per_day_i) <= max_trades_per_day`（默认 6.0）  
   - `min(avg_hold_bars_i) >= min_avg_hold_bars`（默认 6.0）  
   - `max(cost_ratio_i) <= max_cost_ratio`（默认 1.1）  
   若存在硬约束违背：  
   - `fitness = -1e8 - 1e5 * 违背条数`（并记录 `hard_constraint_failures`）

7. 进化迭代（Generations）  
   每一代按以下步骤更新种群：  
   - 选择：锦标赛选择（更高 fitness 更容易被选中）  
   - 交叉：按 `--ga-crossover-rate` 交换父代参数  
   - 变异：按 `--ga-mutation-rate` 随机扰动参数  
   - 精英保留：`--ga-elitism-k` 直接保留当代最优个体  
   重复到 `--ga-generations` 结束。

8. 封存终检与研究报告  
   GA 选出最佳参数后，会在封存终检窗口单独跑一次，不参与任何调参；并自动生成：  
   - 成本敏感性曲线（0.5x/1x/2x）  
   - 最差窗口报告  
   - 终检通过/失败报告（含判定理由）

9. 记录与导出  
   每一代会把 top-k 候选写入 `generation_topk.csv`（包含参数 JSON 和关键指标）。  
   最终最佳候选会写入 `best_params.json`，并记录 `run_meta.json` 以支持复现。

10. 参数回灌到回测 / dry-run  
   GA 结束后，可直接把 `best_params.json` 导入回测或 dry-run：  
   `python -m spot.main --backtest --best-params-file ./spot/best_params_ga.json`  
   `python -m spot.main --monitor --best-params-file ./spot/best_params_ga.json`  
   注意：当启用 `--optimize-ga` 时，`--best-params-file` 会被忽略。

### 6.2 调参建议（实战）

- 先固定 timeframe：默认不要打开 `--ga-search-timeframe`，先优化阈值和风险参数。  
- 先小规模试跑：如 `--ga-pop-size 12 --ga-generations 6` 快速验证搜索方向。  
- 再扩搜索：确认方向后再提升到 `24x12` 或更高。  
- 保持成本真实：`fee_bps/slippage_bps` 建议按真实成交环境设置。  
- 重点看 OOS 稳定性：不仅看单一最高收益，更看最差窗口和波动性。

说明：GA 模块只搜索和评估“参数”，不会改写策略逻辑本身；因此第 2.1 入场 BUY 与第 2.2 出场 SELL 的触发条件描述仍然成立。

## 7. 测试

- `tests/test_spot_strategy_execution.py`
- `tests/test_spot_backtest_mode.py`
- `tests/test_spot_ga_optimizer.py`

运行：

```bash
pytest tests/test_spot_strategy_execution.py tests/test_spot_backtest_mode.py tests/test_spot_ga_optimizer.py -q
```
