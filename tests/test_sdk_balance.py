#!/usr/bin/env python3
"""
test_sdk_balance.py - 测试 Polymarket API 余额获取
===================================================

功能: 使用正确的 L2 认证方式调用 Polymarket CLOB API
用途: 验证 API 凭证是否正确，测试余额获取

L2 认证头:
  - POLY_ADDRESS: EOA 地址 (从私钥派生)
  - POLY_API_KEY: API 密钥
  - POLY_TIMESTAMP: Unix 时间戳
  - POLY_SIGNATURE: HMAC-SHA256 签名
  - POLY_PASSPHRASE: API 口令

API 端点:
  GET /balance-allowance?asset_type=COLLATERAL&signature_type=2

使用方法:
  python test_sdk_balance.py

依赖:
  pip install eth-account py-clob-client
"""

import os
from pathlib import Path
from datetime import datetime

# Load .env manually (from project root, parent of tests/)
env_path = Path(__file__).parent.parent / ".env"
if not env_path.exists():
    print(f"Error: .env file not found at {env_path}")
    print("Please create .env file in Cryptex/ directory")
    exit(1)
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, _, value = line.partition('=')
            os.environ[key.strip()] = value.strip()

# Load credentials
api_key = os.environ.get('POLY_API_KEY')
api_secret = os.environ.get('POLY_API_SECRET')
api_passphrase = os.environ.get('POLY_API_PASSPHRASE')
proxy_wallet = os.environ.get('POLY_PROXY_WALLET')
private_key = os.environ.get('POLY_PRIVATE_KEY')

print("=" * 50)
print("Polymarket Balance Test")
print("=" * 50)

# Validate credentials
if not api_key:
    print("\nError: POLY_API_KEY not configured in .env")
    exit(1)
if not private_key:
    print("\nError: POLY_PRIVATE_KEY not configured in .env")
    exit(1)

print(f"\nAPI Key: {api_key[:8]}...")
print(f"Proxy Wallet: {proxy_wallet}")

# Get EOA address from private key
from eth_account import Account
eoa_address = Account.from_key(private_key).address
print(f"EOA Address: {eoa_address}")

# Use SDK's HMAC signing
from py_clob_client.signing.hmac import build_hmac_signature
from py_clob_client.http_helpers.helpers import get

print("\nBuilding L2 headers...")
timestamp = int(datetime.now().timestamp())
method = "GET"

# Sign without query params, but request with them
sign_path = "/balance-allowance"
# asset_type should be COLLATERAL (for USDC), and need signature_type=2
request_path = "/balance-allowance?asset_type=COLLATERAL&signature_type=2"

hmac_sig = build_hmac_signature(api_secret, timestamp, method, sign_path, None)

headers = {
    "POLY_ADDRESS": eoa_address,
    "POLY_SIGNATURE": hmac_sig,
    "POLY_TIMESTAMP": str(timestamp),
    "POLY_API_KEY": api_key,
    "POLY_PASSPHRASE": api_passphrase,
}

print(f"Headers: {list(headers.keys())}")

print("\nFetching USDC balance...")
try:
    result = get(f"https://clob.polymarket.com{request_path}", headers=headers)
    print(f"Result: {result}")
    
    if isinstance(result, dict):
        balance = float(result.get("balance", 0)) / 1e6
        allowance = float(result.get("allowance", 0)) / 1e6
        print(f"\n💰 Balance: ${balance:.2f} USDC")
        print(f"📊 Allowance: ${allowance:.2f}")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 50)