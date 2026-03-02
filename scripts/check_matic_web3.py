#!/usr/bin/env python3
"""
check_matic_web3.py - 使用 Web3 检查 MATIC 余额
================================================

功能: 使用 web3.py 库检查 MATIC 余额
用途: check_matic.py 的替代方案，更可靠

与 check_matic.py 的区别:
  - check_matic.py: 使用原生 urllib，轻量但不稳定
  - check_matic_web3.py: 使用 web3.py，更可靠

使用方法:
  python check_matic_web3.py

依赖:
  pip install web3 eth-account
"""

from web3 import Web3
import os
from pathlib import Path

# Load .env (from project root, parent of scripts/)
env_path = Path(__file__).parent.parent / '.env'
if not env_path.exists():
    print(f"Error: .env file not found at {env_path}")
    print("Please create .env file in Cryptex/ directory")
    exit(1)
for line in env_path.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        key, _, value = line.partition('=')
        os.environ[key.strip()] = value.strip()

from eth_account import Account

proxy_wallet = os.environ.get('POLY_PROXY_WALLET')
private_key = os.environ.get('POLY_PRIVATE_KEY')
eoa_address = Account.from_key(private_key).address

# Connect to Polygon
w3 = Web3(Web3.HTTPProvider('https://polygon-bor-rpc.publicnode.com'))

# Convert to checksum addresses
proxy_wallet = w3.to_checksum_address(proxy_wallet)
eoa_address = w3.to_checksum_address(eoa_address)

print('=' * 50)
print('Checking MATIC Balance')
print('=' * 50)
print(f'Connected: {w3.is_connected()}')

# Check MATIC balance
eoa_balance = w3.eth.get_balance(eoa_address)
proxy_balance = w3.eth.get_balance(proxy_wallet)

print(f'\nEOA Address: {eoa_address}')
print(f'  MATIC: {w3.from_wei(eoa_balance, "ether"):.6f}')

print(f'\nProxy Wallet: {proxy_wallet}')
print(f'  MATIC: {w3.from_wei(proxy_balance, "ether"):.6f}')

print('\n' + '=' * 50)
if eoa_balance < w3.to_wei(0.01, 'ether'):
    print('⚠️  EOA needs MATIC for gas!')
    print(f'   Send at least 0.1 MATIC to:')
    print(f'   {eoa_address}')
else:
    print(f'✅ EOA has enough MATIC for gas')