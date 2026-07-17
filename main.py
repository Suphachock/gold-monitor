#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gold Price Monitor + Telegram Alert (ฮั่วเซ่งเฮง) — เวอร์ชัน GitHub Actions
--------------------------------------------------------------------------
ดึงราคาทองจาก endpoint ของฮั่วเซ่งเฮง แล้วแจ้งเตือนผ่าน Telegram
เมื่อราคา "ขยับแรง" (พุ่งขึ้น / ร่วงลง) เกินที่ตั้งไว้ — ไม่ต้อง fix ราคาเป้า

หลักการ:
  - เก็บ "ราคาอ้างอิง" (anchor) ไว้ในไฟล์ state.json
  - ทุกครั้งที่เช็ค ถ้าราคาปัจจุบันขยับจาก anchor เกิน MOVE_THRESHOLD (บาท)
    หรือเกิน MOVE_PERCENT (%) -> ส่งเตือน แล้วเลื่อน anchor มาที่ราคาปัจจุบัน
  - จึงเตือนเฉพาะตอน "ขยับแรงพอ" ไม่สแปมทุกรอบ

เวอร์ชันนี้ออกแบบให้ "รันครั้งเดียวจบ" (ไม่มี while loop) เพื่อให้ GitHub Actions
เรียกซ้ำทุก 5 นาทีผ่าน cron

ตั้งค่า Telegram TOKEN / CHAT_ID ผ่าน "GitHub Secrets" (ดู README.md)
รันในเครื่องตัวเองก็ได้ โดย set env var:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

หมายเหตุความหมายราคา:
  - "Sell" ในข้อมูล = ราคาที่ร้านขายให้เรา = ราคาที่เราจ่ายตอน "ซื้อ"
  - "Buy"  ในข้อมูล = ราคาที่ร้านรับซื้อจากเรา = ราคาที่เราได้ตอน "ขาย"
  โค้ดนี้ใช้ราคา Sell (ราคาขายออกหน้าร้าน) เป็นตัวอ้างอิงในการวัดการขยับ
"""

import os
import json
import html
import requests
from datetime import datetime

# ============ ตั้งค่าตรงนี้ ============
# อ่านจาก Environment (GitHub Secrets) — อย่าใส่ token ตรงๆ ในไฟล์ที่ push ขึ้น GitHub!
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# เลือกชนิดทองที่จะเฝ้า: "HSH" (ราคาฮั่วเซ่งเฮง), "REF" (อ้างอิง/สมาคม), "JEWEL" (รูปพรรณ)
GOLD_TYPE = os.environ.get("GOLD_TYPE", "HSH")

# ==== เกณฑ์ "ขยับแรง" ====
# เตือนเมื่อราคาขยับจากราคาอ้างอิงเกิน "จำนวนบาท" นี้ (0 = ปิดการใช้เกณฑ์บาท)
MOVE_THRESHOLD = float(os.environ.get("MOVE_THRESHOLD", 100))
# หรือเตือนเมื่อขยับเกิน "เปอร์เซ็นต์" นี้ (0 = ปิดการใช้เกณฑ์ %)
MOVE_PERCENT = float(os.environ.get("MOVE_PERCENT", 0))
# ใช้เกณฑ์ไหนก็ได้ที่ถึงก่อน (เข้าเงื่อนไขข้อใดข้อหนึ่งก็เตือน)

API_URL = "https://apicheckprice.huasengheng.com/api/values/getprice/"
STATE_FILE = "state.json"
# =====================================


def fetch_prices():
    """คืนค่า dict ของ gold type -> ข้อมูลราคา"""
    r = requests.get(API_URL, timeout=15)
    r.raise_for_status()
    data = r.json()
    return {row["GoldType"]: row for row in data}


def to_number(s):
    """แปลง '63,620' -> 63620.0"""
    if s is None:
        return None
    return float(str(s).replace(",", "").strip())


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("!! ยังไม่ได้ตั้งค่า TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (ข้ามการส่ง)")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] ส่ง Telegram ไม่สำเร็จ: {e}")


def fmt(n):
    return f"{n:,.0f}"


def load_state():
    """โหลดสถานะจากไฟล์ (ถ้าไม่มี ให้เริ่มต้นใหม่)"""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_big_move(delta, anchor):
    """เช็คว่าขยับแรงพอไหม ตามเกณฑ์บาท หรือ %"""
    moved = abs(delta)
    if MOVE_THRESHOLD > 0 and moved >= MOVE_THRESHOLD:
        return True
    if MOVE_PERCENT > 0 and anchor > 0 and (moved / anchor * 100) >= MOVE_PERCENT:
        return True
    return False


def check_once():
    """เช็คราคา 1 ครั้ง แล้วแจ้งเตือนถ้าราคาขยับแรงจากราคาอ้างอิง"""
    state = load_state()
    anchor = state.get("anchor")  # ราคาอ้างอิงล่าสุด (Sell)

    prices = fetch_prices()
    row = prices.get(GOLD_TYPE)
    if not row:
        print(f"ไม่พบข้อมูลชนิด {GOLD_TYPE}")
        return

    shop_sell = to_number(row["Sell"])   # ราคาขายออกหน้าร้าน (ตัวอ้างอิง)
    shop_buy = to_number(row["Buy"])      # ราคาร้านรับซื้อ
    updated = row.get("TimeUpdate", "")

    ts = datetime.now().strftime("%H:%M:%S")

    # ครั้งแรกที่ยังไม่มี anchor -> ตั้งเป็นราคาปัจจุบัน ไม่ต้องเตือน
    if anchor is None:
        print(f"[{ts}] ตั้งราคาอ้างอิงเริ่มต้น (Sell) = {fmt(shop_sell)}")
        save_state({
            "anchor": shop_sell,
            "last_sell": shop_sell,
            "last_buy": shop_buy,
            "last_check": datetime.now().isoformat(timespec="seconds"),
        })
        return

    delta = shop_sell - anchor
    print(f"[{ts}] {GOLD_TYPE}  Sell={fmt(shop_sell)}  Buy={fmt(shop_buy)}  "
          f"อ้างอิง={fmt(anchor)}  ขยับ={delta:+,.0f}  upd={updated}")
    print(f"     เกณฑ์: ขยับ >= {fmt(MOVE_THRESHOLD)} บาท"
          + (f" หรือ >= {MOVE_PERCENT}%" if MOVE_PERCENT > 0 else ""))

    new_anchor = anchor
    if is_big_move(delta, anchor):
        if delta > 0:
            arrow, word = "🟢▲", "พุ่งขึ้น"
        else:
            arrow, word = "🔴▼", "ร่วงลง"
        pct = (delta / anchor * 100) if anchor else 0
        send_telegram(
            f"{arrow} <b>ทอง{word}</b> ({GOLD_TYPE})\n"
            f"ราคาขายออก: <b>{fmt(shop_sell)}</b> บาท\n"
            f"เปลี่ยน: <b>{delta:+,.0f}</b> บาท ({pct:+.2f}%)\n"
            f"จากอ้างอิง {fmt(anchor)}\n"
            f"ร้านรับซื้อ: {fmt(shop_buy)}\n"
            f"อัปเดต: {html.escape(updated)}"
        )
        new_anchor = shop_sell  # เลื่อนอ้างอิงมาที่ราคาปัจจุบัน
        print(f"     -> เตือน! เลื่อนอ้างอิงเป็น {fmt(new_anchor)}")

    save_state({
        "anchor": new_anchor,
        "last_sell": shop_sell,
        "last_buy": shop_buy,
        "last_check": datetime.now().isoformat(timespec="seconds"),
    })


if __name__ == "__main__":
    try:
        check_once()
    except requests.RequestException as e:
        print(f"[{datetime.now():%H:%M:%S}] ดึงข้อมูลไม่สำเร็จ: {e}")
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] ผิดพลาด: {e}")
