"""
quantdesk.data.recovery — rebuild the download log by scanning a data folder.

Verbatim port of the original ``rebuild_db_from_folder`` (upstox_app.py:176-206): walks a
root folder, parses ``{symbol}_{interval}_{start}_{end}.csv|parquet`` filenames and inserts
any not-yet-logged files back into ``downloads_log``. Used by the Data Management page.
"""
from __future__ import annotations

import datetime
import os

from quantdesk.core.db import connect
from quantdesk.core.logging import get_logger

log = get_logger("data.recovery")


def rebuild_db_from_folder(root_folder: str):
    """Scan ``root_folder`` for csv/parquet data files and re-index them. Returns (added, errors)."""
    restored_count = 0
    errors = 0
    try:
        with connect() as conn:
            c = conn.cursor()
            for dirpath, _dirnames, filenames in os.walk(root_folder):
                for file in filenames:
                    if file.endswith((".csv", ".parquet")):
                        try:
                            full_path = os.path.join(dirpath, file)
                            c.execute(
                                "SELECT id FROM downloads_log WHERE file_path = ?", (full_path,)
                            )
                            if c.fetchone():
                                continue
                            name_no_ext = file.replace(".csv", "").replace(".parquet", "")
                            parts = name_no_ext.split("_")
                            # Two filename formats exist in the app:
                            #   single:  {symbol}_{interval}_{start}_{end}
                            #   master:  {symbol}_{interval}_{start}_to_{end}
                            # Handle the optional literal "to" token between the dates.
                            if len(parts) >= 5 and parts[-2] == "to":
                                date_end = parts[-1]
                                date_start = parts[-3]
                                interval_raw = parts[-4]
                                symbol = "_".join(parts[:-4])
                            elif len(parts) >= 4:
                                date_end = parts[-1]
                                date_start = parts[-2]
                                interval_raw = parts[-3]
                                symbol = "_".join(parts[:-3])
                            else:
                                continue
                            c.execute(
                                """INSERT INTO downloads_log
                                   (broker, symbol, instrument_key, interval, start_date,
                                    end_date, file_path, downloaded_at)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    "Unknown", symbol, "RESTORED", interval_raw,
                                    date_start, date_end, full_path, datetime.datetime.now(),
                                ),
                            )
                            restored_count += 1
                        except Exception:
                            errors += 1
    except Exception as e:  # noqa: BLE001
        log.error("DB Restore Error: %s", e)
    return restored_count, errors
