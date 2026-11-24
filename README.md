# ElderCare Insight: mini data warehouse and analytics dashboard

Team project (group 05, three students) for the Data Mining course at Kasetsart University, 2026.

A data warehouse and two Streamlit dashboards for skilled nursing facility operators, built from US CMS open data covering 2019 to 2026. CMS publishes only the current state of each facility, and the history is spread over monthly ZIP snapshots instead of one series. This project joins them so that expansion and staffing decisions can rest on time series.

- **Five sources:** CMS archived snapshots (ZIP), the CMS Provider Data REST API (JSON), SNF VBP facility performance (CSV), the Census ACS API (nested JSON) and CMS lookup tables.
- **Warehouse:** a star schema in DuckDB with two fact tables (periodic snapshot and transaction) and six dimensions; `Dim_Facility` is a type 2 slowly changing dimension.
- **Answers:** eight business questions, ten measures, and handling for eight kinds of data-quality problems.
- **Checks:** dimension and fact checks (22 and 22 passing, including reconciliation against figures CMS computes itself) and a dashboard check (56 passing).

**Stack:** Python, pandas, DuckDB, Streamlit, XeLaTeX.

Run instructions, the folder guide and the design document are in [Group05_ElderCareDW/README.md](Group05_ElderCareDW/README.md) (in Thai). The assignment brief is not included because it belongs to the course.

**Data:** Centers for Medicare & Medicaid Services, Provider Data Catalog (Nursing Homes); U.S. Census Bureau, American Community Survey (table B01001).
