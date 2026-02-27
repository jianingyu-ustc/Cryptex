# 🚀 Polymarket Crypto Predictor

基于 Polymarket 预测市场数据的加密货币涨跌预测系统。

## 功能特点

- 🔍 **实时市场数据**: 从 Polymarket API 获取加密货币相关预测市场
- 📊 **多时间框架**: 支持 5分钟、15分钟、1小时的短期预测
- ?? **智能分析**: 自动计算概率、置信度和市场情绪
- 🎯 **机会发现**: 识别高置信度的交易机会
- 📈 **实时监控**: Watch 模式持续跟踪市场变化
- 🔄 **策略回测**: 基于历史数据验证预测策略准确性

## 支持的加密货币

- BTC (Bitcoin)
- ETH (Ethereum)
- SOL (Solana)
- DOGE (Dogecoin)
- XRP (Ripple)
- BNB (Binance Coin)
- ADA (Cardano)
- AVAX (Avalanche)
- MATIC (Polygon)
- DOT (Polkadot)

## 安装

```bash
# 进入项目目录
cd btc_prediction/polymarket_crypto_predictor

# 安装依赖
pip install -r requirements.txt
```

## 使用方法

### 基本用法

```bash
# 显示所有短期预测
python main.py

# 显示特定加密货币的预测
python main.py --crypto BTC
python main.py --crypto ETH

# 按时间框架筛选
python main.py --crypto BTC --timeframe 5min
python main.py --crypto ETH --tf 1hour

# 显示最佳交易机会
python main.py --opportunities

# 显示所有加密货币市场 (不限于短期)
python main.py --all
```

### 实时监控模式

```bash
# 监控所有机会
python main.py --watch

# 监控特定加密货币
python main.py --watch --crypto BTC

# 自定义刷新间隔 (秒)
python main.py --watch --interval 60
```

### 策略回测

```bash
# 运行回测 (自动获取历史数据)
python main.py --backtest

# 使用模拟数据回测 (API不可用时)
python main.py --backtest --demo

# 指定回测时间范围
python main.py --backtest --hours 12

# 只回测特定加密货币
python main.py --backtest --crypto BTC
```

### 命令行参数

| 参数 | 简写 | 说明 |
|------|------|------|
| `--crypto` | `-c` | 加密货币符号 (BTC, ETH, etc.) |
| `--timeframe` | `--tf` | 时间框架 (5min, 15min, 1hour) |
| `--opportunities` | `-o` | 显示最佳交易机会 |
| `--all` | `-a` | 显示所有加密市场 |
| `--watch` | `-w` | 启用实时监控模式 |
| `--interval` | `-i` | 监控刷新间隔 (秒) |
| `--min-confidence` | | 最小置信度阈值 (0-1) |
| `--backtest` | `-b` | 运行策略回测 |
| `--hours` | | 回测时间范围 (小时, 默认: 6) |
| `--demo` | | 使用模拟数据回测 |
| `--include-settled` | | 包含已结算的历史市场 |

## 输出说明

### 预测表格字段

- **Crypto**: 加密货币符号
- **Time**: 时间框架 (5min/15min/1hour)
- **Direction**: 预测方向 (UP ↑ / DOWN ↓ / NEUTRAL ↔)
- **Probability**: 市场概率 (Polymarket 上的 YES 价格)
- **Confidence**: 置信度评分 (基于流动性、交易量和概率偏离度)
- **Vol 24h**: 24小时交易量
- **Sentiment**: 市场情绪 (Bullish/Bearish/Neutral)

### 置信度计算

置信度基于以下因素:
1. **概率确定性** (40%): 概率偏离 50% 越远，确定性越高
2. **市场健康度** (40%): 基于流动性和交易量
3. **交易活跃度** (20%): 近期交易数量

## API 说明

本项目使用 Polymarket 的公开 API:

- **Gamma API**: `https://gamma-api.polymarket.com` - 市场数据
- **Data API**: `https://data-api.polymarket.com` - 交易数据

无需 API 密钥，直接使用公开端点。

## 项目结构

```
polymarket_crypto_predictor/
├── main.py          # 主入口点
├── predictor.py     # 预测引擎核心逻辑
├── api_client.py    # Polymarket API 客户端
├── backtest.py      # 策略回测模块
├── display.py       # 终端显示模块
├── demo_data.py     # 演示数据生成
├── config.py        # 配置文件
├── requirements.txt # 依赖列表
└── README.md        # 本文档
```

## 回测输出说明

```
╭───────────────────────────── 📈 Backtest Results ─────────────────────────────╮
│ Total Predictions: 72                                                         │
│ ├─ ✅ Correct: 38                                                             │
│ ├─ ❌ Incorrect: 34                                                           │
│ └─ ❓ Unknown: 0                                                              │
│                                                                               │
│ Accuracy: 52.8%                                                               │
│ Strong Signal Accuracy: 55.2% (45 trades)                                     │
│                                                                               │
│ 💰 Simulated ROI                                                              │
│ Invested: $7,200                                                              │
│ Returned: $7,410                                                              │
│ ROI: +2.9%                                                                    │
╰───────────────────────────────────────────────────────────────────────────────╯
```

### 回测指标说明

| 指标 | 说明 |
|------|------|
| **Accuracy** | 预测准确率 (正确预测数 / 总预测数) |
| **Strong Signal Accuracy** | 强信号准确率 (概率偏离50%超过5%的预测) |
| **ROI** | 模拟投资回报率 (假设每次下注$100，赢得支付95%) |

### 策略评估标准

- **准确率 > 55%**: ✅ 策略有正向优势，可考虑纸上交易
- **准确率 50-55%**: ⚠️ 接近盈亏平衡，需要优化
- **准确率 < 50%**: ❌ 策略表现不佳，需重新审视逻辑

## 示例输出

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║    🚀 POLYMARKET CRYPTO PREDICTOR 🚀                                          ║
║    Real-time cryptocurrency price predictions based on Polymarket data        ║
╚═══════════════════════════════════════════════════════════════════════════════╝

📊 Market Overview
Total Markets: 45
24h Volume: $1.23M
Total Liquidity: $5.67M

🎯 Top Trading Opportunities

1. BTC (5min) 🟢 ↑ [72%] Confidence: 65%
   └─ Will Bitcoin price be above $95,000 in 5 minutes?

2. ETH (1hour) 🔴 ↓ [68%] Confidence: 58%
   └─ Will Ethereum drop below $3,000 in the next hour?
```

## 注意事项

⚠️ **风险提示**:
- 本工具仅供参考，不构成投资建议
- 预测市场数据反映的是市场参与者的集体判断，不保证准确性
- 加密货币交易存在高风险，请谨慎决策

## 技术原理

### 预测市场基础

Polymarket 是一个去中心化预测市场，用户可以对未来事件下注。对于加密货币 5 分钟涨跌市场：

- **市场问题**: "Bitcoin Up or Down - February 27 8:35AM-8:40AM ET"
- **两个结果**: UP (上涨) 或 DOWN (下跌)
- **概率定价**: 如果 UP 的价格是 $0.52，表示市场认为上涨概率为 52%

### 核心预测策略

本系统采用**群体智慧跟随策略**：

```
预测方向 = UP   如果 P(UP) > 50%
预测方向 = DOWN 如果 P(UP) < 50%
```

**原理**: Polymarket 的价格反映了所有参与者的加权平均预期。当多数人预期上涨时，UP 的价格会高于 50%。我们跟随这个群体共识。

### 置信度评分算法

置信度 (0-100%) 综合三个维度：

| 维度 | 权重 | 计算方式 |
|------|------|----------|
| **概率偏离度** | 40% | 概率距离 50% 越远，确定性越高 |
| **市场健康度** | 40% | 流动性 + 24h交易量 |
| **交易活跃度** | 20% | 近期买卖订单数量 |

```python
# 简化的置信度计算
probability_score = abs(probability - 0.5) * 2  # 0-1
health_score = min(1, (liquidity + volume_24h) / 100000)
activity_score = min(1, recent_trades / 50)

confidence = 0.4 * probability_score + 0.4 * health_score + 0.2 * activity_score
```

### 市场情绪判断

基于最近 50 笔交易的买卖比例：

| 买入比例 | 情绪 |
|----------|------|
| > 60% | Bullish (看涨) |
| < 40% | Bearish (看跌) |
| 40-60% | Neutral (中性) |

### 共识聚合

当同一加密货币有多个时间框架的市场时，系统会：

1. 收集所有相关市场的预测方向
2. 计算方向一致性 (Agreement)
3. 加权平均置信度
4. 输出综合共识

### 回测验证

回测模块通过以下方式验证策略：

1. 获取历史已结算的 5 分钟市场
2. 模拟当时的预测决策 (基于概率 > 50% 则预测 UP)
3. 与实际结果对比，计算准确率
4. 模拟投资回报 (每次下注 $100，赢得支付 95%)

### 策略局限性

⚠️ 本策略存在以下局限：

1. **信息延迟**: 概率反映的是下注时刻的市场预期，不保证准确
2. **流动性风险**: 低流动性市场的价格可能被少数大单操纵
3. **手续费影响**: 实际交易需考虑平台手续费和滑点
4. **黑天鹅事件**: 突发新闻可能导致价格剧烈波动

### 理论基础

- **有效市场假说 (EMH)**: 价格反映所有已知信息
- **群体智慧**: 大量参与者的平均预测往往优于个体
- **概率校准**: 预测市场的价格通常与实际发生概率高度相关

## License

MIT License