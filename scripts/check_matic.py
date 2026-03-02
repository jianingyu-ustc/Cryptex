#!/usr/bin/env python3
"""
check_matic.py - 检查 MATIC 余额 (Gas 费用)
============================================

功能: 检查 EOA 和 Proxy Wallet 的 MATIC 余额
用途: 确保有足够的 MATIC 支付交易 Gas 费用

为什么需要 MATIC:
  - Polygon 网络的 Gas 费用以 MATIC 支付
  - 授权 (Approve) USDC 需要 Gas
  - 下单交易需要 Gas
  - 建议 EOA 至少有 0.1 MATIC

使用方法:
  python check_matic.py

依赖:
  pip install eth-account
"""

import os
from pathlib import Path
import json
import urllib.request

# Load .env
env_path = Path(__file__).parent / '.env'
for line in env_path.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        key, _, value = line.partition('=')
        os.environ[key.strip()] = value.strip()

from eth_account import Account

proxy_wallet = os.environ.get('POLY_PROXY_WALLET')
private_key = os.environ.get('POLY_PRIVATE_KEY')
eoa_address = Account.from_key(private_key).address

print("=" * 50)
print("Checking MATIC balance for gas fees")
print("=" * 50)

rpc_endpoints = [
    'https://rpc.ankr.com/polygon',
    'https://polygon.llamarpc.com',
]

def check_matic(address, label):
    rpc_payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "eth_getBalance",
        "params": [address, "latest"],
        "id": 1
    })
    
    for rpc_url in rpc_endpoints:
        try:
            req = urllib.request.Request(
                rpc_url,
                data=rpc_payload.encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                result = json.loads(r.read().decode())
                if 'result' in result:
                    balance_wei = int(result['result'], 16)
                    balance_matic = balance_wei / 1e18
                    print(f"\n{label}:")
                    print(f"  Address: {address}")
                    print(f"  MATIC: {balance_matic:.6f}")
                    return balance_matic
        except Exception as e:
            continue
    print(f"\n{label}: Error querying")
    return 0

eoa_matic = check_matic(eoa_address, "EOA (pays gas)")
proxy_matic = check_matic(proxy_wallet, "Proxy Wallet")

print("\n" + "=" * 50)
if eoa_matic < 0.01:
    print("⚠️  EOA needs MATIC for gas!")
    print("   Send at least 0.1 MATIC to:")
    print(f"   {eoa_address}")
else:
    print(f"✅ EOA has {eoa_matic:.4f} MATIC")