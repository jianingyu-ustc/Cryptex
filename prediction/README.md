# 预测子系统 (Prediction)

基于 Polymarket 预测市场的加密货币短期预测系统。

## 功能概览

- 多币种预测（BTC/ETH/SOL 等）
- 时间粒度：`5min` / `15min` / `1hour`
- 交易机会扫描（opportunities）
- 共识视图（consensus）
- 持续监控（watch）
- 回测与 demo 回测

## 运行指令（从根 README 迁移）

```bash
# 查看所有预测
python -m prediction.main

# 查看特定加密货币
python -m prediction.main --crypto BTC

# 指定时间粒度
python -m prediction.main --crypto ETH --timeframe 5min

# 查看交易机会
python -m prediction.main --opportunities

# 查看市场共识
python -m prediction.main --consensus

# 持续监控
python -m prediction.main --watch
python -m prediction.main --watch --interval 60

# 运行回测
python -m prediction.main --backtest
python -m prediction.main --backtest --hours 12

# 使用 demo 数据回测
python -m prediction.main --backtest --demo

# 查看参数
python -m prediction.main --help
```

## 常用参数

- `--crypto/-c`: 指定币种（如 `BTC`）
- `--timeframe/-t`: 时间粒度（`5min` / `15min` / `1hour`）
- `--opportunities/-o`: 输出交易机会
- `--consensus`: 输出共识视图
- `--watch/-w`: 持续监控
- `--interval/-i`: 监控刷新间隔（秒）
- `--backtest/-b`: 运行回测
- `--hours`: 回测时间窗口（小时）
- `--demo`: 使用演示数据
- `--min-confidence`: 机会筛选最低置信度
