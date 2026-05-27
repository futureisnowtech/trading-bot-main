"""
setup.py — Run ONCE before first use.
Creates .env, installs all dependencies, verifies imports, initializes DB.
Usage: python setup.py
"""
import os, sys, subprocess, shutil


def main():
    print("\n👑 Setting up The King's Algo Trading System...\n")

    # Directories
    for d in ['logs', 'logs/csv', 'logs/backtest', 'logs/memory']:
        os.makedirs(d, exist_ok=True)
    print("✅ Directories created")

    # .env
    if not os.path.exists('.env'):
        shutil.copy('.env.example', '.env')
        print("✅ .env created from .env.example")
        print("   ⚠️  EDIT .env WITH YOUR CREDENTIALS BEFORE STARTING\n")
    else:
        print("✅ .env already exists")

    # .gitignore
    if not os.path.exists('.gitignore'):
        with open('.gitignore', 'w') as f:
            f.write(".env\nlogs/\n*.db\n__pycache__/\n*.pyc\n.webull/\n.DS_Store\nvenv/\n")
        print("✅ .gitignore created")

    # Install deps
    print("\n📦 Installing dependencies (2-3 minutes)...")
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt', '-q'],
        capture_output=False
    )
    if result.returncode != 0:
        print("⚠️  Some packages failed to install — check output above")
    else:
        print("✅ Dependencies installed")

    # Verify imports
    print("\n🔍 Verifying imports...")
    required = ['pandas', 'numpy', 'yfinance', 'schedule', 'pytz', 'requests', 'bs4', 'dotenv']
    optional = ['webull', 'coinbase', 'backtesting', 'streamlit', 'telegram', 'pandas_ta', 'lancedb', 'sentence_transformers']

    all_good = True
    for mod in required:
        try:
            __import__(mod)
            print(f"  ✅ {mod}")
        except ImportError:
            print(f"  ❌ {mod} — MISSING (required)")
            all_good = False

    for mod in optional:
        try:
            __import__(mod)
            print(f"  ✅ {mod} (optional)")
        except ImportError:
            print(f"  ⚠️  {mod} — not installed (optional, run: pip install {mod})")

    # Init DB
    print("\n🗄️  Initializing database...")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from logging_db.trade_logger import init_db
        init_db()
        print("✅ logs/trades.db ready")
    except Exception as e:
        print(f"❌ DB init failed: {e}")

    print("""
╔══════════════════════════════════════════════════════════╗
║  SETUP COMPLETE — NEXT STEPS                             ║
╠══════════════════════════════════════════════════════════╣
║                                                          ║
║  1. Edit .env with your credentials:                     ║
║     - ANTHROPIC_API_KEY  (console.anthropic.com)         ║
║     - WEBULL_USERNAME / PASSWORD / TRADE_PIN             ║
║     - COINBASE_API_KEY / API_SECRET                      ║
║     - TELEGRAM_BOT_TOKEN / CHAT_ID                       ║
║                                                          ║
║  2. Backtest first:                                      ║
║     python run_backtest.py --strategy crypto             ║
║     python run_backtest.py --strategy equity             ║
║                                                          ║
║  3. Start paper trading:                                 ║
║     python main.py --mode paper                          ║
║                                                          ║
║  4. Dashboard (second terminal):                         ║
║     streamlit run dashboard/app.py                       ║
║     → http://localhost:8501                              ║
║                                                          ║
║  5. Run 2+ weeks paper before going live                 ║
║                                                          ║
║  "Nothing is given. Everything is earned." 👑            ║
╚══════════════════════════════════════════════════════════╝
""")


if __name__ == '__main__':
    main()
