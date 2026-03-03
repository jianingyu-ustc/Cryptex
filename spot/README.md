# 现货自动交易子系统 (Spot Auto Trading)

基于 Binance 现货行情的自动交易子系统，默认运行在 `dry-run` 模式。  
系统目标是用统一的趋势策略和明确风控规则，在可控风险下进行自动化交易。

## 1. 策略说明

策略实现位于 `spot/strategy.py`，核心是趋势跟随 + 风险退出。

### 1.1 入场条件（BUY）

对每个交易对，按 `--kline-interval` 获取 K 线后计算：

- `fast_ma = SMA(9)`（可配置）
- `slow_ma = SMA(21)`（可配置）
- `RSI(14)`（可配置）
- 近 2 根 K 线动量 `momentum_pct`
- 24h 成交额过滤

满足以下条件才发出 `BUY`：

- `fast_ma > slow_ma`
- `momentum_pct > 0`
- `50 <= RSI <= rsi_buy_max`（默认上限 68）
- `24h quote volume >= min_24h_quote_volume`（默认 20,000,000）

### 1.2 出场条件（SELL）

若已有持仓，满足任一条件触发 `SELL`：

- 止损：`pnl_pct <= -stop_loss_pct`（默认 `-2%`）
- 止盈：`pnl_pct >= take_profit_pct`（默认 `+4%`）
- 趋势转弱：`fast_ma < slow_ma` 且 `RSI <= rsi_sell_min`（默认 45）

### 1.3 未触发条件

不满足开/平仓条件时返回 `HOLD`，继续等待下一轮信号。

## 2. 模块结构

```text
spot/
├── main.py        # 子系统入口与 CLI 参数解析、终端展示
├── config.py      # 策略参数/风控参数/运行参数
├── models.py      # Signal/Position/Trade 数据结构
├── strategy.py    # 信号决策（BUY/SELL/HOLD）
├── execution.py   # 下单执行、持仓状态、收益统计
└── README.md      # 本文档
```

共享 API 在 `common/`：

- `common/binance_client.py`: Binance REST/WebSocket 通用客户端
- Spot 子系统、套利子系统都复用同一套 Binance API 封装

## 3. 运行流程

1. `main.py spot` 启动后初始化 Binance 客户端
2. `strategy.py` 生成每个标的的 `BUY/SELL/HOLD` 信号
3. `execution.py` 在 `--auto-execute` 下执行成交并维护持仓
4. 每轮刷新展示信号、持仓、交易与收益统计

## 4. 资金与收益计算

新增了“初始资金 + 每笔交易后收益追踪”：

- 初始资金：`--initial-capital`（默认 `10000` USDT）
- 现金余额：`cash_balance`
- 持仓市值：`sum(position.quantity * last_price)`
- 账户净值：`equity = cash_balance + 持仓市值`
- 累计收益：`equity - initial_capital`
- 累计收益率：`(equity - initial_capital) / initial_capital * 100%`

每次成交后会记录并展示：

- `Equity`
- `Return`
- `CumPnL`

## 5. 使用方法

### 5.1 环境准备

```bash
cd Cryptex
pip install -r requirements.txt
cp .env.example .env
```

`.env` 至少需要 Binance Key（实盘必须，dry-run 仅连行情也建议配置）：

```bash
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
SPOT_DRY_RUN=true
```

### 5.2 常用命令

```bash
# 单次扫描（默认 dry-run）
python main.py spot --scan

# 单次扫描 + 自动模拟执行
python main.py spot --scan --auto-execute

# 持续监控 + 自动模拟执行（每 30 秒）
python main.py spot --monitor --auto-execute --interval 30

# 指定初始资金并追踪累计收益
python main.py spot --monitor --auto-execute --interval 30 --initial-capital 10000

# 调整交易范围与单笔资金
python main.py spot --monitor --auto-execute \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --usdt-per-trade 200 \
  --max-positions 4

# 实盘模式（谨慎）
python main.py spot --monitor --auto-execute --live
```

也可直接运行模块入口：

```bash
python -m spot.main --help
```

### 5.3 关键参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--symbols` | `BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT` | 监控交易对 |
| `--monitor` | `False` | 持续运行模式 |
| `--auto-execute` | `False` | 自动下单执行信号 |
| `--live` | `False` | 实盘交易开关（默认 dry-run） |
| `--interval` | `30` | 刷新间隔（秒） |
| `--initial-capital` | `10000` | 初始资金（USDT） |
| `--usdt-per-trade` | `100` | 单笔分配资金 |
| `--max-positions` | `3` | 最大持仓数量 |
| `--kline-interval` | `15m` | 策略 K 线周期 |
| `--stop-loss` | `2.0` | 止损百分比 |
| `--take-profit` | `4.0` | 止盈百分比 |

## 6. 注意事项

- 默认推荐先 `dry-run` 连续观察信号和收益曲线，再考虑实盘。
- 若出现 `401` 或权限错误，需要检查 Binance API Key 权限、IP 白名单和账户可交易范围。
- “无交易”通常不是故障，而是当前窗口内未满足入场条件（趋势、RSI、动量、成交额过滤）。
