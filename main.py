import os
import requests
import yfinance as yf

def send_discord_msg(msg, webhook_url):
    payload = {"content": msg}
    requests.post(webhook_url, json=payload)

def check_006208_signal():
    # 抓取 006208 近半年股價
    ticker = yf.Ticker("006208.TW")
    df = ticker.history(period="6mo")
    
    if df.empty:
        return "⚠️ 找不到 006208 股價資料"
        
    # 計算 60日均線(季線)
    df['60MA'] = df['Close'].rolling(window=60).mean()
    
    latest_close = round(df['Close'].iloc[-1], 2)
    latest_60ma = round(df['60MA'].iloc[-1], 2)
    
    # 簡單的波段/獵殺邏輯判斷
    if latest_close < latest_60ma:
        status = "🔴 目前【低於】季線，或許是主動佈局的好時機！"
    else:
        status = "🟢 目前【高於】季線，趨勢偏多，維持紀律即可。"
        
    msg = (
        f"📊 **盤後自動健檢：006208**\n"
        f"最新收盤價：{latest_close}\n"
        f"目前季線值：{latest_60ma}\n"
        f"狀態：{status}"
    )
    return msg

if __name__ == "__main__":
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if discord_url:
        message = check_006208_signal()
        send_discord_msg(message, discord_url)
    else:
        print("Webhook URL 遺失，請檢查 GitHub Secrets")
