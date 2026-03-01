# Polymarket Crypto Predictor

基于 Polymarket 预测市场数据的加密货币价格预测系统。

## 项目结构

### 核心模块
- main.py - 主入口，CLI 命令行接口
- config.py - 配置管理，从 .env 加载配置
- predictor.py - 预测引擎，核心预测逻辑
- api_client.py - API 客户端，与 Polymarket 公共 API 交互
- price_client.py - 价格客户端，获取实时/历史价格
- display.py - 显示模块，Rich 终端输出
- demo_data.py - 演示数据，API 不可用时的模拟数据

### 钱包模块
- polymarket_clob_client.py - CLOB API 客户端，L2 认证和交易
- wallet_status.py - 钱包状态检查，综合诊断工具
- check_balance.py - USDC 余额检查
- check_matic.py - MATIC 余额检查 (Gas 费用)
- check_matic_web3.py - MATIC 检查 (Web3 版本)
- refresh_balance.py - 刷新余额
- test_sdk_balance.py - SDK 测试

### 回测模块
- backtest.py - 回测引擎，策略历史表现分析

### 测试脚本
- test_api.py - API 测试
- test_server.py - 服务器测试
- debug_api.py - API 调试
- debug_backtest.py - 回测调试
- find_active.py - 查找活跃市场

## 快速开始

1. 安装依赖: pip install -r requirements.txt
2. 配置环境: cp .env.example .env
3. 运行预测: python main.py

## 环境变量

- POLY_API_KEY - API 密钥
- POLY_API_SECRET - API 签名密钥
- POLY_API_PASSPHRASE - API 口令
- POLY_PRIVATE_KEY - EOA 私钥
- POLY_PROXY_WALLET - 代理钱包地址

## 常用命令

- python main.py - 运行预测
- python wallet_status.py - 检查钱包状态
- python check_balance.py - 检查 USDC 余额
- python test_api.py - API 测试

## 地址说明

- EOA: 由私钥派生，用于签名和支付 Gas
- Proxy Wallet: 存放 USDC 交易资金

## 常见问题

1. API 返回余额为 0: 需要在网站上授权 USDC
2. 无法支付 Gas: EOA 需要 MATIC

## License

MIT License