#!/usr/bin/env python3
"""
refresh_balance.py - 刷新 Polymarket 余额
==========================================

功能: 尝试刷新余额授权并获取最新余额
用途: 当链上有余额但 API 返回 0 时，尝试触发余额同步

API 端点:
  - POST /update-balance-allowance (尝试刷新)
  - GET /balance-allowance (获取余额)

使用方法:
  python refresh_balance.py

依赖:
  pip install eth-account py-clob-client httpx

注意:
  如果 API 返回 balance=0 但链上有余额，通常需要:
  1. 确保 EOA 有 MATIC 支付 Gas
  2. 在 Polymarket 网站上授权 (Approve) USDC
"""

import os
from pathlib import Path
from datetime import datetime
import httpx

# Load .env
env_path = Path(__file__).parent / '.env'
for line in env_path.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        key, _, value = line.partition('=')
        os.environ[key.strip()] = value.strip()

from py_clob_client.signing.hmac import build_hmac_signature
from eth_account import Account

api_key = os.environ.get('POLY_API_KEY')
api_secret = os.environ.get('POLY_API_SECRET')
api_passphrase = os.environ.get('POLY_API_PASSPHRASE')
private_key = os.environ.get('POLY_PRIVATE_KEY')

eoa_address = Account.from_key(private_key).address

print("=" * 50)
print("Refreshing Polymarket Balance")
print("=" * 50)
print(f"EOA Address: {eoa_address}")

def make_request(sign_path, request_path, method="GET"):
    timestamp = int(datetime.now().timestamp())
    signature = build_hmac_signature(api_secret, timestamp, method, sign_path, None)
    
    headers = {
        'POLY_ADDRESS': eoa_address,
        'POLY_API_KEY': api_key,
        'POLY_TIMESTAMP': str(timestamp),
        'POLY_SIGNATURE': signature,
        'POLY_PASSPHRASE': api_passphrase,
    }
    
    with httpx.Client(timeout=30.0) as client:
        url = f'https://clob.polymarket.com{request_path}'
        response = client.get(url, headers=headers)
        return response

# Step 1: Update balance allowance
print("\n[1] Updating balance allowance...")
try:
    response = make_request('/update-balance-allowance', '/update-balance-allowance?asset_type=COLLATERAL&signature_type=2')
    print(f"    Status: {response.status_code}")
    print(f"    Response: {response.text[:200]}")
except Exception as e:
    print(f"    Error: {e}")

# Step 2: Get balance
print("\n[2] Getting balance...")
try:
    response = make_request('/balance-allowance', '/balance-allowance?asset_type=COLLATERAL&signature_type=2')
    print(f"    Status: {response.status_code}")
    result = response.json()
    print(f"    Raw response: {result}")
    
    balance = float(result.get('balance', 0))
    if balance > 1e6:
        balance = balance / 1e6
    
    print(f"\n    💰 Balance: ${balance:.2f} USDC")
except Exception as e:
    print(f"    Error: {e}")

print("\n" + "=" * 50)