#!/usr/bin/env python3
"""
Polymarket Wallet Status - 钱包状态综合检查工具
================================================

功能说明:
  - 检查链上 USDC 余额 (Proxy Wallet 和 EOA)
  - 检查 MATIC 余额 (用于支付 Gas 费用)
  - 检查 Polymarket API 返回的余额和授权状态
  - 诊断常见问题并给出解决建议

地址说明:
  - EOA (Externally Owned Account): 由私钥控制的账户，用于签名交易
  - Proxy Wallet: Polymarket 为你创建的智能合约钱包，用于存放交易资金
  - 私钥派生 EOA 地址: Account.from_key(private_key).address

使用方法:
  python wallet_status.py

依赖:
  pip install eth-account py-clob-client httpx web3

作者: Zulu (AI Assistant)
日期: 2026-03-01
"""

import os
import sys
from pathlib import Path
from datetime import datetime

# ==================== 加载环境变量 ====================

def load_env():
    """从 .env 文件加载环境变量"""
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                os.environ[key.strip()] = value.strip()
    else:
        print("❌ 未找到 .env 文件")
        sys.exit(1)

load_env()

# ==================== 导入依赖 ====================

try:
    from eth_account import Account
except ImportError:
    print("❌ 请安装 eth-account: pip install eth-account")
    sys.exit(1)

try:
    from py_clob_client.signing.hmac import build_hmac_signature
    HAS_SDK = True
except ImportError:
    HAS_SDK = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from web3 import Web3
    HAS_WEB3 = True
except ImportError:
    HAS_WEB3 = False

# ==================== 配置 ====================

# 从环境变量读取
PROXY_WALLET = os.environ.get('POLY_PROXY_WALLET', '')
PRIVATE_KEY = os.environ.get('POLY_PRIVATE_KEY', '')
API_KEY = os.environ.get('POLY_API_KEY', '')
API_SECRET = os.environ.get('POLY_API_SECRET', '')
API_PASSPHRASE = os.environ.get('POLY_API_PASSPHRASE', '')

# 派生 EOA 地址
EOA_ADDRESS = Account.from_key(PRIVATE_KEY).address if PRIVATE_KEY else ''

# Polygon 网络配置
USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
RPC_ENDPOINTS = [
    'https://polygon-bor-rpc.publicnode.com',
    'https://rpc.ankr.com/polygon',
    'https://polygon.llamarpc.com',
]

# ==================== 链上余额检查 ====================

def check_onchain_balances():
    """检查链上 USDC 和 MATIC 余额"""
    print("\n" + "=" * 60)
    print("📊 链上余额检查 (On-chain Balance)")
    print("=" * 60)
    
    if not HAS_WEB3:
        print("⚠️  web3 未安装，跳过链上检查")
        print("   安装: pip install web3")
        return None, None, None, None
    
    # 连接 RPC
    w3 = None
    for rpc in RPC_ENDPOINTS:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc))
            if w3.is_connected():
                break
        except:
            continue
    
    if not w3 or not w3.is_connected():
        print("❌ 无法连接到 Polygon RPC")
        return None, None, None, None
    
    # 转换为 checksum 地址
    eoa = w3.to_checksum_address(EOA_ADDRESS)
    proxy = w3.to_checksum_address(PROXY_WALLET)
    
    # USDC 合约 ABI (只需要 balanceOf)
    usdc_abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_CONTRACT), abi=usdc_abi)
    
    # 查询余额
    eoa_matic = w3.from_wei(w3.eth.get_balance(eoa), 'ether')
    proxy_matic = w3.from_wei(w3.eth.get_balance(proxy), 'ether')
    eoa_usdc = usdc.functions.balanceOf(eoa).call() / 1e6
    proxy_usdc = usdc.functions.balanceOf(proxy).call() / 1e6
    
    # 显示结果
    print(f"\n🔑 EOA 地址 (签名/Gas): {eoa}")
    print(f"   MATIC: {eoa_matic:.6f}")
    print(f"   USDC:  ${eoa_usdc:.6f}")
    
    print(f"\n💼 Proxy Wallet (交易资金): {proxy}")
    print(f"   MATIC: {proxy_matic:.6f}")
    print(f"   USDC:  ${proxy_usdc:.6f}")
    
    return eoa_matic, eoa_usdc, proxy_matic, proxy_usdc

# ==================== API 余额检查 ====================

def check_api_balance():
    """检查 Polymarket API 返回的余额"""
    print("\n" + "=" * 60)
    print("🌐 API 余额检查 (Polymarket CLOB API)")
    print("=" * 60)
    
    if not HAS_SDK or not HAS_HTTPX:
        print("⚠️  缺少依赖，跳过 API 检查")
        print("   安装: pip install py-clob-client httpx")
        return None, None
    
    if not API_KEY or not API_SECRET:
        print("❌ API 凭证未配置")
        return None, None
    
    # 构建请求
    sign_path = '/balance-allowance'
    request_path = '/balance-allowance?asset_type=COLLATERAL&signature_type=2'
    
    timestamp = int(datetime.now().timestamp())
    signature = build_hmac_signature(API_SECRET, timestamp, "GET", sign_path, None)
    
    headers = {
        'POLY_ADDRESS': EOA_ADDRESS,
        'POLY_API_KEY': API_KEY,
        'POLY_TIMESTAMP': str(timestamp),
        'POLY_SIGNATURE': signature,
        'POLY_PASSPHRASE': API_PASSPHRASE,
    }
    
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(f'https://clob.polymarket.com{request_path}', headers=headers)
            result = response.json()
            
            balance = float(result.get('balance', 0))
            if balance > 1e6:
                balance = balance / 1e6
            
            allowances = result.get('allowances', {})
            total_allowance = sum(float(v) for v in allowances.values())
            if total_allowance > 1e6:
                total_allowance = total_allowance / 1e6
            
            print(f"\n💰 API 返回余额: ${balance:.2f} USDC")
            print(f"📊 授权额度: ${total_allowance:.2f}")
            print(f"\n   原始响应: {result}")
            
            return balance, total_allowance
            
    except Exception as e:
        print(f"❌ API 请求失败: {e}")
        return None, None

# ==================== 诊断建议 ====================

def diagnose(eoa_matic, eoa_usdc, proxy_matic, proxy_usdc, api_balance, api_allowance):
    """根据检查结果给出诊断建议"""
    print("\n" + "=" * 60)
    print("🔍 诊断结果 (Diagnosis)")
    print("=" * 60)
    
    issues = []
    
    # 检查 MATIC (Gas)
    if eoa_matic is not None and eoa_matic < 0.01:
        issues.append({
            'level': '❌',
            'issue': 'EOA 没有 MATIC，无法支付 Gas 费用',
            'solution': f'向 {EOA_ADDRESS} 转入 0.1-0.5 MATIC'
        })
    
    # 检查 USDC
    if proxy_usdc is not None and proxy_usdc == 0:
        issues.append({
            'level': '⚠️',
            'issue': 'Proxy Wallet 没有 USDC',
            'solution': '通过 Polymarket 网站充值 USDC'
        })
    
    # 检查 API vs 链上
    if api_balance is not None and proxy_usdc is not None:
        if proxy_usdc > 0 and api_balance == 0:
            issues.append({
                'level': '⚠️',
                'issue': '链上有余额但 API 返回 0',
                'solution': '需要在 Polymarket 网站上授权 (Approve) USDC'
            })
    
    # 检查授权
    if api_allowance is not None and api_allowance == 0:
        issues.append({
            'level': '⚠️',
            'issue': 'USDC 未授权给交易合约',
            'solution': '在 Polymarket 网站尝试下单，会提示授权'
        })
    
    # 显示结果
    if not issues:
        print("\n✅ 一切正常！钱包状态良好。")
    else:
        print(f"\n发现 {len(issues)} 个问题:\n")
        for i, item in enumerate(issues, 1):
            print(f"{item['level']} 问题 {i}: {item['issue']}")
            print(f"   💡 解决方案: {item['solution']}\n")

# ==================== 主函数 ====================

def main():
    """主入口"""
    print("=" * 60)
    print("🔐 Polymarket 钱包状态检查")
    print("=" * 60)
    
    print(f"\n配置信息:")
    print(f"  API Key: {API_KEY[:8]}...{API_KEY[-4:]}" if len(API_KEY) > 12 else "  API Key: [未配置]")
    print(f"  EOA: {EOA_ADDRESS}")
    print(f"  Proxy: {PROXY_WALLET}")
    
    # 执行检查
    eoa_matic, eoa_usdc, proxy_matic, proxy_usdc = check_onchain_balances()
    api_balance, api_allowance = check_api_balance()
    
    # 诊断
    diagnose(eoa_matic, eoa_usdc, proxy_matic, proxy_usdc, api_balance, api_allowance)
    
    print("\n" + "=" * 60)
    print("检查完成!")
    print("=" * 60)

if __name__ == "__main__":
    main()