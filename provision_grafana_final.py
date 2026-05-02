import requests
import json
import time

GRAFANA_URL = 'http://localhost:3000'
AUTH = ('admin', 'Sniper2026!')

def hard_reset_datasources():
    print("🧹 Cleaning up old data sources...")
    resp = requests.get(f'{GRAFANA_URL}/api/datasources', auth=AUTH)
    for ds in resp.json():
        print(f"🗑️ Deleting {ds['name']}...")
        requests.delete(f"{GRAFANA_URL}/api/datasources/{ds['id']}", auth=AUTH)

    print("🏗️ Creating fresh data sources...")
    # Prometheus
    ds_prom = {
        'name': 'Prometheus',
        'type': 'prometheus',
        'url': 'http://prometheus:9090',
        'access': 'proxy',
        'isDefault': True
    }
    r1 = requests.post(f'{GRAFANA_URL}/api/datasources', json=ds_prom, auth=AUTH)
    prom_uid = r1.json().get('uid')
    
    # Loki
    ds_loki = {
        'name': 'Loki',
        'type': 'loki',
        'url': 'http://loki:3100',
        'access': 'proxy'
    }
    r2 = requests.post(f'{GRAFANA_URL}/api/datasources', json=ds_loki, auth=AUTH)
    loki_uid = r2.json().get('uid')
    
    print(f"✅ DS Created: Prom={prom_uid}, Loki={loki_uid}")
    return prom_uid, loki_uid

def recreate_dashboard(prom_uid, loki_uid):
    print("📊 Recreating Dashboard...")
    dashboard = {
        'dashboard': {
            'title': 'Master Sniper Dashboard v2',
            'timezone': 'browser',
            'refresh': '5s',
            'panels': [
                {
                    'title': 'OBI Sniper',
                    'type': 'timeseries',
                    'gridPos': {'h': 8, 'w': 12, 'x': 0, 'y': 0},
                    'datasource': {'type': 'prometheus', 'uid': prom_uid},
                    'targets': [{'expr': 'algo_bot_obi_score', 'refId': 'A'}]
                },
                {
                    'title': 'Price Vitals (Micro/Mid)',
                    'type': 'timeseries',
                    'gridPos': {'h': 8, 'w': 12, 'x': 12, 'y': 0},
                    'datasource': {'type': 'prometheus', 'uid': prom_uid},
                    'targets': [
                        {'expr': 'algo_bot_microprice', 'refId': 'A', 'legendFormat': 'Micro'},
                        {'expr': 'algo_bot_mid_price', 'refId': 'B', 'legendFormat': 'Mid'}
                    ]
                },
                {
                    'title': 'System Health: CPU/RAM',
                    'type': 'timeseries',
                    'gridPos': {'h': 8, 'w': 24, 'x': 0, 'y': 8},
                    'datasource': {'type': 'prometheus', 'uid': prom_uid},
                    'targets': [
                        {'expr': 'algo_bot_cpu_percent', 'refId': 'A', 'legendFormat': 'CPU %'},
                        {'expr': 'algo_bot_ram_percent', 'refId': 'B', 'legendFormat': 'RAM %'}
                    ]
                },
                {
                    'title': 'Real-time Logs (Loki)',
                    'type': 'logs',
                    'gridPos': {'h': 10, 'w': 24, 'x': 0, 'y': 16},
                    'datasource': {'type': 'loki', 'uid': loki_uid},
                    'targets': [{'expr': '{job="algo-bot"}', 'refId': 'A'}]
                }
            ],
            'schemaVersion': 39,
            'version': 10
        },
        'overwrite': True
    }
    requests.post(f'{GRAFANA_URL}/api/dashboards/db', json=dashboard, auth=AUTH)
    print(f'✅ Dashboard Re-Sync Complete.')

if __name__ == '__main__':
    # Wait for Grafana to be ready
    time.sleep(2)
    p_uid, l_uid = hard_reset_datasources()
    recreate_dashboard(p_uid, l_uid)
