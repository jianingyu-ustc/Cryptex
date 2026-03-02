# 统一套利交易系统 (Unified Arbitrage Trading System)

基于 Binance API 的专业级加密货币套利交易系统，支持三种套利策略。

## 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     Arbitrage Trading System                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   Market Data Layer                       │   │
│  │  • WebSocket 实时行情订阅                                 │   │
│  │  • REST API 备用接口                                      │   │
│  │  • 支持现货 + 永续合约 + 季度合约                          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    Strategy Layer                         │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐ │   │
│  │  │ FundingRate │ │   Basis     │ │ StablecoinSpread    │ │   │
│  │  │  Strategy   │ │ Arbitrage   │ │    Strategy         │ │   │
│  │  └─────────────┘ └─────────────┘ └─────────────────────┘ │   │
│  │                   ↑ BaseStrategy 抽象类                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   Execution Layer                         │   │
│  │  • 下单封装 (Spot + Futures)                              │   │
│  │  • 对冲执行 (Atomic Hedging)                              │   │
│  │  • 自动回滚机制 (Rollback on Failure)                     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                  Risk Control Layer                       │   │
│  │  • 最大仓位限制 (≤50% 账户余额)                           │   │
│  │  • 单策略最大敞口限制 (≤25%)                              │   │
│  │  • 强平风险检测                                           │   │
│  │  • 止损保护 (默认 3%)                                     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## 套利策略收益公式

### 1️⃣ 资金费率套利 (Perpetual Funding Arbitrage)

**原理**: 当永续合约资金费率为正时，做多现货 + 做空永续，收取资金费。

```
净收益 = 资金费率 × 持仓价值 - 交易成本
       = FR × Position - 2 × (taker_fee + slippage) × Position
       = Position × [FR - 0.10%]
```

| 条件 | 最低资金费率 |
|------|------------|
| 盈亏平衡 | > 0.10% |
| 推荐入场 | > 0.20% |
| 最佳时机 | > 0.30% |

**执行逻辑**:
- 当资金费率 > 0.03%: 买入现货 + 做空永续
- 若资金费率转负: 平仓退出

### 2️⃣ 期现套利 (Cash & Carry Arbitrage)

**原理**: 利用季度合约相对现货的溢价，锁定无风险收益。

```
基差 = (期货价格 - 现货价格) / 现货价格
年化收益率 = 基差 × (365 / 到期天数) × 100%

净年化收益 = 年化收益率 - 年化交易成本
           = 年化收益率 - [2 × 0.05% × (365 / D)]
```

| 条件 | 最低年化收益 |
|------|------------|
| 盈亏平衡 | 覆盖成本即可 |
| 推荐入场 | > 15% |
| 最佳时机 | > 25% |

**执行逻辑**:
- 当年化收益 > 15%: 买入现货 + 做空季度合约
- 到期前 3 天: 自动平仓

### 3️⃣ 稳定币价差套利 (Stablecoin Spread Arbitrage)

**原理**: 利用 USDT/USDC/BUSD 等稳定币之间的价格偏差获利。

```
价差收益 = (高价稳定币 - 低价稳定币) / 低价稳定币 × 100%
净收益 = 价差 - 2 × (taker_fee + slippage)
       = Spread - 0.10%
```

| 条件 | 最低价差 |
|------|---------|
| 盈亏平衡 | > 0.10% |
| 推荐入场 | > 0.50% |
| 最佳时机 | > 1.00% |

**执行逻辑**:
- 当价差 > 0.50%: 卖出高价稳定币，买入低价稳定币
- 价差收敛时平仓

## 交易成本假设

| 费用类型 | 费率 |
|---------|-----|
| Taker Fee | 0.04% |
| Maker Fee | 0.02% |
| 滑点估计 | 0.01% |
| **单向成本** | **0.05%** |
| **双向成本** | **0.10%** |

> ⚠️ 所有收益必须扣除成本后才允许执行

## 模块说明

### 文件结构

```
arbitrage/
├── __init__.py          # 模块导出
├── config.py            # 配置管理
├── api.py               # Binance API 客户端
├── strategy.py          # 策略层实现
├── execution.py         # 执行层实现
├── risk.py              # 风控层实现
├── main.py              # 主入口
└── README.md            # 文档
```

### 核心模块

| 模块 | 功能 |
|------|-----|
| `api.py` | 统一 API 客户端，支持现货、永续、交割合约 |
| `strategy.py` | 三种套利策略 + BaseStrategy 抽象类 |
| `execution.py` | 原子化对冲执行 + 自动回滚 |
| `risk.py` | 仓位限制、止损、强平检测 |
| `config.py` | 可配置参数 + 收益公式说明 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件，填入 Binance API Key
```

### 3. 运行系统

```bash
# 使用统一入口 (推荐)
python main.py arb --formulas           # 查看收益公式
python main.py arb --scan               # 扫描套利机会
python main.py arb --funding-rates      # 查看资金费率
python main.py arb --stablecoin-spreads # 查看稳定币价差
python main.py arb --monitor            # 持续监控模式
python main.py arb --monitor --auto-execute  # 自动执行模式 (谨慎使用)

# 或使用模块方式运行
python -m arbitrage.main --formulas
python -m arbitrage.main --scan
python -m arbitrage.main --funding-rates
python -m arbitrage.main --stablecoin-spreads
python -m arbitrage.main --monitor
python -m arbitrage.main --monitor --auto-execute
```

## API 使用示例

### 初始化系统

```python
import asyncio
from arbitrage import ArbitrageConfig, BinanceClient, StrategyManager

async def main():
    # 创建配置
    config = ArbitrageConfig()
    
    # 创建客户端
    client = BinanceClient(config)
    
    # 测试连接
    if await client.test_connectivity():
        print("Connected to Binance!")
    
    # 创建策略管理器
    manager = StrategyManager(client, config)
    
    # 扫描机会
    signals = await manager.get_best_opportunities(min_profit=0.1)
    
    for signal in signals:
        print(f"{signal.strategy_name}: {signal.symbol}")
        print(f"  Gross Profit: {signal.expected_profit_pct:.4f}%")
        print(f"  Net Profit: {signal.net_profit_pct:.4f}%")
    
    await client.close()

asyncio.run(main())
```

### 获取资金费率

```python
from arbitrage.api import create_client

async def get_funding_rates():
    client = create_client()
    
    # 获取单个
    rate = await client.get_funding_rate("BTCUSDT")
    print(f"BTC Funding Rate: {rate.funding_rate:.4f}%")
    
    # 获取全部
    rates = await client.get_all_funding_rates()
    for r in rates[:10]:
        print(f"{r.symbol}: {r.funding_rate:.4f}%")
    
    await client.close()
```

### 执行对冲交易

```python
from arbitrage import ExecutionEngine, ArbitrageSignal, SignalType

async def execute_hedge():
    config = ArbitrageConfig()
    client = BinanceClient(config)
    engine = ExecutionEngine(client, config)
    
    # 创建信号
    signal = ArbitrageSignal(
        strategy_name="FundingRateStrategy",
        signal_type=SignalType.HEDGE,
        symbol="BTCUSDT",
        side="HEDGE",
        quantity=0.001,
        price=50000,
        reason="Test hedge",
        expected_profit_pct=0.05,
        net_profit_pct=0.04,
        confidence=0.8
    )
    
    # 执行
    result = await engine.execute_signal(signal)
    print(f"Status: {result.status.value}")
    
    await client.close()
```

## 风控参数

| 参数 | 默认值 | 说明 |
|------|-------|-----|
| `max_position_pct` | 50% | 最大持仓占账户比例 |
| `max_single_strategy_pct` | 25% | 单策略最大敞口 |
| `stop_loss_pct` | 3% | 止损阈值 |
| `min_margin_ratio` | 5% | 最低保证金率 |

## 注意事项

1. **API 权限**: 需要启用 Spot 和 Futures 交易权限
2. **资金要求**: 建议至少 1000 USDT 起步
3. **网络稳定**: 套利对延迟敏感，建议使用稳定网络
4. **风险提示**: 套利也有风险，务必理解策略原理后再使用

## License

MIT License