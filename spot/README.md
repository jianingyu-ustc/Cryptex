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
  - `decision_timing`（`on_close` / `intrabar`）
- `StrategyParams`（定义于 `spot/config.py`）支持结构约束修复：
  - `slow_ma_len >= 2 * fast_ma_len`
  - `trail_atr_k >= atr_k`
  - `rsi_buy_min < rsi_buy_max`

所有 BUY/HOLD/SELL 都输出 `reasons` 原因链，回测与实时 dry-run 共用同一套逻辑。

## 2. 策略逻辑（趋势回撤入场 + ATR 风控）

实现文件：`spot/strategy.py`

### 2.1 入场 BUY

需同时满足：

- 趋势过滤：`fast_ma > slow_ma`
- 回撤入场：最近价格回踩 `fast_ma` 附近（`pullback_tol`）
- 确认：重新站上 `fast_ma` 或小突破（`confirm_breakout`）
- RSI 区间：`rsi_buy_min <= RSI <= rsi_buy_max`
- 市场状态过滤：优先 `ADX(14) >= adx_min`，否则使用趋势强度 proxy
- 流动性过滤：`24h quote volume >= min_24h_quote_volume`

### 2.2 出场 SELL

任一触发：

- ATR 初始止损：`price <= stop_price`，`stop_price = entry - atr_k * ATR`
- ATR 追踪止盈：`price <= max_price - trail_atr_k * ATR`
- 趋势转弱：`fast_ma < slow_ma and RSI <= rsi_sell_min`

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

补充：GA 优化只会搜索上述指标相关参数，不会改变指标定义和 BUY/SELL 规则结构。

## 3. 执行、成本与风控

实现文件：`spot/execution.py`、`spot/models.py`、`spot/config.py`

- 风险定仓：
  - `risk_amount = equity * risk_per_trade_pct`
  - `qty = risk_amount / (entry - stop)`
  - `usdt_per_trade` 作为 notional 上限
- 成本模型：
  - `fee_bps`（双边手续费）
  - `slippage_bps`（BUY 正滑点，SELL 反滑点）
- 组合风控：
  - `max_total_exposure_pct`
  - `daily_loss_limit_pct`
  - `cooldown_bars`
  - `max_daily_trades`

统计口径保留并扩展：`equity/return/cumpnl` + `fees/slippage/exposure/daily loss`

## 4. 回测与 dry-run 运行示例（合并版）

### 4.1 单次扫描（默认 dry-run）

```bash
python -m spot.main --scan
```

### 4.2 实时 dry-run（每 30 秒扫描）

```bash
python -m spot.main --monitor --auto-execute \
  --interval 30 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT
```

### 4.3 三年完整回测（不睡眠，尽快跑完）

```bash
python -m spot.main --backtest \
  --backtest-start 2023-03-04 \
  --backtest-end 2026-03-03 \
  --kline-interval 15m \
  --decision-timing on_close \
  --backtest-sleep 0 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT
```

参数说明：

- `--backtest`: 启用历史回测模式
- `--backtest-start / --backtest-end`: UTC 时间窗（ISO 格式）
- `--kline-interval 15m`: 指标与策略按 15m bar 决策
- `--decision-timing on_close`: 使用收盘决策（与回测一致）
- `--backtest-sleep 0`: 不等待，按最快速度回放
- `--symbols`: 参与回测的交易对

### 4.4 实盘（需显式开启）

```bash
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
- GA 主循环：初始化、评估、选择、交叉、变异、精英保留
- 默认 walk-forward：`train 2y + test 3m` 滚动 OOS
- 多目标 fitness：收益、Sharpe/Sortino、回撤、交易行为、成本占比、稳定性、最差窗口、DSR proxy

示例命令：

```bash
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

### 6.1 `--optimize-ga` 过程详解（如何找到最优参数）

命令执行后，优化器会按下面流程运行：

1. 参数空间初始化  
   在 `spot/optimizer.py` 的 `ParameterSpace` 中定义可搜索参数（窗口、阈值、可选风险参数、可选成本参数），并对每个候选参数执行 `repair()` 约束修复：  
   - `slow_ma_len >= 2 * fast_ma_len`  
   - `trail_atr_k >= atr_k`  
   - `rsi_buy_min < rsi_buy_max`  
   这样可避免无效参数进入回测。

2. 生成 walk-forward 窗口（样本外为主）  
   使用 `--walkforward-train/--walkforward-test/--walkforward-step` 切分时间区间，例如默认 `730d train + 90d test`。  
   每个候选参数不是只跑一次整段回测，而是要在多个 OOS 窗口上评估并聚合分数，降低过拟合。

3. 初始化种群（Population）  
   根据 `--ga-pop-size` 随机生成第一代候选参数。  
   `--seed` 固定时，初始种群与进化过程可复现。

4. 候选评估（核心耗时阶段）  
   每个候选参数都会在所有 walk-forward OOS 窗口上运行完整回测。  
   回测复用同一套实盘/回测决策引擎（`SpotDecisionEngine`），不会出现“回测逻辑与实盘逻辑分叉”。

5. 计算 fitness（多目标加权）  
   每个窗口会产出收益与风险指标（如年化收益、Sharpe、Sortino、最大回撤、胜率、PF、交易次数、持仓长度、成本占比）。  
   然后跨窗口聚合，并加入稳定性惩罚（窗口方差、最差窗口）与 DSR proxy。  
   最终 fitness = 正向收益项 - 各类惩罚项，权重由 `--fitness-weights` 控制。

6. 进化迭代（Generations）  
   每一代按以下步骤更新种群：  
   - 选择：锦标赛选择（更高 fitness 更容易被选中）  
   - 交叉：按 `--ga-crossover-rate` 交换父代参数  
   - 变异：按 `--ga-mutation-rate` 随机扰动参数  
   - 精英保留：`--ga-elitism-k` 直接保留当代最优个体  
   重复到 `--ga-generations` 结束。

7. 记录与导出  
   每一代会把 top-k 候选写入 `generation_topk.csv`（包含参数 JSON 和关键指标）。  
   最终最佳候选会写入 `best_params.json`，并记录 `run_meta.json` 以支持复现。

8. 参数回灌到回测 / dry-run  
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

## 7. CLI 参数总览（新增重点）

- 策略参数：
  - `--kline-interval`: 策略使用的 K 线周期（如 `15m/30m/1h/4h/1d`）。
  - `--decision-timing`: 决策时点（`on_close` 收盘决策；`intrabar` 盘中决策）。
  - `--fast-ma-len`: 快速均线窗口长度。
  - `--slow-ma-len`: 慢速均线窗口长度（受约束：`slow >= 2*fast`）。
  - `--rsi-len`: RSI 指标窗口长度。
  - `--atr-len`: ATR 指标窗口长度（用于止损/追踪止盈）。
  - `--adx-len`: ADX 指标窗口长度（用于市场状态过滤）。
  - `--pullback-tol`: 回撤到快均线附近的容忍阈值。
  - `--confirm-breakout`: 入场确认的小突破阈值。
  - `--rsi-buy-min`: BUY 的 RSI 下限。
  - `--rsi-buy-max`: BUY 的 RSI 上限。
  - `--rsi-sell-min`: 趋势转弱 SELL 的 RSI 阈值。
  - `--atr-k`: 初始 ATR 止损倍数。
  - `--trail-atr-k`: ATR 追踪止盈倍数（受约束：`trail_atr_k >= atr_k`）。
  - `--adx-min`: ADX 最小阈值（优先用于市场状态过滤）。
  - `--trend-strength-min`: 当 ADX 不可用时的趋势强度替代阈值。
  - `--min-24h-quote-volume`: 最低 24h 成交额过滤阈值。
- 风控参数：
  - `--risk-per-trade-pct`: 单笔风险占净值百分比（风险定仓核心参数）。
  - `--usdt-per-trade`: 单笔名义金额上限（与风险定仓共同约束仓位）。
  - `--max-total-exposure-pct`: 组合总暴露上限（持仓市值/净值）。
  - `--daily-loss-limit-pct`: 当日回撤阈值，超过后停止开新仓。
  - `--cooldown-bars`: 卖出后禁止再次买入的冷却 bar 数。
  - `--max-daily-trades`: 每日最多允许成交次数。
  - `--max-positions`: 最大同时持仓标的数量。
- 成本参数：
  - `--fee-bps`: 每边手续费（bps）。
  - `--slippage-bps`: 模拟滑点（bps，BUY 正滑点，SELL 反滑点）。
- 回测与运行控制：
  - `--backtest`: 启用回测模式。
  - `--backtest-years`: 回测年数（最少 3 年）。
  - `--backtest-start`: 回测开始时间（UTC，ISO）。
  - `--backtest-end`: 回测结束时间（UTC，ISO）。
  - `--backtest-sleep`: 每根 bar 的暂停秒数（`0` 表示不等待）。
  - `--symbols`: 交易对列表（逗号分隔）。
  - `--monitor`: 持续轮询模式。
  - `--scan`: 单次扫描模式。
  - `--auto-execute`: 自动执行 BUY/SELL 信号。
  - `--live`: 启用实盘（默认 dry-run）。
  - `--interval`: 监控模式刷新间隔（秒）。
- GA 参数：
  - `--optimize-ga`: 启用遗传算法优化模式。
  - `--ga-output-dir`: GA 输出目录（存放 `best_params/run_meta/csv`）。
  - `--ga-pop-size`: 每代种群数量。
  - `--ga-generations`: 进化代数。
  - `--ga-mutation-rate`: 变异概率。
  - `--ga-crossover-rate`: 交叉概率。
  - `--ga-elitism-k`: 每代保留的精英个体数量。
  - `--ga-top-k-log`: 每代写入 CSV 的 top-k 数量。
  - `--walkforward-train`: walk-forward 训练窗口天数。
  - `--walkforward-test`: walk-forward 测试窗口天数。
  - `--walkforward-step`: walk-forward 滚动步长天数（`0` 时默认等于 test）。
  - `--fitness-weights`: fitness 各指标权重（`k=v` 逗号分隔）。
  - `--seed`: 随机种子（用于复现 GA 结果）。
  - `--ga-search-timeframe`: 允许 GA 搜索 bar 周期。
  - `--ga-search-risk`: 允许 GA 搜索风险参数。
  - `--ga-search-cost`: 允许 GA 搜索成本参数。
  - `--ga-max-search-dims`: 限制 GA 搜索维度数量，降低过拟合风险。
- 参数文件：
  - `--best-params-file`: 加载参数文件（用于 backtest/dry-run）。
  - `--export-best-params`: 导出当前参数或 GA 最优参数到文件。

说明：GA 模块只搜索和评估“参数”，不会改写策略逻辑本身；因此第 2.1 入场 BUY 与第 2.2 出场 SELL 的触发条件描述仍然成立。

## 8. 测试

- `tests/test_spot_strategy_execution.py`
- `tests/test_spot_backtest_mode.py`
- `tests/test_spot_ga_optimizer.py`

运行：

```bash
pytest tests/test_spot_strategy_execution.py tests/test_spot_backtest_mode.py tests/test_spot_ga_optimizer.py -q
```
