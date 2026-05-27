"""
monitoring/irm_reporter.py — Active incident reporter for Grafana IRM.

Pushes critical system halts and violations directly to the Grafana Incident API,
attaching version info, system state, and failure context.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any, Dict, List, Optional

import requests

import config

logger = logging.getLogger(__name__)

def _get_git_commit() -> str:
    """Return the current git commit hash or 'unknown'."""
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], 
                                       stderr=subprocess.STDOUT).decode().strip()
    except Exception:
        return "unknown"

def create_irm_incident(
    title: str,
    severity: str = "critical",
    description: str = "",
    labels: Optional[List[str]] = None,
    extra_details: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Create a new incident in Grafana IRM.
    Returns the incidentID if successful, else None.
    """
    if not config.GRAFANA_INCIDENT_ENABLED:
        return None

    if not config.GRAFANA_URL or not config.GRAFANA_TOKEN:
        logger.warning("[irm] Integration enabled but URL or TOKEN is missing")
        return None

    # Standard endpoint for Grafana Incident RPC
    url = f"{config.GRAFANA_URL.rstrip('/')}/api/plugins/grafana-irm-app/resources/api/v1/IncidentsService.CreateIncident"
    
    headers = {
        "Authorization": f"Bearer {config.GRAFANA_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json"
    }

    # Enrich description with system metadata
    from main import VERSION
    commit = _get_git_commit()
    sa_id = getattr(config, "GRAFANA_SERVICE_ACCOUNT_ID", "unknown")
    
    full_description = (
        f"**System Version:** {VERSION} ({commit})\n"
        f"**Reporter ID:** {sa_id}\n"
        f"**Source:** algo-trading-bot\n\n"
        f"{description}\n"
    )
    
    if extra_details:
        full_description += "\n**Extra Context:**\n```json\n"
        full_description += json.dumps(extra_details, indent=2)
        full_description += "\n```"

    # Standard labels
    final_labels = ["service:algo-bot", f"version:{VERSION}"]
    if labels:
        final_labels.extend(labels)

    payload = {
        "title": title,
        "severity": severity.lower(),
        "description": full_description,
        "labels": final_labels,
        "status": "active"
    }

    try:
        # Timeout quickly to avoid blocking the bot's execution thread
        resp = requests.post(url, headers=headers, json=payload, timeout=5)
        
        if resp.status_code == 200:
            data = resp.json()
            incident_id = data.get("incident", {}).get("incidentID")
            logger.info(f"[irm] Incident created successfully: {incident_id}")
            return str(incident_id)
        else:
            logger.error(f"[irm] Failed to create incident ({resp.status_code}): {resp.text}")
            return None
    except Exception as e:
        logger.error(f"[irm] Exception during incident creation: {e}")
        return None
