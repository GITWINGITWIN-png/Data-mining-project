# ElderCare Insight — Mini Data Warehouse & Analytics Dashboard

**Group Assignment #1 — Data Mining / Data Warehouse**
กลุ่มที่ 01 · สมาชิก: _(ยังไม่เติม)_ , _(ยังไม่เติม)_ , _(ยังไม่เติม)_

คลังข้อมูลและแดชบอร์ดสำหรับธุรกิจสถานดูแลผู้สูงอายุ (Skilled Nursing Facility)
จากข้อมูลเปิดของ CMS สหรัฐอเมริกา ปี 2562–2569

> **สถานะ:** ออกแบบเสร็จแล้ว · ETL ครบทั้ง 6 Dimension และ 2 Fact ตรวจผ่านแล้ว · ยังไม่ทำแดชบอร์ด
> เอกสารออกแบบฉบับเต็มอยู่ที่ [`06_Report/eldercare_dw_design.pdf`](06_Report/eldercare_dw_design.pdf)

---

## โครงสร้างโฟลเดอร์

| โฟลเดอร์ | เนื้อหา | สถานะ |
|---|---|---|
| `01_Raw_Data/` | ข้อมูลดิบจาก CMS (ไม่เก็บใน git — ดู [README](01_Raw_Data/README.md)) | มีคำสั่งโหลดแล้ว |
| `02_ETL/` | สคริปต์ Extract → Clean → Transform → Integrate → Load ([README](02_ETL/README.md)) | เสร็จ (Dimension + Fact) |
| `03_Data_Warehouse/` | ไฟล์ฐานข้อมูล DuckDB (สร้างจาก `02_ETL/` ไม่เก็บใน git) | มี 6 Dimension + 2 Fact |
| `04_Dashboard/` | แอป Streamlit | ว่าง |
| `05_AI_Usage_Log/` | บันทึกการใช้ Generative AI ([README](05_AI_Usage_Log/README.md)) | เริ่มบันทึกแล้ว |
| `06_Report/` | เอกสารออกแบบ (LaTeX) และรายงานฉบับส่ง | เอกสารออกแบบเสร็จ |

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
