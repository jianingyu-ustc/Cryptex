# 🚀 Polymarket Crypto Predictor

基于 Polymarket 预测市场数据的加密货币涨跌预测系统。

## 功能特点

- 🔍 **实时市场数据**: 从 Polymarket API 获取加密货币相关预测市场
- 📊 **多时间框架**: 支持 5分钟、15分钟、1小时的短期预测
- 💡 **智能分析**: 自动计算概率、置信度和市场情绪
- 🎯 **机会发现**: 识别高置信度的交易机会
- 📈 **实时监控**: Watch 模式持续跟踪市场变化

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
├── display.py       # 终端显示模块
├── config.py        # 配置文件
├── requirements.txt # 依赖列表
└── README.md        # 本文档
```

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

Polymarket 是一个去中心化预测市场，用户可以对未来事件下注。本工具:

1. 抓取 Polymarket 上与加密货币相关的预测市场
2. 识别短期 (5分钟/15分钟/1小时) 的价格预测市场
3. 分析市场数据，包括概率、流动性、交易量
4. 计算置信度评分
5. 聚合多个市场得出共识方向

## License

MIT License