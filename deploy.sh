#!/bin/bash
#
# Polymarket Crypto Predictor - Deployment Script
# Run this script on an overseas server to deploy the application
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/your-repo/main/deploy.sh | bash
#   OR
#   bash deploy.sh
#

set -e

# Colors
GREEN='\033[92m'
RED='\033[91m'
YELLOW='\033[93m'
CYAN='\033[96m'
NC='\033[0m' # No Color
BOLD='\033[1m'

echo -e "${BOLD}${CYAN}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║     🚀 Cryptex Trading System - Deployment Script             ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check Python version
echo -e "${CYAN}Checking Python version...${NC}"
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
    echo -e "${GREEN}✅ Python $PYTHON_VERSION found${NC}"
else
    echo -e "${RED}❌ Python3 not found. Installing...${NC}"
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y python3 python3-pip
    elif command -v yum &> /dev/null; then
        sudo yum install -y python3 python3-pip
    else
        echo -e "${RED}Please install Python 3.8+ manually${NC}"
        exit 1
    fi
fi

# Check pip
echo -e "${CYAN}Checking pip...${NC}"
if ! command -v pip3 &> /dev/null; then
    echo -e "${YELLOW}Installing pip...${NC}"
    python3 -m ensurepip --upgrade 2>/dev/null || curl https://bootstrap.pypa.io/get-pip.py | python3
fi
echo -e "${GREEN}✅ pip ready${NC}"

# Create virtual environment (optional but recommended)
if [ ! -d "venv" ]; then
    echo -e "${CYAN}Creating virtual environment...${NC}"
    python3 -m venv venv 2>/dev/null || echo -e "${YELLOW}venv creation skipped${NC}"
fi

# Activate venv if exists
if [ -d "venv" ]; then
    source venv/bin/activate 2>/dev/null || true
fi

# Install dependencies
echo -e "${CYAN}Installing dependencies...${NC}"
pip3 install -q --upgrade pip
pip3 install -q -r requirements.txt 2>/dev/null || pip3 install -q rich httpx python-dateutil

echo -e "${GREEN}✅ Dependencies installed${NC}"

# Setup environment file
if [ ! -f ".env" ]; then
    echo -e "${CYAN}Creating .env file...${NC}"
    cp .env.example .env 2>/dev/null || cat > .env << 'EOF'
# OKX API Key (recommended - global exchange, works in most regions)
OKX_API_KEY=
OKX_API_SECRET=
OKX_PASSPHRASE=

# Binance API Key (optional - may be blocked in US/EU)
BINANCE_API_KEY=
BINANCE_API_SECRET=

# Demo mode disabled for production
POLYMARKET_DEMO_MODE=false
EOF
    echo -e "${YELLOW}⚠️  Please edit .env to add your OKX/Binance API key (optional)${NC}"
fi

# Run connectivity test
echo ""
echo -e "${CYAN}Running connectivity test...${NC}"
python3 tests/test_server.py --quick 2>/dev/null || echo -e "${YELLOW}⚠️  Connectivity test skipped (test file not found)${NC}"

echo ""
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}   ✅ Deployment Complete!${NC}"
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Quick start commands:${NC}"
echo ""
echo -e "  ${BOLD}📈 预测系统 (Prediction):${NC}"
echo -e "  python3 main.py predict                 # 查看所有预测"
echo -e "  python3 main.py predict --crypto BTC    # BTC 预测"
echo -e "  python3 main.py predict --opportunities # 交易机会"
echo -e "  python3 main.py predict --backtest      # 运行回测"
echo -e "  python3 main.py predict --watch         # 实时监控模式"
echo ""
echo -e "  ${BOLD}💰 套利系统 (Arbitrage):${NC}"
echo -e "  python3 main.py arb --formulas          # 查看收益公式"
echo -e "  python3 main.py arb --scan              # 扫描套利机会"
echo -e "  python3 main.py arb --funding-rates     # 查看资金费率"
echo -e "  python3 main.py arb --monitor           # 持续监控模式"
echo ""
echo -e "${YELLOW}Note: Edit .env to add your OKX/Binance API key (套利系统必需)${NC}"