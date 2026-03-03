# 现货自动交易子系统 (Spot Auto Trading)

基于 Binance 现货行情的自动交易子系统，默认 `dry-run`。  
本次升级保持工程结构不变（`main.py/config.py/models.py/strategy.py/execution.py`），但模拟执行更贴近真实交易。

## 1. 新策略（趋势回撤入场 + ATR 风控）

实现文件：`spot/strategy.py`

### 1.1 BUY 逻辑

开仓必须同时满足：

- 趋势过滤：`fast_ma > slow_ma`
- 回撤入场：价格先回踩 `fast_ma` 附近（`pullback_tol`）
- 确认：重新站上 `fast_ma` 或出现小突破确认
- RSI 区间：`rsi_buy_min <= RSI <= rsi_buy_max`（默认 45~65）
- 市场状态过滤（优先 ADX）：
  - `ADX(14) >= adx_min`，若 ADX 不可用则回退到  
  - `abs(fast_ma-slow_ma)/close >= trend_strength_min`
- 24h 成交额过滤：`min_24h_quote_volume`

### 1.2 SELL 逻辑

持仓中任一条件触发平仓：

- ATR 初始止损：`price <= stop_price`，其中  
  `stop_price = entry_price - atr_k * ATR(14)`（开仓时确定）
- ATR 追踪止盈：`price <= max_price - trail_atr_k * ATR(14)`
- 趋势转弱：`fast_ma < slow_ma and RSI <= rsi_sell_min`

### 1.3 决策解释

`SpotSignal` 新增 `reasons: list[str]`，终端会展示 BUY/HOLD/SELL 的原因链，便于调参与排查。

## 2. 执行与风控升级

实现文件：`spot/execution.py`, `spot/models.py`, `spot/config.py`

### 2.1 仓位模型

- `SpotPosition` 扩展字段：`entry_price/quantity/entry_time/stop_price/max_price/fees_paid/realized_pnl/unrealized_pnl`
- `SpotTrade` 扩展字段：`reasons/fee_paid/slippage_bps/slippage_cost_usdt/expected_price`

### 2.2 风险定仓

- `risk_amount = equity * risk_per_trade_pct`
- `qty = risk_amount / (entry_price - stop_price)`
- `usdt_per_trade` 仍保留，作为每笔 notional 上限：`min(qty*price, usdt_per_trade)`

### 2.3 真实模拟成本

- `fee_bps`：每边手续费（默认 10 bps）
- `slippage_bps`：滑点（默认 10 bps，可调 5~20）
- dry-run 成交价：
  - BUY：`price * (1 + slippage)`
  - SELL：`price * (1 - slippage)`
- 手续费会从现金扣除，并计入统计（PnL、Equity、Return、CumPnL）

### 2.4 组合风控

- `max_total_exposure_pct`：持仓总市值占净值上限
- `daily_loss_limit_pct`：当日净值跌幅超过阈值后，停止开新仓（允许平仓）
- `cooldown_bars`：某标的 SELL 后，N 根 bar 内禁止重新 BUY

## 3. CLI 新增参数

实现文件：`spot/main.py`（并同步 `main.py spot` 转发）

- `--backtest`, `--backtest-years`, `--backtest-start`, `--backtest-end`
- `--backtest-sleep`（每根回测K线暂停秒数，`0` 表示尽快回放）
- `--rsi-buy-min`, `--rsi-buy-max`
- `--atr-k`, `--trail-atr-k`
- `--adx-min`, `--trend-strength-min`
- `--risk-per-trade-pct`
- `--fee-bps`, `--slippage-bps`
- `--max-total-exposure-pct`, `--daily-loss-limit-pct`, `--cooldown-bars`

## 4. 关键改动文件与函数

- `spot/config.py`
  - `SpotTradingConfig` 新增策略/风控/成本参数
  - `min_klines_required` 覆盖 ATR/ADX 窗口
- `spot/models.py`
  - `SpotSignal` 增加 `reasons/atr/adx/trend_strength/stop_price`
  - `SpotPosition` 增加 ATR/trailing 相关字段与 PnL 字段
  - `SpotTrade` 增加费用与滑点字段
- `spot/strategy.py`
  - 新增 `_atr/_adx/_market_state_ok`
  - `analyze_symbol` 升级为“趋势 + 回撤确认 + ATR风控 + reasons”
- `spot/execution.py`
  - 新增 `_risk_based_qty_and_stop`
  - `execute_signal` 支持风险定仓、手续费、滑点、组合风控、cooldown
  - `get_stats` 增加 `fees/slippage/exposure/daily_loss` 统计
- `spot/main.py`
  - 信号表新增 ATR/ADX、Reasons
  - 交易展示新增 Fee
  - Stats 增加费用/滑点/暴露/日内风控状态

## 5. 运行示例

```bash
# 默认 dry-run，单次扫描
python main.py spot --scan

# 历史回测（3年完整窗口，无睡眠，尽快回放）
python main.py spot --backtest \
  --backtest-start 2023-03-04 \
  --backtest-end 2026-03-03 \
  --kline-interval 15m \
  --backtest-sleep 0 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT

# 历史回测（按 years 自动取窗，并可调慢放节奏）
python main.py spot --backtest --backtest-years 3 \
  --kline-interval 15m \
  --backtest-sleep 30 \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT

# dry-run 自动执行 + 新参数
python main.py spot --monitor --auto-execute \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --interval 30 \
  --rsi-buy-min 45 --rsi-buy-max 65 \
  --atr-k 2.0 --trail-atr-k 2.5 \
  --adx-min 18 \
  --risk-per-trade-pct 0.5 \
  --fee-bps 10 --slippage-bps 10 \
  --max-total-exposure-pct 80 \
  --daily-loss-limit-pct 3 \
  --cooldown-bars 2

# 实盘（仍需显式开启）
python main.py spot --monitor --auto-execute --live
```

完整回测命令参数说明：

- `--backtest`: 启用历史回测模式（不走实时监控）。
- `--backtest-start`: 回测开始时间（UTC，ISO格式）。
- `--backtest-end`: 回测结束时间（UTC，ISO格式）。
- `--kline-interval 15m`: 使用 15 分钟 K 线；策略按每根 15m bar 决策。
- `--backtest-sleep 0`: 每根 bar 不暂停，按最快速度执行回测。
- `--symbols BTCUSDT,ETHUSDT,SOLUSDT`: 指定参与回测的交易对列表。

## 6. 测试

新增：

- `tests/test_spot_strategy_execution.py`
- `tests/test_spot_backtest_mode.py`

覆盖场景：

- 趋势成立但未回撤 -> HOLD
- 趋势成立 + 回撤 + 确认 -> BUY
- ATR 止损触发 -> SELL
- 追踪止盈触发 -> SELL
- fee/slippage 对 equity 影响
- cooldown 生效：卖出后 N bars 内不再 BUY

运行：

```bash
pytest tests/test_spot_strategy_execution.py tests/test_spot_backtest_mode.py -q
```
