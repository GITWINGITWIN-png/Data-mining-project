# ElderCare Insight — Mini Data Warehouse & Analytics Dashboard

**Group Assignment #1 — Data Mining / Data Warehouse**
กลุ่มที่ 01 · สมาชิก: _(ยังไม่เติม)_ , _(ยังไม่เติม)_ , _(ยังไม่เติม)_

คลังข้อมูลและแดชบอร์ดสำหรับธุรกิจสถานดูแลผู้สูงอายุ (Skilled Nursing Facility)
จากข้อมูลเปิดของ CMS สหรัฐอเมริกา ปี 2562–2569

> **สถานะ:** ตอบครบทั้ง 8 คำถามทางธุรกิจ และแดชบอร์ดใช้งานได้แล้ว · เหลือรายงาน PDF และวิดีโอ
> · คำตอบทั้งแปดข้อ → [`06_Report/BQ_answers.md`](06_Report/BQ_answers.md)
> · เอกสารออกแบบฉบับเต็ม → [`06_Report/eldercare_dw_design.pdf`](06_Report/eldercare_dw_design.pdf)
> · ความคืบหน้าและสิ่งที่เหลือ → [`../PROGRESS.md`](../PROGRESS.md)

## เปิดแดชบอร์ด

```bash
cd 02_ETL   && python -m pip install -r requirements.txt
python run_dims.py && python run_facts.py && python population.py   # สร้างคลังข้อมูล
cd ../04_Dashboard && python -m pip install -r requirements.txt
streamlit run app.py
```

---

## โครงสร้างโฟลเดอร์

| โฟลเดอร์ | เนื้อหา | สถานะ |
|---|---|---|
| `01_Raw_Data/` | ข้อมูลดิบจาก CMS (ไม่เก็บใน git — ดู [README](01_Raw_Data/README.md)) | มีคำสั่งโหลดแล้ว |
| `02_ETL/` | สคริปต์ Extract → Clean → Transform → Integrate → Load ([README](02_ETL/README.md)) | เสร็จ (Dimension + Fact) |
| `03_Data_Warehouse/` | สคีมาพร้อมข้อบังคับ + ชั้นความหมาย ([README](03_Data_Warehouse/README.md)) · ไฟล์ DuckDB ไม่เก็บใน git | 8 ตาราง + 10 วิว |
| `04_Dashboard/` | แอป Streamlit **สองชุด** + โน้ตบุ๊ก + ภาพหน้าจอ ([README](04_Dashboard/README.md)) | ตอบ BQ1–BQ8 ครบทั้งสองชุด |
| `05_AI_Usage_Log/` | บันทึกการใช้ Generative AI ([README](05_AI_Usage_Log/README.md)) | 15 รายการ (เกณฑ์ขั้นต่ำ 5) |
| `06_Report/` | เอกสารออกแบบ (LaTeX) · คำตอบ 8 ข้อ · รายงานฉบับส่ง | เอกสารออกแบบ + คำตอบเสร็จ |

## สรุปโครงงาน

**ปัญหาทางธุรกิจ** — ผู้ประกอบการเครือสถานดูแลผู้สูงอายุตัดสินใจขยายตลาดและลงทุน
ด้านพนักงานโดยไม่มีข้อมูลอนุกรมเวลารองรับ เพราะ CMS เปิดเผยเฉพาะสถานะปัจจุบัน
ส่วนข้อมูลย้อนหลังกระจายอยู่ในไฟล์ ZIP รายเดือนที่ไม่มีใครต่อให้เป็นชุดเดียว

**แหล่งข้อมูล 5 แหล่ง** — CMS Archived Snapshots (ZIP หลายช่วงเวลา) · CMS Provider Data
REST API (JSON) · SNF VBP Facility Performance (CSV รายปีงบประมาณ) · Census ACS API
(nested JSON) · ตาราง lookup ของ CMS

**คลังข้อมูล** — Star Schema แบบ 2 Fact (Periodic Snapshot + Transaction) และ
6 Dimension โดย `Dim_Facility` เป็น SCD ชนิดที่ 2

**เครื่องมือ** — Python + pandas (ETL) · DuckDB (คลังข้อมูล) · Streamlit (แดชบอร์ด) · XeLaTeX (รายงาน)

รายละเอียดทั้งหมด — คำถามทางธุรกิจ 8 ข้อ, measure 10 ตัว, ปัญหาคุณภาพข้อมูล 8 ประเภท,
การประกาศ Grain และผัง Star Schema — อยู่ในเอกสารออกแบบ

## วิธีรันทั้งโครงงานตั้งแต่ต้น

เริ่มจากโฟลเดอร์เปล่าได้เลย ข้อมูลดิบไม่ได้เก็บใน git แต่โหลดใหม่ได้จาก CMS เสมอ

```bash
python -m pip install -r requirements.txt      # ติดตั้งครั้งเดียวครบทุกส่วน

cd 02_ETL
python fetch_snapshots.py --dates 2019-01-17 2026-06-24 2026-07-29 2026-08-06
python run_dims.py && python run_facts.py      # ต้องรันคู่กันเสมอ
python verify_dims.py && python verify_facts.py

cd ../03_Data_Warehouse
python build_warehouse.py --report             # ใส่ข้อบังคับและสร้างวิว

cd ../04_Dashboard
python verify_dashboard.py                     # ตรวจก่อน (56 ข้อ)
streamlit run app.py                           # เปิดที่ http://localhost:8501
```

ขั้นตอนโหลดข้อมูลใช้เวลาสักพักเพราะงวด `2026-07-29` มีขนาด 622 MB
งวดที่โหลดแล้วจะถูกข้ามในการรันครั้งถัดไป

| ชุดตรวจ | ตรวจอะไร | ผลล่าสุด |
|---|---|---|
| `02_ETL/verify_dims.py` | dimension ทั้งหก รวม SCD2 | 22 ผ่าน 0 ตก |
| `02_ETL/verify_facts.py` | fact ทั้งสอง รวมการกระทบยอดกับตัวเลขที่ CMS คำนวณเอง | 22 ผ่าน 0 ตก |
| `04_Dashboard/verify_dashboard.py` | measure ตรงกับคลัง และครบตามเกณฑ์โจทย์ | 56 ผ่าน 0 ตก |

## การสร้างเอกสารรายงานใหม่

ต้องใช้ **XeLaTeX** และฟอนต์ตระกูล **TLWG** (Laksaman, Garuda) — ฟอนต์ละตินล้วนจะทำให้
ข้อความไทยหาย

```bash
# Fedora
sudo dnf install texlive-scheme-medium texlive-xetex latexmk thai-scalable-fonts-common
# Debian/Ubuntu
sudo apt install texlive-xetex texlive-latex-extra latexmk fonts-tlwg-laksaman fonts-tlwg-garuda

cd 06_Report && latexmk -xelatex -outdir=build eldercare_dw_design.tex && cp build/eldercare_dw_design.pdf .
```

## แหล่งที่มาของข้อมูล

Centers for Medicare & Medicaid Services (CMS), *Provider Data Catalog — Nursing Homes*.
<https://data.cms.gov/provider-data/archived-data/nursing-homes>
ข้อมูลสาธารณะของรัฐบาลสหรัฐอเมริกา ใช้ได้เสรีโดยต้องอ้างอิงแหล่งที่มา

ข้อมูลประชากรจาก U.S. Census Bureau, *American Community Survey* (ตาราง B01001)

## หมายเหตุการส่งงาน

- โจทย์กำหนดให้โฟลเดอร์ Google Drive มี `README.pdf` หรือ `README.txt`
  ก่อนส่งให้ export ไฟล์นี้เป็นหนึ่งในสองรูปแบบนั้น
- ต้องเปิดสิทธิ์โฟลเดอร์เป็น **Anyone with the link can view**
- ยังต้องเติม: หมายเลขกลุ่มและชื่อสมาชิกสามคน (ทั้งในไฟล์นี้และหน้าปกเอกสารออกแบบ)
