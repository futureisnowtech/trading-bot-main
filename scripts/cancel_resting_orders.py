import logging
from execution.kalshi_broker import KalshiBroker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cancel_resting_orders")

def main():
    broker = KalshiBroker()
    broker.connect()

    logger.info("Fetching resting orders from Kalshi...")
    # Fetch orders with status='resting'
    res = broker._request("GET", "/trade-api/v2/portfolio/orders", params={"status": "resting"})
    orders = res.get("orders", [])
    
    if not orders:
        logger.info("No resting orders found on Kalshi.")
        return

    logger.info(f"Found {len(orders)} resting order(s). Canceling them now...")
    for order in orders:
        order_id = order.get("order_id")
        ticker = order.get("ticker")
        side = order.get("side") or ("BID" if order.get("yes_price_dollars") else "UNKNOWN")
        qty = order.get("remaining_count_fp") or order.get("qty") or order.get("count")
        
        logger.info(f"Canceling order {order_id} | Ticker: {ticker} | {side} x{qty}...")
        try:
            # Event-market order cancels now use the V2 event-order path.
            cancel_res = broker._request("DELETE", f"/trade-api/v2/portfolio/events/orders/{order_id}")
            logger.info(f"Successfully canceled order {order_id}: {cancel_res}")
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")

    logger.info("All operations complete.")

if __name__ == "__main__":
    main()
