import os
import requests
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import warnings

warnings.filterwarnings('ignore', category=FutureWarning)

def calculate_beta(stock_data, market_data):
    """計算個股相對於大盤的 Beta 值"""
    combined = pd.concat([stock_data['Close'], market_data['Close']], axis=1).dropna()
    combined.columns = ['Stock', 'Market']
    returns = combined.pct_change().dropna()
    covariance = returns['Stock'].cov(returns['Market'])
    market_variance = returns['Market'].var()
    return covariance / market_variance

def send_discord_msg(msg, webhook_url):
    requests.post(webhook_url, json={"content": msg})

def run_hunting():
    tickers = ["006208.TW", "2330.TW", "8299.TWO"]
    benchmark = "^TWII"  
    target_rsi = 45
    bb_std = 2.0  
    
    # 預先下載大盤數據用於計算 Beta
    market_df = yf.download(benchmark, period="1y", interval="1d", progress=False, auto_adjust=True)
    
    msg = f"🔍 **獵殺小隊監控中** (目標：RSI < {target_rsi}, 偏離度: {bb_std}σ)\n"
    msg += "-" * 40 + "\n"
    
    for ticker in tickers:
        df = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
        if df.empty: continue
        
        # 處理 yfinance 新版的 MultiIndex 問題
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        current_beta = calculate_beta(df, market_df)
        
        recent_df = df.tail(100).copy()
        recent_df['RSI'] = ta.rsi(recent_df['Close'], length=14)
        bb = ta.bbands(recent_df['Close'], length=20, std=bb_std)
        
        if bb is None or bb.empty: continue
        
        last_close = recent_df['Close'].iloc[-1]
        day_low = recent_df['Low'].iloc[-1]
        last_rsi = recent_df['RSI'].iloc[-1]
        suggest_buy = bb.iloc[-1, 0] # BBL (下軌)
        
        beta_status = "穩健" if current_beta < 1 else "激進"
        
        msg += f"**【{ticker}】** 價格: `{last_close:.1f}` | 建議收藏價: `{suggest_buy:.2f}`\n"
        msg += f"📊 當前 RSI: `{last_rsi:.1f}` | 波動 Beta: `{current_beta:.2f}` ({beta_status})\n"
        
        if last_rsi < target_rsi and day_low < suggest_buy:
            if current_beta > 1.3:
                msg += "🚀 🚨 **【強烈訊號但波動高】建議先動用 1,500 元，觀察 Beta 震盪。**\n\n"
            else:
                msg += "✅ 🚨 **【黃金獵殺點】標的穩健且進入收藏區！建議投入 3,000 元。**\n\n"
        elif last_rsi < target_rsi or day_low < suggest_buy:
            msg += "⚠️ 【接近中】RSI 或 價格已有一項達標，適合分批布局。\n\n"
        else:
            msg += "😴 【市場穩定】非甜蜜點，維持定期定額。\n\n"
            
    return msg

if __name__ == "__main__":
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if discord_url:
        final_message = run_hunting()
        send_discord_msg(final_message, discord_url)
    else:
        print("未設定 Discord Webhook URL")
