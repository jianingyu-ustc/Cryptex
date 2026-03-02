#!/usr/bin/env python3
"""
check_balance.py - 检查链上 USDC 余额
======================================

功能: 通过 Polygon RPC 直接查询链上 USDC 余额
用途: 验证资金是否正确到账，排查充值问题

涉及地址:
  - Proxy Wallet: Polymarket 代理钱包，交易资金应该在这里
  - EOA: 由私钥派生的外部账户，用于签名

使用方法:
  python check_balance.py

依赖:
  pip install eth-account

注意:
  - 查询的是 Polygon 网络上的 USDC 余额
  - USDC 合约: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
"""

import os
from pathlib import Path
import json
import urllib.request

# Load .env
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, _, value = line.partition('=')
            os.environ[key.strip()] = value.strip()

proxy_wallet = os.environ.get('POLY_PROXY_WALLET')
private_key = os.environ.get('POLY_PRIVATE_KEY')

from eth_account import Account
eoa_address = Account.from_key(private_key).address

print("=" * 50)
print("Checking on-chain balances...")
print("=" * 50)

# USDC contract on Polygon
usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Multiple RPC endpoints
rpc_endpoints = [
    'https://rpc.ankr.com/polygon',
    'https://polygon.llamarpc.com',
    'https://polygon-bor-rpc.publicnode.com',
]

def check_usdc_balance(address, label):
    """Check USDC balance via RPC"""
    wallet_padded = address.lower().replace('0x', '').zfill(64)
    call_data = f"0x70a08231{wallet_padded}"
    
    rpc_payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": usdc_contract, "data": call_data}, "latest"],
        "id": 1
    })
    
    for rpc_url in rpc_endpoints:
        try:
            req = urllib.request.Request(
                rpc_url,
                data=rpc_payload.encode('utf-8'),
                headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                result = json.loads(r.read().decode())
                if 'result' in result:
                    balance_wei = int(result['result'], 16)
                    balance_usdc = balance_wei / 1e6
                    print(f"\n{label}:")
                    print(f"  Address: {address}")
                    print(f"  USDC Balance: ${balance_usdc:.6f}")
                    return balance_usdc
        except Exception as e:
            continue
    
    print(f"\n{label}:")
    print(f"  Address: {address}")
    print(f"  Error: Could not query balance")
    return 0

# Check both addresses
print(f"\n🔍 Checking balances on Polygon network...")
proxy_balance = check_usdc_balance(proxy_wallet, "Proxy Wallet (资金应该在这里)")
eoa_balance = check_usdc_balance(eoa_address, "EOA Address (签名用)")

print("\n" + "=" * 50)
if proxy_balance > 0:
    print(f"✅ Proxy Wallet has ${proxy_balance:.2f} USDC")
elif eoa_balance > 0:
    print(f"⚠️  资金在 EOA 地址而非 Proxy Wallet!")
    print(f"   需要通过 Polymarket 网站将资金转移到 Proxy Wallet")
else:
    print("❌ 两个地址都没有 USDC 余额")
    print("   请检查：")
    print("   1. 是否充值到了正确的地址")
    print("   2. 是否使用了 Polygon 网络")
    print("   3. 交易是否已确认")