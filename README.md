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

## 🎯 策略原理

### 核心逻辑

```
预测方向 = Polymarket概率 × 6因子调整

6因子:
1. 时间过滤: 剩余>3分钟入场
2. 信号强度: |概率-50%| > 5%
3. 极端概率: >70%或<30%时警惕
4. 流动性: >$10K才交易
5. 价格动量: 实时RSI/SMA确认
6. 技术指标: 趋势一致性验证
```

### 订单簿分析

```python
# 买卖压力信号
if bid_ask_ratio > 1.5:  # 买单多
    signal = "BUY"
if buy_volume_pct > 65%:  # 成交以买为主
    signal = "STRONG_BUY"
```

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