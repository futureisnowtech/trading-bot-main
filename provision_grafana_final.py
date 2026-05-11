import requests
import json
import time

GRAFANA_URL = "http://grafana:3000"
AUTH = ("admin", "Sniper2026!")


def soft_sync_datasources():
    print("🔍 Checking for existing data sources...")
    resp = requests.get(f"{GRAFANA_URL}/api/datasources", auth=AUTH)
    existing = {ds["name"]: ds["uid"] for ds in resp.json()}
    
    prom_uid = existing.get("Prometheus")
    loki_uid = existing.get("Loki")

    if not prom_uid:
        print("🏗️ Creating Prometheus data source...")
        ds_prom = {
            "name": "Prometheus",
            "type": "prometheus",
            "url": "http://prometheus:9090",
            "access": "proxy",
            "isDefault": True,
        }
        r1 = requests.post(f"{GRAFANA_URL}/api/datasources", json=ds_prom, auth=AUTH)
        prom_uid = r1.json().get("uid")
    else:
        print("✅ Prometheus DS already exists.")

    if not loki_uid:
        print("🏗️ Creating Loki data source...")
        ds_loki = {
            "name": "Loki",
            "type": "loki",
            "url": "http://loki:3100",
            "access": "proxy",
        }
        r2 = requests.post(f"{GRAFANA_URL}/api/datasources", json=ds_loki, auth=AUTH)
        loki_uid = r2.json().get("uid")
    else:
        print("✅ Loki DS already exists.")

    return prom_uid, loki_uid


PINNED_DASHBOARD_UID = "d9ecf89d-5e95-4e63-b0ae-f8008debbc0f"


def recreate_dashboard(prom_uid, loki_uid):
    print("⚠️ Dashboard auto-overwrite is currently DISABLED to protect Grafana Assistant work.")
    # To re-enable, uncomment the code below.
    return
    
    # print("Recreating Calibrated Watchtower...")
    # dashboard = { ... }
    # requests.post(f"{GRAFANA_URL}/api/dashboards/db", json=dashboard, auth=AUTH)
    # print(f"✅ Dashboard Re-Sync Complete.")


if __name__ == "__main__":
    time.sleep(2)
    p_uid, l_uid = soft_sync_datasources()
    recreate_dashboard(p_uid, l_uid)
