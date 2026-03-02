#!/bin/bash
# =============================================================================
# Cryptex Project Migration Script
# =============================================================================
# 此脚本用于：
# 1. 删除已迁移到子目录的旧文件
# 2. 移动未迁移的相关文件到对应子目录
# =============================================================================

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "=============================================="
echo "   Cryptex Project Migration Script"
echo "=============================================="
echo ""

# =============================================================================
# Step 1: 删除已迁移到 prediction/ 的旧文件
# =============================================================================
log_info "Step 1: 删除已迁移到 prediction/ 的旧文件..."

MIGRATED_FILES=(
    "api_client.py"       # -> prediction/api_client.py
    "predictor.py"        # -> prediction/predictor.py
    "backtest.py"         # -> prediction/backtest.py
    "config.py"           # -> prediction/config.py (prediction specific)
    "display.py"          # -> prediction/display.py
    "demo_data.py"        # -> prediction/demo_data.py
    "price_client.py"     # -> common/price_client.py
)

for file in "${MIGRATED_FILES[@]}"; do
    if [ -f "$file" ]; then
        rm -f "$file"
        log_success "已删除: $file"
    else
        log_warning "文件不存在(可能已删除): $file"
    fi
done

# =============================================================================
# Step 2: 移动 Polymarket 相关文件到 prediction/
# =============================================================================
log_info "Step 2: 移动 Polymarket 相关文件到 prediction/..."

POLY_FILES=(
    "polymarket_clob_client.py"  # CLOB 交易客户端
    "wallet_status.py"           # 钱包状态检查
)

for file in "${POLY_FILES[@]}"; do
    if [ -f "$file" ]; then
        mv "$file" "prediction/"
        log_success "已移动: $file -> prediction/$file"
    else
        log_warning "文件不存在: $file"
    fi
done

# =============================================================================
# Step 3: 创建 scripts/ 目录并移动工具脚本
# =============================================================================
log_info "Step 3: 创建 scripts/ 目录并移动工具脚本..."

mkdir -p scripts

SCRIPT_FILES=(
    "check_balance.py"
    "check_matic.py"
    "check_matic_web3.py"
    "refresh_balance.py"
    "find_active.py"
)

for file in "${SCRIPT_FILES[@]}"; do
    if [ -f "$file" ]; then
        mv "$file" "scripts/"
        log_success "已移动: $file -> scripts/$file"
    else
        log_warning "文件不存在: $file"
    fi
done

# =============================================================================
# Step 4: 创建 tests/ 目录并移动测试文件
# =============================================================================
log_info "Step 4: 创建 tests/ 目录并移动测试文件..."

mkdir -p tests

TEST_FILES=(
    "test_api.py"
    "test_sdk_balance.py"
    "test_server.py"
    "debug_api.py"
    "debug_backtest.py"
)

for file in "${TEST_FILES[@]}"; do
    if [ -f "$file" ]; then
        mv "$file" "tests/"
        log_success "已移动: $file -> tests/$file"
    else
        log_warning "文件不存在: $file"
    fi
done

# =============================================================================
# Step 5: 创建必要的 __init__.py 文件
# =============================================================================
log_info "Step 5: 创建必要的 __init__.py 文件..."

# scripts/__init__.py
if [ ! -f "scripts/__init__.py" ]; then
    echo '"""Utility scripts for Cryptex"""' > scripts/__init__.py
    log_success "已创建: scripts/__init__.py"
fi

# tests/__init__.py
if [ ! -f "tests/__init__.py" ]; then
    echo '"""Tests for Cryptex"""' > tests/__init__.py
    log_success "已创建: tests/__init__.py"
fi

# =============================================================================
# Step 6: 清理 __pycache__ 目录
# =============================================================================
log_info "Step 6: 清理 __pycache__ 目录..."

find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
log_success "已清理所有 __pycache__ 目录"

# =============================================================================
# 完成
# =============================================================================
echo ""
echo "=============================================="
log_success "迁移完成！"
echo "=============================================="
echo ""
echo "新的项目结构："
echo ""
echo "Cryptex/"
echo "├── common/              # 共用模块"
echo "│   ├── __init__.py"
echo "│   └── price_client.py"
echo "├── prediction/          # 预测子系统"
echo "│   ├── __init__.py"
echo "│   ├── api_client.py"
echo "│   ├── predictor.py"
echo "│   ├── backtest.py"
echo "│   ├── config.py"
echo "│   ├── display.py"
echo "│   ├── demo_data.py"
echo "│   ├── main.py"
echo "│   ├── polymarket_clob_client.py"
echo "│   └── wallet_status.py"
echo "├── arbitrage/           # 套利子系统"
echo "│   └── ..."
echo "├── scripts/             # 工具脚本"
echo "│   └── ..."
echo "├── tests/               # 测试文件"
echo "│   └── ..."
echo "├── main.py              # 统一入口"
echo "├── requirements.txt"
echo "├── .env.example"
echo "└── README.md"
echo ""
echo "使用方法："
echo "  python main.py predict              # 运行预测系统"
echo "  python main.py arb --formulas       # 查看套利公式"
echo "  python main.py arb --scan           # 扫描套利机会"
echo ""