"""
scripts/migrate_to_postgres.py
One-way migration script: SQLite -> Postgres.
Assumes Postgres is running and config.py has correct credentials.
"""

import sqlite3
import psycopg2
from psycopg2 import sql
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def migrate():
    print(f"Starting migration: {config.DB_PATH} -> {config.DB_NAME} on {config.DB_HOST}")
    
    # 1. Connect to SQLite
    sl_conn = sqlite3.connect(config.DB_PATH)
    sl_cur = sl_conn.cursor()
    
    # 2. Connect to Postgres
    pg_conn = psycopg2.connect(
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT
    )
    pg_cur = pg_conn.cursor()
    
    # 3. Get all tables from SQLite
    sl_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [t[0] for t in sl_cur.fetchall()]
    
    for table in tables:
        print(f"  Migrating table: {table}...")
        
        # Get schema
        sl_cur.execute(f"PRAGMA table_info({table})")
        columns = sl_cur.fetchall()
        
        # Build CREATE TABLE for Postgres (simplified mapping)
        col_defs = []
        for col in columns:
            name = col[1]
            ctype = col[2].upper()
            is_pk = "PRIMARY KEY" if col[5] else ""
            
            # Type mapping
            if "INTEGER" in ctype:
                pg_type = "SERIAL" if col[5] and table != 'open_positions' else "INTEGER"
            elif "REAL" in ctype or "FLOAT" in ctype:
                pg_type = "DOUBLE PRECISION"
            elif "TEXT" in ctype:
                pg_type = "TEXT"
            else:
                pg_type = "TEXT"
                
            col_defs.append(f"{name} {pg_type} {is_pk}")
            
        create_stmt = f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(col_defs)});"
        pg_cur.execute(create_stmt)
        
        # Transfer data
        sl_cur.execute(f"SELECT * FROM {table}")
        rows = sl_cur.fetchall()
        
        if rows:
            placeholders = ",".join(["%s"] * len(columns))
            cols_names = ",".join([c[1] for c in columns])
            insert_stmt = f"INSERT INTO {table} ({cols_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING;"
            
            pg_cur.executemany(insert_stmt, rows)
            print(f"    Inserted {len(rows)} rows.")
            
    pg_conn.commit()
    print("Migration complete! ✅")
    
    sl_conn.close()
    pg_conn.close()

if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)
