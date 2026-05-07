import os
from dotenv import load_dotenv

load_dotenv()

# ── Database ───────────────────────────────────────────────────────────────────
DATABASE_URL: str = os.environ["DATABASE_URL"]  # postgres://user:pass@host/db

# ── Sanctions sources ──────────────────────────────────────────────────────────
OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
OFAC_CONS_URL = "https://www.treasury.gov/ofac/downloads/consolidated/consolidated.xml"

UN_LIST_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"

EU_LIST_URL = (
    "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content"
    "?token=dG9rZW4tMjAxNw"
)

# ── Scheduler ─────────────────────────────────────────────────────────────────
RUN_HOUR_UTC = int(os.getenv("RUN_HOUR_UTC", "18"))
RUN_MINUTE_UTC = int(os.getenv("RUN_MINUTE_UTC", "0"))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
