#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M.M小舖 戰鬥陀螺 監控腳本 v2
- 只在台灣時間 11:00 ~ 隔日 01:30 之間執行
- 每次被 GitHub Actions 觸發（每5分鐘一次）後，內部自己每60秒重新抓取一次、共檢查5次，
  等於在監控時段內達到「每分鐘檢查」的效果，且完全在GitHub Actions免費額度內（public repo不限額度）
- 逐一比對每個商品的狀態標籤（例如『補貨中』『缺貨中』），
  只要有商品從「補貨中/缺貨中」變成「非補貨中」（現貨/可下單），立刻推播 Telegram，
  訊息附上該商品的完整連結，iPhone上點開Telegram會直接用內建網頁預覽打開，方便快速下單。
"""

import os
import re
import json
import time
import sys
from datetime import datetime, timezone, timedelta

import requests

# ------------------- 設定區 -------------------
TARGET_URL = "https://mmtoyshop.com/category/%F0%9F%8C%80%E6%88%B0%E9%AC%A5%E9%99%80%E8%9E%BA"
# 等同於 https://mmtoyshop.com/category/🌀戰鬥陀螺

STATE_FILE = "state.json"

TAIPEI = timezone(timedelta(hours=8))
WINDOW_START = (11, 0)   # 11:00
WINDOW_END = (1, 30)     # 隔日 01:30

# 視為「不可下單」的狀態關鍵字，可自行增減
UNAVAILABLE_STATUSES = ["補貨中", "缺貨中", "缺貨", "已售完", "補貨"]

# 只想盯特定系列/型號時，填入關鍵字（例如 ["UX-19", "限定"]）；留空代表全部商品都盯
WATCH_KEYWORDS = []

# 每次觸發後，內部檢查幾次、間隔幾秒（5次 x 60秒 = 涵蓋整個5分鐘排程區間）
LOOP_TIMES = 5
LOOP_INTERVAL_SEC = 60
# ------------------------------------------------


def in_watch_window(now_taipei: datetime) -> bool:
    h, m = now_taipei.hour, now_taipei.minute
    start_minutes = WINDOW_START[0] * 60 + WINDOW_START[1]
    end_minutes = WINDOW_END[0] * 60 + WINDOW_END[1]
    now_minutes = h * 60 + m
    if now_minutes >= start_minutes:
        return True
    if now_minutes <= end_minutes:
        return True
    return False


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"items": {}}  # { link: {"title": ..., "status": ..., "available": bool} }


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_page():
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/604.1"
    }
    resp = requests.get(TARGET_URL, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.text


def extract_items(html: str):
    """
    解析商品標題與連結，例如：
    [【M.M小舖】『預購』 6月 BANDAI ...](https://mmtoyshop.com/item/xxxxx)
    回傳 dict: { link: {"title": 標題, "status": 狀態標籤或None} }
    """
    items = {}
    pattern = re.findall(
        r'\[([^\[\]]{4,120})\]\((https://mmtoyshop\.com/item/[^\)\s]+)\)',
        html
    )
    for title, link in pattern:
        title = title.strip()
        if not title or "http" in title:
            continue
        status_match = re.search(r'『([^『』]{1,10})』', title)
        status = status_match.group(1) if status_match else None
        items[link] = {"title": title, "status": status}
    return items


def is_available(status):
    if status is None:
        return True
    return not any(bad in status for bad in UNAVAILABLE_STATUSES)


def matches_watch_keywords(title):
    if not WATCH_KEYWORDS:
        return True
    return any(k in title for k in WATCH_KEYWORDS)


def send_telegram(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，略過推播。")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print(f"Telegram 發送失敗: {r.status_code} {r.text}")
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")


def check_once(state):
    now_taipei = datetime.now(TAIPEI)
    html = fetch_page()
    current_items = extract_items(html)

    prev_items = state.get("items", {})
    triggered_any = False

    for link, info in current_items.items():
        title = info["title"]
        status = info["status"]

        if not matches_watch_keywords(title):
            continue

        now_available = is_available(status)
        prev_info = prev_items.get(link)
        prev_available = prev_info["available"] if prev_info else None

        # 觸發條件：以前是「不可下單」(False)，現在變成「可下單」(True)
        # 或是全新出現的商品，且一出現就是可下單狀態
        newly_available = (
            (prev_available is False and now_available is True) or
            (prev_info is None and now_available is True)
        )

        if newly_available:
            triggered_any = True
            status_text = f"『{status}』" if status else "（無特別標記，判斷為可下單）"
            msg = (
                "🌀 <b>M.M小舖 戰鬥陀螺 有新品項可下單！</b>\n\n"
                f"商品：{title}\n"
                f"狀態：{status_text}\n"
                f"🔗 {link}\n\n"
                f"偵測時間：{now_taipei.strftime('%Y-%m-%d %H:%M:%S')} (台灣時間)"
            )
            send_telegram(msg)
            print(f"[通知] {title} -> {link}")

        current_items[link]["available"] = now_available

    state["items"] = current_items
    if not triggered_any:
        print(f"[{now_taipei.strftime('%H:%M:%S')}] 檢查完成，無新的可下單品項。")

    return state


def main():
    now_taipei = datetime.now(TAIPEI)
    if not in_watch_window(now_taipei):
        print(f"目前台灣時間 {now_taipei.strftime('%H:%M')}，不在監控時段(11:00~01:30)內，略過本次任務。")
        return

    state = load_state()

    for i in range(LOOP_TIMES):
        try:
            state = check_once(state)
            save_state(state)  # 每次檢查後都存檔，避免中途失敗遺失進度
        except Exception as e:
            print(f"第 {i+1} 次檢查發生錯誤: {e}", file=sys.stderr)

        # 最後一次不用再等待
        if i < LOOP_TIMES - 1:
            # 若已超出監控時段（例如剛好跨過01:30），提前結束
            if not in_watch_window(datetime.now(TAIPEI)):
                print("已超出監控時段，提前結束本次任務。")
                break
            time.sleep(LOOP_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"執行發生錯誤: {e}", file=sys.stderr)
