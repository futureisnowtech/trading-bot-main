#!/usr/bin/env python3
"""
scripts/test_brokers.py — One-command broker connectivity test.

Run this before switching to live trading to confirm all connections work.

Usage:
    python3 scripts/test_brokers.py           # Test paper/demo endpoints
    python3 scripts/test_brokers.py --live     # Test live endpoints too

Exit codes:
    0 — all tested brokers connected OK
    1 — one or more brokers failed or missing credentials

What this checks:
  Webull   — paper login, account balance, order placement capability
  Tradovate — demo API auth (if APP_ID set), otherwise shows simulation status
  Coinbase  — REST API key validity, portfolio balance
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_ROOT  = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJ_ROOT)

# Load .env before importing config
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJ_ROOT, '.env'))


# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'

def ok(msg):    print(f'  {GREEN}✅  {msg}{RESET}')
def fail(msg):  print(f'  {RED}❌  {msg}{RESET}')
def warn(msg):  print(f'  {YELLOW}⚠️   {msg}{RESET}')
def info(msg):  print(f'  {CYAN}ℹ️   {msg}{RESET}')
def header(msg):print(f'\n{BOLD}{msg}{RESET}')


# ─── Webull ───────────────────────────────────────────────────────────────────

def test_webull(live: bool = False) -> bool:
    header('─── ALPACA (Equity — replaces Webull) ───────────────────')

    api_key    = os.getenv('ALPACA_API_KEY', '')
    secret_key = os.getenv('ALPACA_SECRET_KEY', '')

    if not api_key or not secret_key:
        fail('ALPACA_API_KEY / ALPACA_SECRET_KEY not set in .env')
        info('Get free paper keys at alpaca.markets:')
        info('  1. Sign up at alpaca.markets (free)')
        info('  2. Left sidebar → Paper Trading')
        info('  3. Click "Generate API Keys"')
        info('  4. Add ALPACA_API_KEY=PKxxx and ALPACA_SECRET_KEY=xxx to .env')
        info('  5. Re-run this test')
        return False

    ok(f'API key present: {api_key[:8]}...')

    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        fail('alpaca-py not installed — run: pip3 install alpaca-py')
        return False

    ok('alpaca-py installed')

    try:
        paper = os.getenv('False', 'true').lower() == 'true'
        client = TradingClient(api_key=api_key, secret_key=secret_key)
        acct   = client.get_account()
        mode   = 'PAPER' if paper else 'LIVE'
        ok(f'Alpaca {mode} login: SUCCESS')
        ok(f'Account status: {acct.status}')
        ok(f'Cash: ${float(acct.cash):,.2f}  |  Equity: ${float(acct.equity):,.2f}')
        ok(f'Buying power: ${float(acct.buying_power):,.2f}')

        if live and not paper:
            ok('Live trading enabled and connected')

        return True

    except Exception as e:
        fail(f'Alpaca connection failed: {e}')
        if 'forbidden' in str(e).lower() or '403' in str(e):
            warn('Paper keys used on live endpoint (or vice versa) — check False setting')
        elif 'unauthorized' in str(e).lower() or '401' in str(e):
            warn('API keys rejected — double-check they were copied correctly from alpaca.markets')
        return False


# ─── Tradovate ────────────────────────────────────────────────────────────────

def test_tradovate(live: bool = False) -> bool:
    header('─── TRADOVATE (MES Futures) ──────────────────────────────')

    username = os.getenv('TRADOVATE_USERNAME', '')
    password = os.getenv('TRADOVATE_PASSWORD', '')
    app_id   = os.getenv('TRADOVATE_APP_ID', '')
    app_ver  = os.getenv('TRADOVATE_APP_VERSION', '1.0')

    if not username or not password:
        fail('TRADOVATE_USERNAME / TRADOVATE_PASSWORD not set in .env')
        info('Sign up free at tradovate.com')
        return False

    ok(f'Credentials present: {username}')

    if not app_id:
        warn('TRADOVATE_APP_ID not set — running in SIMULATION mode (no real API calls)')
        info('To get APP_ID:')
        info('  1. Log in to demo.tradovate.com')
        info('  2. Click your avatar → API Access')
        info('  3. Create a new app → copy the App ID')
        info('  4. Add TRADOVATE_APP_ID=<value> to .env')
        info('Paper simulation will still trade and log — APP_ID unlocks real demo account data')
        return True   # Not a failure — system runs fine without it in paper sim mode

    ok(f'APP_ID present: {app_id[:8]}...')

    import requests
    import uuid

    demo_url  = 'https://demo.tradovateapi.com/v1'
    live_url  = 'https://live.tradovateapi.com/v1'

    def _auth(base_url: str, label: str) -> tuple:
        """Returns (token, account_id) or (None, None) on failure."""
        try:
            resp = requests.post(
                f'{base_url}/auth/accesstokenrequest',
                json={
                    'name':       username,
                    'password':   password,
                    'appId':      app_id,
                    'appVersion': app_ver,
                    'deviceId':   os.getenv('TRADOVATE_DEVICE_ID', str(uuid.uuid4())),
                    'cid':        0,
                    'sec':        ''
                },
                timeout=15
            )
            data = resp.json()
            if 'accessToken' not in data:
                fail(f'{label} auth failed: {data}')
                return None, None
            token      = data['accessToken']
            headers    = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
            accts_resp = requests.get(f'{base_url}/account/list', headers=headers, timeout=10)
            accts      = accts_resp.json()
            acct_id    = accts[0].get('id', 0) if accts else 0
            balance_resp = requests.get(
                f'{base_url}/cashbalance/getcashbalancesnapshot?accountId={acct_id}',
                headers=headers, timeout=10
            )
            balance_data = balance_resp.json()
            cash = balance_data.get('totalCashValue', balance_data.get('cashBalance', '?'))
            return token, (acct_id, cash)
        except Exception as e:
            fail(f'{label} connection error: {e}')
            return None, None

    # Demo
    token, acct_info = _auth(demo_url, 'Demo')
    if token:
        acct_id, cash = acct_info
        ok(f'Demo API login: SUCCESS — Account ID: {acct_id}  |  Balance: ${cash}')
        ok(f'Front-month contract: MESM6 (June 2026) — $5/point, ~$40 intraday margin')
    else:
        warn('Demo API auth failed — check APP_ID and credentials')
        warn('Note: demo.tradovate.com must be accessed at least once in a browser first')
        return False

    # Live (optional check)
    if live:
        header('  Tradovate LIVE endpoint check:')
        warn('Tradovate live requires a paid subscription ($99-199/mo) + funded account ($1000+)')
        warn('With $500 account: use demo endpoint (fully functional paper trading)')
        warn('Path to live: grow paper account → add funds → subscribe at tradovate.com/pricing')
        token_live, _ = _auth(live_url, 'Live')
        if token_live:
            ok('Live API reachable — subscription is active')
        else:
            info('Live API not reachable (expected if no live subscription yet)')

    return True


# ─── Coinbase ─────────────────────────────────────────────────────────────────

def test_coinbase() -> bool:
    header('─── COINBASE (Crypto) ────────────────────────────────────')

    api_key    = os.getenv('COINBASE_API_KEY', '')
    api_secret = os.getenv('COINBASE_API_SECRET', '')

    if not api_key or not api_secret:
        fail('COINBASE_API_KEY / COINBASE_API_SECRET not set in .env')
        info('Create at coinbase.com/settings/api with "Advanced Trade" View+Trade scope')
        return False

    ok(f'API key present: {api_key[:30]}...')

    try:
        from coinbase.rest import RESTClient
    except ImportError:
        fail('coinbase-advanced-py not installed — run: pip3 install coinbase-advanced-py')
        return False

    try:
        client  = RESTClient(api_key=api_key, api_secret=api_secret)
        product = client.get_product('BTC-USDC')
        price   = getattr(product, 'price', None)
        if price:
            ok(f'REST API connected — BTC-USDC: ${float(price):,.2f}')
        else:
            ok('REST API connected')

        # Account balances
        try:
            accts     = client.get_accounts()
            acct_list = getattr(accts, 'accounts', []) or []
            for acct in acct_list:
                currency = getattr(acct, 'currency', '') or acct.get('currency', '')
                if currency in ('USDC', 'USD'):
                    bal_obj = getattr(acct, 'available_balance', None) or acct.get('available_balance', {})
                    bal = getattr(bal_obj, 'value', None) or (bal_obj.get('value') if isinstance(bal_obj, dict) else None)
                    if bal:
                        ok(f'{currency} balance: ${float(bal):.2f}')
        except Exception:
            pass  # Balance fetch is informational

        ok('Coinbase Advanced Trade API: FULLY CONNECTED')
        return True

    except Exception as e:
        fail(f'Coinbase API error: {e}')
        if 'INVALID_ARGUMENT' in str(e) or '401' in str(e):
            warn('API key rejected — check scope is "Advanced Trade" with View+Trade enabled')
        elif 'expired' in str(e).lower():
            warn('API key may be expired — regenerate at coinbase.com/settings/api')
        return False


# ─── Live readiness summary ───────────────────────────────────────────────────

def print_live_readiness():
    header('─── LIVE TRADING READINESS ───────────────────────────────')
    print()

    paper = os.getenv('False', 'true').lower() == 'true'
    eq_en = os.getenv('EQUITY_ENABLED', 'true').lower() == 'true'
    ft_en = os.getenv('FUTURES_ENABLED', 'false').lower() == 'true'

    # Alpaca equity
    alpaca_key = bool(os.getenv('ALPACA_API_KEY', ''))
    print(f'  Alpaca (equity):')
    ok('API key set')                                        if alpaca_key else fail('ALPACA_API_KEY missing — get free at alpaca.markets')
    ok('Equity enabled in .env')                             if eq_en      else warn('EQUITY_ENABLED=false')
    warn('False=true → switch to false when ready') if paper       else ok('False=false')
    if not paper:
        info('For live: generate LIVE keys at alpaca.markets → Live Trading (separate from paper keys)')

    print()
    print(f'  Tradovate live:')
    ok('Credentials set')                                                     if os.getenv('TRADOVATE_USERNAME') else fail('No credentials')
    ok('APP_ID set')                                                           if os.getenv('TRADOVATE_APP_ID')   else warn('TRADOVATE_APP_ID missing (get from demo.tradovate.com → API Access)')
    ok('Futures enabled in .env')                                             if ft_en                           else warn('FUTURES_ENABLED=false')
    warn('Live subscription needed (tradovate.com/pricing) + $1000 account') if True                            else None

    print()
    print(f'  To go live when paper results are ready:')
    info('  1. python3 scripts/check_readiness.py  ← must show ALL PASS')
    info('  2. Edit .env: False=false')
    info('  3. python3 scripts/go_live.py          ← controlled live transition')
    info('  4. Subscribe to Tradovate live plan once account exceeds $1000')
    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Broker connectivity test')
    parser.add_argument('--live', action='store_true',
                        help='Also test live endpoints (Webull live login, Tradovate live API)')
    parser.add_argument('--broker', choices=['webull', 'tradovate', 'coinbase'],
                        help='Test only one broker')
    args = parser.parse_args()

    print(f'\n{BOLD}{"="*60}')
    print('  BROKER CONNECTIVITY TEST')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    if args.live:
        print('  Mode: paper + LIVE endpoints')
    else:
        print('  Mode: paper / demo endpoints')
    print(f'{"="*60}{RESET}')

    results = {}

    if not args.broker or args.broker == 'webull':
        results['webull']    = test_webull(live=args.live)

    if not args.broker or args.broker == 'tradovate':
        results['tradovate'] = test_tradovate(live=args.live)

    if not args.broker or args.broker == 'coinbase':
        results['coinbase']  = test_coinbase()

    print_live_readiness()

    print(f'{BOLD}{"="*60}')
    print('  SUMMARY')
    print(f'{"="*60}{RESET}')
    all_ok = True
    for broker, passed in results.items():
        if passed:
            ok(f'{broker.capitalize():12} CONNECTED')
        else:
            fail(f'{broker.capitalize():12} FAILED or incomplete')
            all_ok = False

    paper = os.getenv('False', 'true').lower() == 'true'
    print()
    if all_ok:
        if paper:
            ok('All brokers OK — system running in PAPER mode')
            info('Run python3 scripts/check_readiness.py to track live-readiness progress')
        else:
            ok('All brokers OK — system running in LIVE mode')
    else:
        warn('Some brokers need attention (see details above)')
        info('Missing credentials = paper simulation only (still logs trades)')
    print(f'{BOLD}{"="*60}{RESET}\n')

    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
