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

## 🌍 海外服务器部署

由于 Polymarket 和 Binance API 在中国大陆可能无法访问，建议在海外服务器上部署运行。

### 快速部署

```bash
# SSH 到你的海外服务器
ssh root@your-server-ip

# 克隆项目
git clone https://github.com/your-repo/polymarket-crypto-predictor.git
cd polymarket-crypto-predictor

# 一键部署
bash deploy.sh
```

### 手动部署

```bash
# 1. 安装依赖
pip3 install -r requirements.txt

# 2. 配置环境变量 (可选)
cp .env.example .env
vim .env  # 添加你的 Binance API Key

# 3. 运行测试
python3 test_server.py

# 4. 开始使用
python3 main.py
```

### 部署脚本 vs 测试脚本

系统提供两个脚本，功能不同：

| 脚本 | 用途 | 执行时机 |
|------|------|----------|
| `deploy.sh` | 一次性安装部署 | 首次部署时运行 |
| `test_server.py` | 诊断和测试 | 遇到问题时运行 |

#### deploy.sh - 部署脚本

```bash
# 首次部署时运行
bash deploy.sh
```

**功能：**
- ✅ 检查/安装 Python 环境
- ✅ 安装 pip 依赖
- ✅ 创建虚拟环境 (可选)
- ✅ 复制 `.env.example` → `.env`
- ✅ 运行快速连通性测试

#### test_server.py - 测试脚本

```bash
# 完整测试 (测试所有 API 和模块)
python3 test_server.py

# 快速测试 (仅测试网络连通性和主要 API)
python3 test_server.py --quick
```

**功能：**
- ✅ 网络连通性测试 (Google DNS, Cloudflare)
- ✅ Binance API 测试 (多端点 + DNS 诊断)
- ✅ CoinGecko API 测试
- ✅ 备用 API 测试 (Kraken, CryptoCompare, CoinPaprika)
- ✅ Polymarket API 测试
- ✅ 价格客户端模块测试
- ✅ 预测引擎模块测试
- ✅ 回测模块测试
- ✅ 生成详细测试报告

**使用场景：**
- 部署后验证系统是否正常
- 遇到 API 连接问题时诊断
- 定期检查系统健康状态

### 推荐的海外服务器

| 提供商 | 地区 | 最低价格 | 推荐理由 |
|--------|------|----------|----------|
| **Vultr** | 东京/新加坡 | $5/月 | 低延迟，性价比高 |
| **DigitalOcean** | 新加坡 | $6/月 | 稳定可靠 |
| **AWS Lightsail** | 东京 | $3.5/月 | 免费额度 |
| **Linode** | 东京 | $5/月 | 网络质量好 |

### 后台运行

```bash
# 使用 nohup 后台运行
nohup python3 main.py --watch > output.log 2>&1 &

# 使用 screen
screen -S predictor
python3 main.py --watch
# Ctrl+A, D 退出 screen

# 使用 tmux
tmux new -s predictor
python3 main.py --watch
# Ctrl+B, D 退出 tmux
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
├── predictor.py     # 预测引擎核心逻辑 (含多因子策略)
├── api_client.py    # Polymarket API 客户端
├── price_client.py  # 外部价格数据客户端 (Binance/CoinGecko)
├── backtest.py      # 策略回测模块
├── display.py       # 终端显示模块
├── demo_data.py     # 演示数据生成
├── config.py        # 配置文件
├── requirements.txt # 依赖列表
└── README.md        # 本文档
```

## 多因子数据源

### 外部价格数据 (price_client.py)

系统集成了多个外部价格数据源，用于实时验证和技术分析：

| 数据源 | 用途 | 延迟 | API Key | 地区限制 |
|--------|------|------|---------|----------|
| **OKX API** | 实时价格、订单簿、K线、成交 | ~100ms | 可选 | ✅ 全球可用 |
| **Binance API** | 实时价格、K线数据 | ~100ms | 可选 | ❌ 美国/部分欧洲受限 |
| **CoinGecko API** | 24h变化、市值数据 | ~10s | 不需要 | ✅ 全球可用 |
| **Kraken API** | 价格、订单簿 | ~200ms | 不需要 | ✅ 欧洲友好 |
| **CryptoCompare** | 聚合价格数据 | ~500ms | 不需要 | ✅ 全球可用 |
| **CoinPaprika** | 市场数据 | ~1s | 不需要 | ✅ 全球可用 |

**数据源优先级**: OKX → Binance → CoinGecko → Kraken → CryptoCompare → CoinPaprika

### 自动 IP 地区检测

系统启动时会自动检测当前 IP 所在地区，并智能切换 Binance 端点：

```
检测到 US IP → 优先使用 api.binance.us
检测到非 US IP → 优先使用 api.binance.com
```

**工作原理：**
1. 调用 ip-api.com / ipinfo.io 检测当前 IP 地区
2. 如果是美国 IP，自动将 `api.binance.us` 放在端点列表最前面
3. 如果是非美国 IP，使用全球 Binance 端点（api.binance.com 等）
4. 无论哪种情况，都会将另一个作为备用

**无需手动配置** - 系统会自动选择最合适的端点！

### OKX API 配置 (推荐)

OKX 是全球性交易所，在大多数地区（包括 Binance 被封锁的地区）都可以正常访问：

```bash
# 编辑 .env 文件
vim .env
```

```bash
# OKX API Key (推荐 - 全球可用)
OKX_API_KEY=your_okx_api_key_here
OKX_API_SECRET=your_okx_api_secret_here
OKX_PASSPHRASE=your_okx_passphrase_here
```

**获取 OKX API Key：**
1. 登录 [OKX](https://www.okx.com/)
2. 进入 API Management: 个人中心 → API
3. 创建新的 API Key（选择 "只读" 权限）
4. 设置 Passphrase 并保存
5. 复制 API Key、Secret 和 Passphrase 到 `.env` 文件

### Binance API 配置

系统支持使用 Binance API Key 来获得更高的请求速率限制：

```bash
# 1. 复制环境变量模板
cp .env.example .env

# 2. 编辑 .env 文件，填入您的 API Key
vim .env
```

`.env` 文件内容示例：
```bash
# Binance API Key (只需要读取权限)
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_secret_here  # 如果只读取数据，可以留空

# 禁用演示模式，使用真实数据
POLYMARKET_DEMO_MODE=false
```

**获取 Binance API Key：**
1. 登录 [Binance](https://www.binance.com/)
2. 进入 API Management: Account → API Management
3. 创建新的 API Key（只需要 "Read" 权限即可）
4. 复制 API Key 到 `.env` 文件

**注意事项：**
- `.env` 文件已添加到 `.gitignore`，不会被提交到版本控制
- 即使没有 API Key，系统也可以使用公共端点（速率限制较低）
- 系统会自动尝试多个 Binance 端点以提高可用性

### Binance API 端点说明

系统使用以下 Binance REST API 端点（根据官方文档 [binance-spot-api-docs](https://developers.binance.com/docs/binance-spot-api-docs/rest-api)）：

| 端点 | 用途 | 权重 | 说明 |
|------|------|------|------|
| `GET /api/v3/ticker/24hr` | 24小时统计 | 40 | 价格、成交量、涨跌幅 |
| `GET /api/v3/ticker/price` | 实时价格 | 2 | 最轻量的价格获取 |
| `GET /api/v3/avgPrice` | 5分钟均价 | 2 | 平滑的价格指标 |
| `GET /api/v3/ticker/bookTicker` | 最优挂单 | 2 | 最佳买卖价（比depth快） |
| `GET /api/v3/depth` | 订单簿 | 5-50 | 完整市场深度 |
| `GET /api/v3/trades` | 近期成交 | 25 | 最近成交记录 |
| `GET /api/v3/aggTrades` | 聚合成交 | 2 | 合并同价同时成交（更适合大单分析） |
| `GET /api/v3/klines` | K线数据 | 2 | 历史蜡烛图 |
| `GET /api/v3/exchangeInfo` | 交易规则 | 20 | tick_size、lot_size等 |

**端点选择建议：**
- 只需价格 → `ticker/price`（权重最低）
- 需要买卖价差 → `bookTicker`（比depth快10倍）
- 分析大单 → `aggTrades`（聚合同价成交）
- 完整深度分析 → `depth`（配合limit参数）

### 获取的数据指标

```python
# 实时价格动量
PriceMomentum:
  - current_price      # 当前价格
  - momentum_1m        # 1分钟动量 (%)
  - momentum_5m        # 5分钟动量 (%)
  - volatility_5m      # 5分钟波动率
  - trend_direction    # 趋势方向 (UP/DOWN/NEUTRAL)

# 技术指标
TechnicalIndicators:
  - sma_5, sma_14      # 简单移动平均线
  - rsi                # 相对强弱指数 (14周期)
  - trend              # 综合趋势 (BULLISH/BEARISH/NEUTRAL)
  - overbought/oversold # 超买/超卖信号

# 订单簿数据 (NEW!)
OrderBookData:
  - best_bid/best_ask  # 最优买卖价
  - spread             # 买卖价差 (USD 和 %)
  - total_bid_volume   # 买单总量 (USD)
  - total_ask_volume   # 卖单总量 (USD)
  - bid_ask_ratio      # 买卖比 (>1 看涨, <1 看跌)
  - imbalance          # 失衡度 [-1, +1]
  - pressure           # 买卖压力 (BUY/SELL/NEUTRAL)
  - large_bid_walls    # 大额买单墙
  - large_ask_walls    # 大额卖单墙

# 市场深度分析 (NEW!)
MarketDepthAnalysis:
  - bid_ask_ratio      # 订单簿买卖比
  - buy_volume_pct     # 近期成交买入占比
  - avg_trade_size     # 平均成交金额
  - large_trade_detected # 是否检测到大单
  - signal             # 综合信号 (STRONG_BUY/BUY/NEUTRAL/SELL/STRONG_SELL)
  - confidence         # 信号置信度 (0-1)
```

### 市场深度分析原理

系统通过分析**订单簿**和**近期成交**来预测短期价格走势：

| 指标 | 权重 | 信号含义 |
|------|------|----------|
| **订单簿买卖比** | 40% | bid_vol/ask_vol > 1.5 = 强买压 |
| **成交买入占比** | 40% | 买入成交 > 65% = 多头主导 |
| **大单检测** | 20% | 大单方向影响短期走势 |

```python
# 信号分数计算
signal_score = 0

# 订单簿信号
if bid_ask_ratio > 1.5:  signal_score += 2  # 强买压
if bid_ask_ratio < 0.67: signal_score -= 2  # 强卖压

# 成交流向信号  
if buy_volume_pct > 0.65: signal_score += 2
if buy_volume_pct < 0.35: signal_score -= 2

# 大单信号
if 大买单 > 大卖单: signal_score += 1
if 大卖单 > 大买单: signal_score -= 1

# 最终信号
signal = "STRONG_BUY" if score >= 3 else
         "BUY" if score >= 1 else
         "STRONG_SELL" if score <= -3 else
         "SELL" if score <= -1 else
         "NEUTRAL"
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

**基础原理**: Polymarket 的价格反映了所有参与者的加权平均预期。但简单跟随当前概率存在局限：概率会随价格实时变化，当前快照不等于最终结果。

### 改进策略：多因子预测模型

为提高最终结果的预测准确率，系统采用以下改进策略：

#### 策略 1: 早期入场 + 概率阈值过滤

```
只在以下条件同时满足时入场：
1. 市场剩余时间 > 3 分钟 (避免后期追高/追低)
2. |P(UP) - 50%| > 5% (只取有明确方向的信号)
3. 流动性 > $10,000 (避免被操纵的小市场)
```

**原理**: 早期概率更多反映"预期"而非"已发生"，有更大的alpha空间。

#### 策略 2: 动量反转检测

```python
# 检测概率变化趋势
if 当前概率 > 55% and 概率在过去1分钟下降 > 5%:
    # 可能是假突破，降低置信度或跳过
    skip_trade()
    
if 当前概率 < 45% and 概率在过去1分钟上升 > 5%:
    # 可能反转，降低置信度
    skip_trade()
```

**原理**: 快速反转的概率信号往往不可靠。

#### 策略 3: 极端概率反向操作

```
if P(UP) > 70%:
    # 市场可能过度乐观，考虑反向下注 DOWN
    consider_contrarian = True
    
if P(UP) < 30%:
    # 市场可能过度悲观，考虑反向下注 UP
    consider_contrarian = True
```

**原理**: 极端概率往往意味着价格已大幅波动，后续可能回调。

#### 策略 4: 多时间框架一致性

```
if 5min市场预测UP and 15min市场预测UP and 1h市场预测UP:
    confidence_boost = 1.3  # 置信度加成
else:
    confidence_penalty = 0.7  # 置信度惩罚
```

**原理**: 多时间框架信号一致时，趋势更可靠。

#### 策略 5: 波动率调整

```python
btc_volatility = 计算最近5分钟BTC价格标准差
if btc_volatility > 历史平均 * 1.5:
    # 高波动期，降低仓位或跳过
    reduce_position_size()
```

**原理**: 高波动期预测难度增大，应降低暴露。

### 策略组合权重

| 策略 | 权重 | 说明 |
|------|------|------|
| 基础共识 | 30% | 跟随 P(UP) > 50% |
| 早期信号 | 25% | 只取开盘3分钟内的信号 |
| 动量确认 | 20% | 概率趋势与方向一致 |
| 极端反转 | 15% | 极端概率时反向考虑 |
| 多框架一致 | 10% | 跨时间框架确认 |

### 概率动态变化示例

⚠️ **重要说明**: 在 5 分钟市场到期之前，概率会随着 BTC 实际价格的变化而**动态调整**：

```
时间轴示例 (8:35AM-8:40AM ET 市场):

8:35:00 开盘价 $96,500
        └─ UP: 50% | DOWN: 50% (平衡)

8:36:30 BTC 涨至 $96,580
        └─ UP: 65% | DOWN: 35% (市场预期维持涨势)

8:38:00 BTC 跌至 $96,450
        └─ UP: 40% | DOWN: 60% (市场预期反转)

8:40:00 结算价 $96,520 > 开盘价
        └─ UP: 100% | DOWN: 0% (UP 获胜)
```

因此，**入场时机**至关重要：
- 越早入场，不确定性越高，但潜在回报也越高
- 越晚入场，概率越确定，但赔率也越低
- 本系统显示的是**当前时刻**的概率快照，不是最终结果

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

### 实盘数据验证

系统支持从 Polymarket API 获取真实历史数据进行回测验证：

```bash
# 在海外服务器运行（需要能访问 Polymarket API）
ssh root@your-server

# 回测最近 6 小时
python main.py --backtest --hours 6

# 回测最近 24 小时（更多数据，更准确的评估）
python main.py --backtest --hours 24 --crypto btc,eth,sol
```

#### 验证流程

```
1. API 扫描
   └─ 遍历过去 N 小时的所有 5 分钟时间槽
   └─ 查询每个槽位的已结算市场 (btc-updown-5m-{timestamp})
   
2. 数据提取
   └─ 获取市场结算时的最终概率
   └─ 获取实际结果 (UP/DOWN)
   └─ 获取流动性和交易量
   
3. 策略模拟
   └─ 应用多因子策略计算预测方向
   └─ 跳过弱信号和低流动性市场
   └─ 记录预测 vs 实际结果
   
4. 统计分析
   └─ 计算总体准确率
   └─ 计算强信号准确率
   └─ 计算模拟 ROI
```

#### 示例实盘回测结果

```
Fetching BTC events from last 24 hours...
⠼ Checked 288 slots, found 245 settled events

╭───────────────────────────── 📈 Backtest Results ─────────────────────────────╮
│ Total Predictions: 245 (after filtering)                                       │
│ ├─ ✅ Correct: 132                                                             │
│ ├─ ❌ Incorrect: 113                                                           │
│ └─ ❓ Unknown: 0                                                               │
│                                                                                │
│ Accuracy: 53.9%                                                                │
│ Strong Signal Accuracy: 56.2% (89 trades)                                      │
│                                                                                │
│ 💰 Simulated ROI                                                               │
│ Invested: $24,500                                                              │
│ Returned: $25,740                                                              │
│ ROI: +5.1%                                                                     │
╰────────────────────────────────────────────────────────────────────────────────╯
```

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