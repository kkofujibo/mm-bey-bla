#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M.M小舖 戰鬥陀螺 監控腳本 v5
- 只在台灣時間 11:00 ~ 隔日 01:30 之間執行
- 每次觸發後，內部每60秒重新抓取一次、共檢查5次，約等於每分鐘檢查
- 掃描整個分類頁（自動抓全部分頁，不用手動指定商品連結）：
    https://mmtoyshop.com/category/🌀戰鬥陀螺?keyword=&page=N&sortType=&filters=&price=,
- 逐一比對每個商品卡片上的「庫存 X」數字：
    只要庫存 > 0（自動排除 0 與負數），且這是「新出現的可下單狀態」
    （之前是0或沒看過，現在變成>0），就推播 Telegram，附上該商品連結
- 每次檢查都會印出目前抓到的商品數與庫存清單，方便核對有沒有抓對
"""

import os
import re
import json
import time
import sys
from datetime import datetime, timezone, timedelta

import requests

# ------------------- 設定區 -------------------
CATEGORY_BASE = "https://mmtoyshop.com/category/%F0%9F%8C%80%E6%88%B0%E9%AC%A5%E9%99%80%E8%9E%BA"
# 等同於 https://mmtoyshop.com/category/🌀戰鬥陀螺

QUERY_SUFFIX = "keyword=&sortType=&filters=&price=,"

# 目前已知共幾頁（61件商品/3頁），程式會嘗試自動偵測，抓不到才會用這個當備援上限
FALLBACK_MAX_PAGES = 3
# 保險上限，避免偵測異常時無限抓頁
HARD_MAX_PAGES = 10

STATE_FILE = "state.json"

TAIPEI = timezone(timedelta(hours=8))
WINDOW_START = (11, 0)
WINDOW_END = (1, 30)

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
    return {"items": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_page(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/604.1"
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.text


def detect_total_pages(html: str) -> int:
    """嘗試從頁面文字找出『共 X 頁』之類的資訊，找不到就用備援值"""
    m = re.search(r'共\s*(\d+)\s*頁', html)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= HARD_MAX_PAGES:
                return n
        except ValueError:
            pass
    # 找不到就試試看用商品總數/每頁預設20~30筆換算，抓不到就用備援
    return FALLBACK_MAX_PAGES


def extract_products(html: str):
    """
    掃描頁面，抓出每個商品卡片的：庫存數字、商品名稱、連結
    做法：找到每個「庫存 X」出現的位置，往後(或往前)一小段範圍內
    找該商品的 <a href="https://mmtoyshop.com/item/...">商品名稱</a> 連結
    回傳 list of dict: [{"link":..., "title":..., "stock": int}, ...]
    """
    products = []
    seen_links_this_page = set()

    for m in re.finditer(r'庫存\s*(-?\d+)', html):
        stock = int(m.group(1))
        pos = m.end()

        # 往後找最近的商品連結+標題（在圖片/空連結之後，通常是真正帶標題文字的那個<a>）
        window = html[pos: pos + 800]
        link_match = None
        for lm in re.finditer(
            r'<a[^>]+href="(https://mmtoyshop\.com/item/[^"]+)"[^>]*>\s*([^<]{2,120}?)\s*</a>',
            window
        ):
            href, text = lm.group(1), lm.group(2).strip()
            # 過濾掉空文字或看起來像路徑的錨點文字
            if text and not text.startswith("/item"):
                link_match = (href, text)
                break

        if not link_match:
            # 往前找找看（保險，防止結構跟預期不同）
            window_before = html[max(0, pos - 800): pos]
            matches = list(re.finditer(
                r'<a[^>]+href="(https://mmtoyshop\.com/item/[^"]+)"[^>]*>\s*([^<]{2,120}?)\s*</a>',
                window_before
            ))
            for lm in reversed(matches):
                href, text = lm.group(1), lm.group(2).strip()
                if text and not text.startswith("/item"):
                    link_match = (href, text)
                    break

        if not link_match:
            continue

        href, title = link_match
        if href in seen_links_this_page:
            continue
        seen_links_this_page.add(href)

        products.append({"link": href, "title": title, "stock": stock})

    return products


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
    items = state.get("items", {})

    page1_url = f"{CATEGORY_BASE}?{QUERY_SUFFIX}&page=1"
    try:
        html_page1 = fetch_page(page1_url)
    except Exception as e:
        print(f"抓取第1頁失敗: {e}")
        return state

    total_pages = detect_total_pages(html_page1)
    print(f"偵測到共 {total_pages} 頁")

    all_products = []
    for page in range(1, total_pages + 1):
        if page == 1:
            html = html_page1
        else:
            url = f"{CATEGORY_BASE}?{QUERY_SUFFIX}&page={page}"
            try:
                html = fetch_page(url)
            except Exception as e:
                print(f"抓取第{page}頁失敗: {e}")
                continue

        products = extract_products(html)
        print(f"第{page}頁抓到 {len(products)} 個商品")
        all_products.extend(products)

    print(f"本次共抓到 {len(all_products)} 個商品，開始比對狀態...")

    triggered_any = False
    for p in all_products:
        link, title, stock = p["link"], p["title"], p["stock"]
        now_available = stock > 0

        prev_info = items.get(link)
        prev_available = prev_info["available"] if prev_info else None

        newly_available = (
            (prev_available is False and now_available is True) or
            (prev_info is None and now_available is True)
        )

        if newly_available:
            triggered_any = True
            msg = (
                "🌀 <b>M.M小舖 戰鬥陀螺 有商品可下單了！</b>\n\n"
                f"商品：{title}\n"
                f"目前庫存：{stock}\n"
                f"🔗 {link}\n\n"
                f"偵測時間：{now_taipei.strftime('%Y-%m-%d %H:%M:%S')} (台灣時間)"
            )
            send_telegram(msg)
            print(f"[通知已發送] {title}（庫存{stock}） -> {link}")

        items[link] = {"title": title, "available": now_available, "stock": stock}

    if not triggered_any:
        print(f"[{now_taipei.strftime('%H:%M:%S')}] 檢查完成，無新的可下單品項。")
        # 印出目前所有商品的庫存概況，方便核對抓取是否正確
        for p in all_products[:10]:
            print(f"  - {p['title'][:30]}｜庫存{p['stock']}")
        if len(all_products) > 10:
            print(f"  ...（其餘 {len(all_products)-10} 項省略）")

    state["items"] = items
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
            save_state(state)
        except Exception as e:
            print(f"第 {i+1} 次檢查發生錯誤: {e}", file=sys.stderr)

        if i < LOOP_TIMES - 1:
            if not in_watch_window(datetime.now(TAIPEI)):
                print("已超出監控時段，提前結束本次任務。")
                break
            time.sleep(LOOP_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"執行發生錯誤: {e}", file=sys.stderr)
