# Cryptex - 加密货币量化交易系统

一个专业级的加密货币交易系统，包含三个核心子系统：
1. **预测子系统** (Prediction) - 基于 Polymarket 预测市场的价格预测
2. **套利子系统** (Arbitrage) - 基于 Binance 的多策略套利交易
3. **现货自动交易子系统** (Spot) - 基于 Binance 现货的自动交易

## 项目结构

```
Cryptex/
├── common/                        # 共用模块
│   ├── __init__.py
│   ├── price_client.py           # 多源价格数据客户端 (Binance/OKX/Kraken等)
│   └── binance_client.py         # 子系统共用 Binance API 客户端
│
├── prediction/                    # 预测子系统
│   ├── __init__.py
│   ├── api_client.py             # Polymarket API 客户端
│   ├── config.py                 # 预测系统配置
│   ├── predictor.py              # 预测引擎 (多因子策略)
│   ├── display.py                # Rich 终端显示
│   ├── backtest.py               # 回测引擎
│   ├── demo_data.py              # 演示数据生成
│   ├── main.py                   # 预测系统入口
│   ├── polymarket_clob_client.py # CLOB 交易客户端
│   └── wallet_status.py          # 钱包状态检查
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
│   ├── main.py                   # 现货系统入口
│   └── README.md                 # 现货系统文档
│
├── scripts/                       # 工具脚本
│   ├── __init__.py
│   ├── check_balance.py          # 余额检查
│   ├── check_matic.py            # MATIC 检查
│   ├── check_matic_web3.py       # Web3 MATIC 检查
│   ├── refresh_balance.py        # 刷新余额
│   └── find_active.py            # 查找活跃市场
│
├── tests/                         # 测试文件
│   ├── __init__.py
│   ├── test_api.py
│   ├── test_sdk_balance.py
│   ├── test_server.py
│   ├── debug_api.py
│   └── debug_backtest.py
│
├── main.py                        # 统一命令行入口
├── migrate.sh                     # 迁移脚本
├── deploy.sh                      # 部署脚本
├── requirements.txt               # 依赖包
├── .env.example                   # 环境变量示例
└── README.md                      # 本文档
```

## 系统架构

### 预测子系统 (Prediction Subsystem)

基于 Polymarket 预测市场数据的加密货币价格预测系统。

**功能特性**:
- 实时获取 Polymarket 加密货币预测市场数据
- 多因子策略分析 (时间衰减、信号强度、流动性、动量、技术指标)
- 短期价格预测 (5分钟/15分钟/1小时)
- 回测验证系统

### 套利子系统 (Arbitrage Subsystem)

统一套利交易系统，支持三种策略：

| 策略 | 原理 | 条件 |
|------|-----|------|
| **资金费率套利** | 做多现货+做空永续，收取资金费 | 费率 > 0.03% |
| **期现套利** | 做多现货+做空季度合约，锁定基差 | 年化 > 15% |
| **稳定币套利** | 利用稳定币之间的价差 | 价差 > 0.5% |

### 现货自动交易子系统 (Spot Auto Trading)

基于 Binance 现货 K 线和行情的自动交易系统，默认 dry-run：

- 趋势策略: `MA(9/21) + RSI + 动量 + 成交额过滤`
- 退出规则: 止损 / 止盈 / 趋势转弱平仓
- 资金统计: 支持 `--initial-capital`，按每笔交易更新累计收益
- 执行模式: 支持模拟交易与实盘交易

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

### 3. 运行预测系统

```bash
# 查看所有预测
python main.py predict

# 查看特定加密货币
python main.py predict --crypto BTC

# 查看交易机会
python main.py predict --opportunities

# 持续监控模式
python main.py predict --watch

# 运行回测
python main.py predict --backtest

# 或使用模块方式运行
python -m prediction.main --help
```

### 4. 运行套利系统

```bash
# 查看收益公式
python main.py arb --formulas

# 扫描套利机会
python main.py arb --scan

# 查看资金费率
python main.py arb --funding-rates

# 查看稳定币价差
python main.py arb --stablecoin-spreads

# 持续监控模式
python main.py arb --monitor

# 或使用模块方式运行
python -m arbitrage.main --help
```

### 5. 运行现货自动交易系统

```bash
# 单次扫描（默认 dry-run）
python main.py spot

# 扫描 + 自动模拟执行
python main.py spot --auto-execute

# 持续监控 + 模拟执行
python main.py spot --monitor --auto-execute --interval 30

# 指定初始资金并跟踪每笔交易后收益
python main.py spot --monitor --auto-execute --interval 30 --initial-capital 10000

# 实盘模式（谨慎）
python main.py spot --monitor --auto-execute --live

# 或使用模块方式运行
python -m spot.main --help
```

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

# Polymarket API (预测系统可选)
POLY_PRIVATE_KEY=your_private_key
POLY_API_KEY=your_api_key
POLY_API_SECRET=your_api_secret
POLY_API_PASSPHRASE=your_passphrase

# OKX API (备用价格源)
OKX_API_KEY=your_okx_api_key
OKX_API_SECRET=your_okx_api_secret
OKX_PASSPHRASE=your_okx_passphrase

# 系统配置
POLYMARKET_DEMO_MODE=false
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

## 更新日志

### v2.0.0 (2024)
- 新增套利子系统
- 支持三种套利策略
- 实现四层架构 (行情/策略/执行/风控)
- 完善风控机制

### v1.0.0
- 基于 Polymarket 的价格预测系统
- 多因子策略分析
- 回测验证功能
