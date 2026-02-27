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
echo "║     🚀 Polymarket Crypto Predictor - Deployment Script        ║"
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
# Binance API Key (optional - for better rate limits)
BINANCE_API_KEY=
BINANCE_API_SECRET=

# Demo mode disabled for production
POLYMARKET_DEMO_MODE=false
EOF
    echo -e "${YELLOW}⚠️  Please edit .env to add your Binance API key (optional)${NC}"
fi

# Run connectivity test
echo ""
echo -e "${CYAN}Running connectivity test...${NC}"
python3 test_server.py --quick

echo ""
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}   ✅ Deployment Complete!${NC}"
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Quick start commands:${NC}"
echo -e "  ${BOLD}python3 main.py${NC}                    # Show live predictions"
echo -e "  ${BOLD}python3 main.py --crypto BTC${NC}       # BTC predictions only"
echo -e "  ${BOLD}python3 main.py --backtest${NC}         # Run strategy backtest"
echo -e "  ${BOLD}python3 main.py --watch${NC}            # Live monitoring mode"
echo -e "  ${BOLD}python3 test_server.py${NC}             # Full system test"
echo ""
echo -e "${YELLOW}Note: Edit .env to add your Binance API key for better rate limits${NC}"