[README.md](https://github.com/user-attachments/files/31049521/README.md)
# M.M小舖 戰鬥陀螺 監控機器人 v2（GitHub Actions + Telegram）

監控頁面：https://mmtoyshop.com/category/🌀戰鬥陀螺
監控時段：台灣時間 每天 11:00 ～ 隔日 01:30
檢查頻率：時段內每 1 分鐘檢查一次
觸發條件：任一商品狀態從「補貨中／缺貨中」變成「非補貨中」（可下單）時，立刻推播 Telegram，並附上該商品完整連結

詳細逐步教學請看對話中的說明，這裡只列檔案結構：
- monitor.py：主程式
- .github/workflows/watch.yml：排程設定
- state.json：程式自動產生、自動更新，不用手動建立
