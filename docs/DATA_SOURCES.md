# Free public data sources

Checked from a script on 2026-11-14. "Works" means it answered an automated request. Only
sources that work from a script can feed the automated videos.

| Source | What it has | Access | Status (2026-11-14) | Used for |
|---|---|---|---|---|
| World Bank API (`api.worldbank.org/v2`) | GDP, income, literacy, population, life expectancy and about 1,400 more indicators for every country | free, no key | works; per-country requests timed out for hours on 11-14, bulk requests are fast | comparison, rankings (live), weekly snapshot |
| Our World in Data grapher CSVs (`ourworldindata.org/grapher/<chart>.csv`) | life expectancy, population, GDP per capita (PPP), hundreds of charts | free, no key, CC BY | works (`life-expectancy`, `population`, `gdp-per-capita-worldbank`); `literacy-rate-adult` returned 404 | backup in `save_stats_snapshot.py` |
| UN SDG API (`unstats.un.org/sdgapi`) | Sustainable Development Goal indicators by country | free, no key | works | not used yet |
| Wikipedia API | article text for fact checks | free, no key | works | `verify_india_facts.py`, rankings refresh |
| IMF DataMapper API | GDP, inflation, debt forecasts | free | **blocked** (403 Access Denied to scripts) | - |
| RBI Database on Indian Economy (`data.rbi.org.in/DBIE`) | India: inflation, GDP, banking, forex, state-wise data | free on the website | **blocked to scripts** (HTTP 418) | - |
| RBI Handbook of Statistics on Indian Economy (`rbi.org.in`, annual) | state-wise and national tables as Excel files | free, website | page loads; tables are Excel downloads, no API | a candidate for a curated India data file (like `config/rankings_static.json`), refreshed yearly |
| data.gov.in API | Indian government datasets | free, needs a free API key | **timed out** from here (may block traffic from outside India) | - |
| MOSPI eSankhyiki | India national statistics (GDP, CPI, PLFS) | free | **timed out** from here | - |

## Saved locally

- `config/country_stats_snapshot.json`: every country's 5 comparison figures, refreshed weekly
  by `.github/workflows/refresh-stats-snapshot.yml`. It's used when the live World Bank request fails.
- `config/country_stats_cache.json`: per-country results from earlier runs.
- `config/india_places.json`: India place facts, each checked against Wikipedia.

## For an "India by the numbers" series

The Indian government sources (RBI, data.gov.in, MOSPI) don't answer automated requests
from outside India. The practical route is a curated file: take each table from the RBI
Handbook (yearly Excel), check it against the published PDF, and commit it with source and
edition, the same way `config/rankings_static.json` is kept. India-wide figures that the World
Bank publishes (population, GDP, literacy, life expectancy) can come from the API directly.
