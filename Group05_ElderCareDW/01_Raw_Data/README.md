# ข้อมูลดิบ — ไม่ได้เก็บใน git

ไฟล์ในโฟลเดอร์นี้ถูก ignore ไว้ (รวมกัน ~69 MB และจะโตเป็น ~1 GB
เมื่อโหลดครบทุกไตรมาส) โหลดใหม่ได้เสมอด้วยขั้นตอนด้านล่าง

## แหล่งที่มา
CMS Provider Data Catalog — Nursing Homes (ข้อมูลสาธารณะ ใช้ได้เสรี)
https://data.cms.gov/provider-data/archived-data/nursing-homes

## 1. ดูรายการ snapshot ทั้งหมด (88 รายการ ปี 2019–2026)
```bash
curl -sS "https://data.cms.gov/provider-data/api/1/archive/aggregate/theme/nursing-homes/relative"
```
คืน JSON ที่มีทั้ง URL ขนาด และวันที่ของทุก snapshot

## 2. โหลด snapshot หนึ่งเดือน (ตัวอย่าง มิ.ย. 2026)
```bash
mkdir -p cms_snapshots && cd cms_snapshots
curl -sS -o nursing-homes_2026-06-24.zip \
  "https://data.cms.gov/provider-data/sites/default/files/dataset-archives/theme/nursing-homes/nursing-homes_2026-06-24.zip"
unzip -l nursing-homes_2026-06-24.zip   # ดูรายชื่อไฟล์ 20 ไฟล์
```

## 3. ไฟล์ที่โปรเจกต์นี้ใช้จริง (6 จาก 20 ไฟล์)
| ไฟล์ | แถว × คอลัมน์ | ใช้ทำอะไร |
|---|---|---|
| `NH_ProviderInfo_*.csv` | 14,695 × 99 | Fact 1 + Dimension ทุกตัว |
| `NH_Penalties_*.csv` | 16,180 × 14 | Fact 2 |
| `NH_SurveySummary_*.csv` | 43,952 × 41 | สำรองไว้สำหรับ Fact 3 |
| `NH_CitationDescriptions_*.csv` | 643 × 5 | lookup รหัสข้อบกพร่อง |
| `NH_StateUSAverages_*.csv` | 54 × 51 | ค่าอ้างอิงรายรัฐ |
| `FY_2026_SNF_VBP_Facility_Performance.csv` | 13,900 × 49 | ตัวคูณเงินจูงใจ |

**อย่าคลาย `NH_HealthCitations` (165 MB) ถ้าไม่จำเป็น**

## หมายเหตุตอนอ่านไฟล์
- CSV **ไม่ใช่ UTF-8 ล้วน** → ใช้ `encoding='latin-1'`
- อ่านทุกคอลัมน์เป็น `dtype=str` ก่อนเสมอ — CCN มีศูนย์นำหน้า (`015009`)
  ถ้าอ่านเป็นตัวเลขจะกลายเป็น `15009` แล้ว join ไม่ติดแบบเงียบ ๆ
- แถวทดสอบ: CCN `015009` มี 57 เตียง ผู้พัก 51.6 คน → occupancy 90.5%

> เมื่อเขียนสคริปต์ ETL เสร็จแล้ว ให้แทนที่ขั้นตอน 1–2 ด้วย
> `python 02_ETL/fetch_snapshots.py`
