# Cryptex - 加密货币量化交易系统

一个专业级的加密货币交易系统，包含两个核心子系统：
1. **套利子系统** (Arbitrage) - 基于 Binance 的多策略套利交易
2. **现货自动交易子系统** (Spot) - 基于 Binance 现货的自动交易

## 项目结构

```
Cryptex/
├── common/                        # 共用模块
│   ├── __init__.py
│   ├── price_client.py           # 多源价格数据客户端 (Binance/OKX/Kraken等)
│   └── binance_client.py         # 子系统共用 Binance API 客户端
│
├── arbitrage/                     # 套利子系统
│   ├── __init__.py
│   ├── api.py                    # Binance API 兼容层（实际实现在 common）
│   ├── config.py                 # 套利系统配置
│   ├── strategy.py               # 策略层 (3种策略)
│   ├── execution.py              # 执行层 (原子化对冲)
│   ├── risk.py                   # 风控层
│   ├── main.py                   # 套利系统入口
│   └── README.md                 # 套利系统文档
│
├── spot/                          # 现货自动交易子系统
│   ├── __init__.py
│   ├── config.py                 # 现货交易配置
│   ├── models.py                 # 共享数据模型
│   ├── strategy.py               # 现货交易信号策略
│   ├── execution.py              # 下单执行与持仓管理
│   ├── optimizer.py              # GA 参数优化 (walk-forward OOS)
│   ├── main.py                   # 现货系统入口
│   └── README.md                 # 现货系统文档
│
├── tests/                         # 测试文件
│   ├── __init__.py
│   ├── test_binance_arb.py
│   ├── test_binance_client_rate_limit.py
│   ├── test_deribit_dvol_api.py
│   ├── test_spot_strategy_execution.py
│   ├── test_spot_backtest_mode.py
│   └── test_spot_ga_optimizer.py
│
├── deploy.sh                      # 部署脚本
├── requirements.txt               # 依赖包
├── .env.example                   # 环境变量示例
└── README.md                      # 本文档
```

## 系统架构

### 套利子系统 (Arbitrage Subsystem)

统一套利交易系统，支持三种策略：

| 策略 | 原理 | 条件 |
|------|-----|------|
| **资金费率套利** | 做多现货+做空永续，收取资金费 | 费率 > 0.03% |
| **期现套利** | 做多现货+做空季度合约，锁定基差 | 年化 > 15% |
| **稳定币套利** | 利用稳定币之间的价差 | 价差 > 0.5% |

### 现货自动交易子系统 (Spot Auto Trading)

基于 Binance 现货 K 线和行情的自动交易系统，默认 dry-run：

- 统一决策引擎：`SpotDecisionEngine.decide(context, params)`，回测与实时 dry-run 共用一套逻辑
- 入场策略：趋势过滤 + 回撤确认 + RSI 区间 + ADX/趋势强度 + 24h成交额过滤
- 风控与出场：ATR 初始止损 + ATR 追踪止盈 + 趋势转弱平仓
- 风险定仓：`risk_per_trade_pct` + `usdt_per_trade` 上限
- 模拟成本：`fee_bps` + `slippage_bps`，已纳入 equity/return/cumpnl 统计
- 组合风控：`max_total_exposure_pct` / `daily_loss_limit_pct` / `cooldown_bars`
- 参数优化：支持 GA + walk-forward OOS，并导出 `best_params.json`

详细策略、模块结构和参数请见：
- `spot/README.md`

## 快速开始

### 1. 安装依赖

```bash
cd Cryptex
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件，填入必要的 API Key
```

### 3. 运行套利系统

运行指令已迁移到：
- `arbitrage/README.md`

### 4. 运行现货自动交易系统

运行指令已迁移到：
- `spot/README.md`

## 套利收益公式

### 1️⃣ 资金费率套利

```
净收益 = Position × [资金费率 - 0.10%]
```

### 2️⃣ 期现套利

```
年化收益 = [(期货价 - 现货价) / 现货价] × (365 / 到期天数) × 100%
净年化 = 年化收益 - 年化交易成本
```

### 3️⃣ 稳定币套利

```
净收益 = 价差 - 0.10%
```

### 交易成本

| 费用类型 | 费率 |
|---------|-----|
| Taker Fee | 0.04% |
| Slippage | 0.01% |
| **单向成本** | **0.05%** |
| **双向成本** | **0.10%** |

## 环境变量

```bash
# Binance API (套利与现货自动交易必需)
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_api_secret

# OKX API (备用价格源)
OKX_API_KEY=your_okx_api_key
OKX_API_SECRET=your_okx_api_secret
OKX_PASSPHRASE=your_okx_passphrase

# 系统配置
SPOT_DRY_RUN=true
```

## 风控参数

| 参数 | 默认值 | 说明 |
|------|-------|-----|
| 最大仓位比例 | 50% | 单仓位不超过账户余额的 50% |
| 单策略敞口 | 25% | 单个策略不超过 25% |
| 止损阈值 | 3% | 亏损 3% 自动平仓 |
| 最低保证金率 | 5% | 低于 5% 触发警告 |

## 常见问题

### Q: API 返回错误怎么办？
A: 检查 API Key 是否正确配置，确保有足够的 API 权限。

### Q: 资金费率数据为空？
A: 可能是网络问题或 API 限制，稍后重试。

### Q: 套利信号但无法执行？
A: 检查账户余额是否充足，以及是否开通了合约交易。

## 风险提示

⚠️ **重要声明**：
- 本系统仅供学习和研究使用
- 加密货币交易存在高风险
- 套利也存在执行风险和市场风险
- 请勿使用无法承受损失的资金
- 使用前请充分理解每种策略的原理

## License

MIT License
