"""Data Management — Backup & Recovery page (upstox_app.py:1341-1378)."""
from __future__ import annotations

import datetime
import io
import os
import zipfile

import streamlit as st

from quantdesk.config import DB_FILE
from quantdesk.data.recovery import rebuild_db_from_folder


def render(ctx):
    st.title("🛠️ Backup & Recovery")
    st.subheader("1. 📦 Manual Backup (ZIP)")
    st.markdown("Create a complete ZIP backup of your **Database** + **Local Data Folder**.")
    backup_root = st.text_input("📁 Root Data Folder to include (Optional)", value=ctx.default_parquet_dir)

    if st.button("🗜️ Create Full Backup"):
        with st.spinner("Zipping files... This may take a while."):
            try:
                mem_zip = io.BytesIO()
                with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                    if os.path.exists(DB_FILE):
                        zf.write(DB_FILE, arcname=os.path.basename(DB_FILE))
                    if backup_root and os.path.exists(backup_root):
                        for root, _dirs, files in os.walk(backup_root):
                            for file in files:
                                if file.endswith((".csv", ".parquet")):
                                    file_path = os.path.join(root, file)
                                    rel_path = os.path.relpath(file_path, os.path.dirname(backup_root))
                                    zf.write(file_path, arcname=rel_path)
                mem_zip.seek(0)
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
                st.success("✅ Backup created successfully!")
                st.download_button(
                    label="⬇️ Download Backup ZIP",
                    data=mem_zip,
                    file_name=f"Data_Backup_{timestamp}.zip",
                    mime="application/zip",
                )
            except Exception as e:
                st.error(f"Backup failed: {e}")

    st.divider()
    st.subheader("2. 🔄 Restore Database")
    st.markdown("Rebuild your database index by scanning your local data folder.")
    restore_path = st.text_input("📁 Select Data Root Folder to Scan", value=ctx.default_parquet_dir, key="restore_path_input")
    if st.button("🔄 Start Recovery Scan"):
        if restore_path and os.path.exists(restore_path):
            with st.spinner("Scanning files..."):
                added, errors = rebuild_db_from_folder(restore_path)
            st.success(f"✅ Restored {added} records.")
            if errors:
                st.error(f"⚠️ {errors} errors.")
        else:
            st.error("Invalid path.")
