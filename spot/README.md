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

- 策略：
  - `--kline-interval`, `--decision-timing`
  - `--fast-ma-len`, `--slow-ma-len`, `--rsi-len`, `--atr-len`, `--adx-len`
  - `--pullback-tol`, `--confirm-breakout`
  - `--rsi-buy-min`, `--rsi-buy-max`, `--rsi-sell-min`
  - `--adx-min`, `--trend-strength-min`, `--min-24h-quote-volume`
- 风控：
  - `--risk-per-trade-pct`, `--usdt-per-trade`
  - `--max-total-exposure-pct`, `--daily-loss-limit-pct`
  - `--cooldown-bars`, `--max-daily-trades`, `--max-positions`
- 成本：`--fee-bps`, `--slippage-bps`
- GA：
  - `--optimize-ga`
  - `--ga-pop-size`, `--ga-generations`, `--ga-mutation-rate`, `--ga-crossover-rate`, `--ga-elitism-k`, `--ga-top-k-log`
  - `--walkforward-train`, `--walkforward-test`, `--walkforward-step`
  - `--fitness-weights`, `--seed`
  - `--ga-search-timeframe`, `--ga-search-risk`, `--ga-search-cost`, `--ga-max-search-dims`
- 参数文件：`--best-params-file`, `--export-best-params`

## 8. 测试

- `tests/test_spot_strategy_execution.py`
- `tests/test_spot_backtest_mode.py`
- `tests/test_spot_ga_optimizer.py`

运行：

```bash
pytest tests/test_spot_strategy_execution.py tests/test_spot_backtest_mode.py tests/test_spot_ga_optimizer.py -q
```
