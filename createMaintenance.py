import json
import os
import requests
from zabbix_utils import ZabbixAPI
from datetime import datetime, time, timedelta
from dotenv import load_dotenv
import logging

# --------- init ------------

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

logger = logging.getLogger(__name__)

# ----------- login ------------

zabbix_url = os.getenv("ZABBIX_URL")
zabbix_token = os.getenv("ZABBIX_API_TOKEN")

if not zabbix_url:
    raise ValueError("ZABBIX_URL not set")

if not zabbix_token:
    raise ValueError("ZABBIX_API_TOKEN not set")

api = ZabbixAPI(url=zabbix_url)
api.login(token=zabbix_token)

# ---------------- params ---------------------

with open("params.json", "r") as f:
    params = json.load(f)

MAINTENANCE_NAME = params["maintenance_name"]
HOST_GROUP = params["host_group"]
WORK_START_HOUR = params["work_start_hour"]
WORK_END_HOUR = params["work_end_hour"]
DESCRIPTION = params["description"]
HOLIDAY_API_URL = params["holiday_api_url"]

now = datetime.now()

# ------------------ helper functions --------------------


def get_group_id(group_name):
    """Get the group ID for a given group name."""
    groups = api.hostgroup.get(
        filter={"name": [group_name]}, output=["groupid", "name"]
    )

    if not groups:
        raise Exception(f"Host group '{group_name}' not found")

    return groups[0]["groupid"]


def get_maintenance_id(name):
    """Get the maintenance ID for a given maintenance name."""
    maintenances = api.maintenance.get(
        filter={"name": [name]}, output=["maintenanceid", "name"]
    )

    if maintenances:
        return maintenances[0]["maintenanceid"]

    return None


def create_timeperiods():
    """Create time periods for the next week (Monday to Friday) excluding holidays."""
    curr_date = now.date()

    days_until_monday = 7 - curr_date.weekday()

    monday = curr_date + timedelta(days=days_until_monday)
    friday = monday + timedelta(days=4)

    # for edge cases where the week spans two years
    years_needed = {monday.year, friday.year}

    holiday_dates = set()

    for year in years_needed:
        url = f"{HOLIDAY_API_URL}/{year}/LT"

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            holidays = response.json()

        except requests.RequestException as e:
            logger.warning("Failed to get holidays for %s: %s", year, e)
            holidays = []

        holiday_dates.update(holiday["date"] for holiday in holidays)

    timeperiods = []

    for i in range(5):  # Monday-Friday
        day = monday + timedelta(days=i)

        # skip holidays
        if day.isoformat() in holiday_dates:
            logger.info("Skipping holiday: %s", day)
            continue

        start_dt = datetime.combine(day, time(WORK_START_HOUR, 0))
        end_dt = datetime.combine(day, time(WORK_END_HOUR, 0))

        timeperiods.append(
            {
                "timeperiod_type": 0,
                "start_date": int(start_dt.timestamp()),
                "period": int((end_dt - start_dt).total_seconds()),
            }
        )

    return timeperiods


# ----------------- logic ---------------------


def main():
    """Create or update maintenance."""
    groupid = get_group_id(HOST_GROUP)
    maintenance_id = get_maintenance_id(MAINTENANCE_NAME)
    today = int(now.timestamp())
    next_week = int((now + timedelta(days=7)).timestamp())

    timeperiods = create_timeperiods()

    if not timeperiods:
        raise RuntimeError("No maintenance time periods generated")

    if maintenance_id:

        api.maintenance.update(
            maintenanceid=maintenance_id,
            active_till=next_week,
            groups=[{"groupid": groupid}],
            timeperiods=timeperiods,
            description=DESCRIPTION,
        ).get("result", {})

    else:

        api.maintenance.create(
            name=MAINTENANCE_NAME,
            active_since=today,
            active_till=next_week,
            groups=[{"groupid": groupid}],
            timeperiods=timeperiods,
            description=DESCRIPTION,
        ).get("result", {})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Script failed: %s", e)
        raise
