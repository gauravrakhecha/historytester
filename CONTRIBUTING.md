# Contributing to QuantDesk

Thank you for contributing to **QuantDesk**! All community contributions help keep quantitative research tools open, reproducible, and accessible.

## License & Contribution Terms

QuantDesk is released under the **GNU Affero General Public License v3.0 (`AGPL-3.0`)** (see [LICENSE](LICENSE)) and follows a dual-licensing stewardship model:

1. **Everything Stays Open Source (`AGPL-3.0`):**
   - Any modified version of QuantDesk that you distribute **or** make available over a network/SaaS/cloud service must release its complete corresponding source code under **AGPL-3.0**.
   - Commercial closed-source use without contributing modifications back under `AGPL-3.0` is not permitted under the open-source license; contact the maintainer for a separate commercial agreement.
2. **Contributor Agreement (Inbound = Outbound + Dual-Licensing Stewardship):**
   - By submitting a Pull Request or patch to this repository, you certify that you authored the work (or hold rights to submit it), and you grant both:
     - the community rights to use, modify, and redistribute your contribution under **AGPL-3.0-or-later**, and
     - the project maintainer (`gauravrakhecha`) a non-exclusive license to include your contribution in future open-source (`AGPL-3.0`) and commercially licensed distributions.
   - If your PR includes third-party assets, verify license compatibility with `AGPL-3.0` and preserve attribution notices.

## Development Workflow

1. Create an isolated environment (`install.bat` or `python -m venv .venv && pip install -r requirements.txt -r requirements-dev.txt`).
2. Never commit `.env`, `*.db`, `.venv/`, caches, downloaded datasets (`data/`), or broker credentials.
3. Keep backend modules (`quantdesk/strategy/`, `backtest/`, `risk/`, `costs/`, `data/`, `live/`, `viz/`) free of Streamlit imports so CLI tools, workers, and headless endpoints stay decoupled.
4. Run verification before opening a PR:
   ```powershell
   python -m compileall -q quantdesk app_main.py
   python -m pytest tests/ --ignore=tests/test_downloader.py -p no:cacheprovider -q
   python tests/verify_app.py
   python tests/verify_features.py
   ```
