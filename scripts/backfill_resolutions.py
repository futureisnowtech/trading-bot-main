#!/usr/bin/env python3
"""
v20 Backfill script to populate new columns in the forecast_resolutions table.
"""
import sys
import os
import sqlite3
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_resolutions")

# Root path alignment
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from config import DB_PATH
from forecast.db import init_forecast_db

def backfill_resolutions(db_path: str = DB_PATH) -> int:
    logger.info("Initializing database migrations...")
    init_forecast_db(db_path=db_path)
    
    logger.info(f"Opening connection to {db_path}...")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        # Check if resolutions exist with null basis_quality
        rows = conn.execute(
            """
            SELECT id, contract_id, basis_quality 
            FROM forecast_resolutions 
            WHERE basis_quality IS NULL
            """
        ).fetchall()
        
        if not rows:
            logger.info("No resolutions require backfilling.")
            return 0
            
        logger.info(f"Found {len(rows)} resolution records to backfill.")
        updated_count = 0
        for row in rows:
            # For historical resolutions, we stamp default fallback values
            conn.execute(
                """
                UPDATE forecast_resolutions
                SET q_gfs = 0.50,
                    q_ecmwf = 0.50,
                    q_hrrr = NULL,
                    q_hat = 0.50,
                    sigma_post = 0.0,
                    lambda_scaler = 1.0,
                    fee_rate_applied = 0.01,
                    basis_quality = 'CONFIRMED'
                WHERE id = ?
                """,
                (row["id"],)
            )
            updated_count += 1
            
        conn.commit()
        logger.info(f"Successfully backfilled {updated_count} resolution records.")
        return updated_count

if __name__ == "__main__":
    backfill_resolutions()
