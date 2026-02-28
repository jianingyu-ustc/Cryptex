# 🚀 Polymarket Crypto Predictor

基于 Polymarket 预测市场 + 交易所实时数据的加密货币涨跌预测系统。

## ✨ 功能

- 📊 **多时间框架预测**: 5分钟 / 15分钟 / 1小时
- 🔄 **多数据源**: Polymarket + OKX + Binance + CoinGecko + Kraken
- 📈 **6因子策略**: 时间、信号强度、极端概率、流动性、动量、技术指标
- 🧪 **回测验证**: 与实盘使用完全相同的策略代码

## 🚀 快速开始

```bash
# 1. 安装
pip install -r requirements.txt

# 2. 配置 (可选)
cp .env.example .env

# 3. 测试连接
python3 test_server.py --quick

# 4. 运行
python3 main.py
```

## 📖 使用方法

```bash
# 显示预测
python main.py
python main.py --crypto BTC --tf 5min

# 实时监控
python main.py --watch

# 策略回测
python main.py --backtest --hours 6
```

## 🌍 海外服务器部署

由于 API 访问限制，建议在海外服务器运行：

```bash
ssh root@your-server
bash deploy.sh  # 一键部署
```

推荐: Vultr 东京 ($5/月) / DigitalOcean 新加坡 ($6/月)

## 📊 数据源

| 数据源 | 用途 | 地区限制 |
|--------|------|----------|
| **Polymarket** | 群体预测概率 | 需海外IP |
| **OKX** | 价格/订单簿/K线 | ✅ 全球 |
| **Binance** | 价格/订单簿/K线 | ❌ US受限 |
| **CoinGecko** | 市值/24h变化 | ✅ 全球 |

**优先级**: OKX → Binance → CoinGecko → Kraken

系统自动检测 US IP 并切换 Binance.US 端点。

## 🎯 策略原理 (v2.0)

### 核心算法

```
raw_score = Σ(wᵢ × fᵢ)        // 加权求和，fᵢ ∈ [-1, 1]
confidence = sigmoid(α × raw_score + β)  // 输出 ∈ (0, 1)
```

### 6因子 (标准化到 [-1, 1])

| 因子 | 权重 | 正向信号 | 负向信号 |
|------|------|----------|----------|
| 时间衰减 | 15% | 剩余>3分钟 | 剩余<1分钟 |
| 信号强度 | 25% | \|概率-50%\|>15% | <5% |
| 极端概率 | 10% | 概率适中 | >70%或<30% |
| 流动性 | 15% | >$100K | <$10K |
| 动量一致 | 20% | 动量与方向一致 | 背离 |
| 技术确认 | 15% | RSI+趋势确认 | RSI超买/超卖 |

### 仓位管理

```
position = bankroll × 2% × conf_adj × vol_adj × loss_decay
```

- 单笔风险 ≤ 2%
- 高波动自动减仓
- 连续亏损后衰减 (γ=0.7)
- 回撤 >20% 停止交易

## 🧪 回测

回测模块直接调用 `predictor.py` 的策略方法，确保结果准确：

```python
# backtest.py
self._predictor = CryptoPredictor()
direction, confidence = await self._apply_strategy_with_predictor(...)
```

```bash
python main.py --backtest --hours 24 --crypto btc,eth
```

## ⚙️ 配置

编辑 `.env` 文件：

```bash
# OKX (推荐)
OKX_API_KEY=your_key
OKX_API_SECRET=your_secret
OKX_PASSPHRASE=your_passphrase

# Binance (可选)
BINANCE_API_KEY=your_key
```

## 📁 项目结构

```
├── main.py          # 入口
├── predictor.py     # 预测引擎 (6因子策略)
├── price_client.py  # 价格数据 (OKX/Binance/...)
├── backtest.py      # 回测 (复用predictor策略)
├── api_client.py    # Polymarket API
├── test_server.py   # 连接测试
└── deploy.sh        # 一键部署
```

## ⚠️ 风险提示

- 本工具仅供研究参考，不构成投资建议
- 预测市场概率 ≠ 实际结果
- 加密货币交易存在高风险

## 📜 License

MIT