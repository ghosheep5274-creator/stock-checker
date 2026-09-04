import os
import requests
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import warnings
import time
from datetime import datetime, timedelta

warnings.filterwarnings('ignore', category=FutureWarning)

def calculate_beta(stock_data, market_data):
    combined = pd.concat([stock_data['Close'], market_data['Close']], axis=1).dropna()
    combined.columns = ['Stock', 'Market']
    returns = combined.pct_change().dropna()
    covariance = returns['Stock'].cov(returns['Market'])
    market_variance = returns['Market'].var()
    return covariance / market_variance

def get_chip_trend(stock_id):
    url = "https://api.finmindtrade.com/api/v4/data"
    start_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    parameter = {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "data_id": stock_id,
        "start_date": start_date,
    }
    try:
        resp = requests.get(url, params=parameter, timeout=5)
        data = resp.json()
        if data.get("msg") == "success" and len(data.get("data", [])) > 0:
            df = pd.DataFrame(data["data"])
            df_foreign = df[df['name'].str.contains('外資')]
            df_trust = df[df['name'] == '投信']
            foreign_daily = df_foreign.groupby('date')['sell_buy'].sum()
            trust_daily = df_trust.groupby('date')['sell_buy'].sum()
            
            def count_consecutive(series):
                if series.empty: return 0
                count = 0
                is_buy = series.iloc[-1] > 0
                if series.iloc[-1] == 0: return 0
                for val in series.iloc[::-1]:
                    if (val > 0) == is_buy and val != 0:
                        count += 1 if is_buy else -1
                    else:
                        break
                return count
            return count_consecutive(foreign_daily), count_consecutive(trust_daily)
    except Exception:
        pass
    return 0, 0

def send_discord_msg(msg, webhook_url):
    # 處理 Discord 2000 字元限制，每 1900 字切塊
    chunks = [msg[i:i+1900] for i in range(0, len(msg), 1900)]
    print(f"準備發送訊息，共切分為 {len(chunks)} 段...")
    
    for idx, chunk in enumerate(chunks, 1):
        response = requests.post(webhook_url, json={"content": chunk})
        if response.status_code not in [200, 204]:
            print(f"❌ 第 {idx} 段發送失敗，狀態碼: {response.status_code}, 錯誤: {response.text}")
        else:
            print(f"✅ 第 {idx} 段訊息發送成功！")
        time.sleep(1)

def run_hunting():
    stock_categories = {
        "📊 【ETF 與 大型權值】": {
            "006208.TW": "富邦台50",
            "2330.TW": "台積電",
            "2354.TW": "鴻準"
        },
        "🏦 【金融控股與銀行】": {
            "2801.TW": "彰銀",
            "2812.TW": "台中銀",
            "2882.TW": "國泰金",
            "2885.TW": "元大金",
            "2887.TW": "台新金",
            "2888.TW": "新光金"
        },
        "💾 【半導體與記憶體】": {
            "2344.TW": "華邦電",
            "3372.TWO": "典範",
            "6533.TW": "晶心科",
            "6770.TW": "力積電",
            "8299.TWO": "群聯"
        },
        "🔌 【電子零組件與光電】": {
            "3481.TW": "群創",
            "3526.TWO": "凡甲",
            "3679.TW": "新至陞"
        }
    }
    
    benchmark = "^TWII"  
    target_rsi = 45
    bb_std = 2.0  
    
    print("📥 開始下載大盤資料作為基準...")
    market_df = yf.download(benchmark, period="1y", interval="1d", progress=False, auto_adjust=True)
    
    msg = f"🔍 **獵殺與防禦系統監控中** (買進 RSI < {target_rsi} / 賣出 RSI > 70)\n\n"
    
    for category_name, stocks in stock_categories.items():
        msg += f"======== {category_name} ========\n"
        
        for ticker, name in stocks.items():
            print(f"⚙️ 正在處理: {name} ({ticker})...")
            df = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
            if df.empty: 
                print(f"⚠️ {name} 抓不到資料，跳過。")
                continue
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            current_beta = calculate_beta(df, market_df)
            recent_df = df.tail(100).copy()
            recent_df['RSI'] = ta.rsi(recent_df['Close'], length=14)
            bb = ta.bbands(recent_df['Close'], length=20, std=bb_std)
            recent_df['60MA'] = ta.sma(recent_df['Close'], length=60)
            
            if bb is None or bb.empty or recent_df['60MA'].isna().iloc[-1]: 
                continue
            
            last_close = recent_df['Close'].iloc[-1]
            day_low = recent_df['Low'].iloc[-1]
            day_high = recent_df['High'].iloc[-1]
            last_rsi = recent_df['RSI'].iloc[-1]
            ma60 = recent_df['60MA'].iloc[-1]
            suggest_buy = bb.iloc[-1, 0]   
            suggest_sell = bb.iloc[-1, 2]  
            beta_status = "穩健" if current_beta < 1 else "激進"
            
            pure_ticker = ticker.split(".")[0]
            fc, tc = get_chip_trend(pure_ticker)
            
            fc_str = f"連買 {fc} 天" if fc > 0 else (f"連賣 {abs(fc)} 天" if fc < 0 else "無明顯動向")
            tc_str = f"連買 {tc} 天" if tc > 0 else (f"連賣 {abs(tc)} 天" if tc < 0 else "無明顯動向")
            
            msg += f"**【{name} ({ticker})】** 收盤: `{last_close:.1f}` | 季線: `{ma60:.1f}`\n"
            msg += f"📊 RSI: `{last_rsi:.1f}` | 區間: `{suggest_buy:.1f}` ~ `{suggest_sell:.1f}`\n"
            msg += f"🏦 籌碼: 外資 `{fc_str}` | 投信 `{tc_str}`\n"
            
            if last_close < ma60:
                if fc < 0 or tc < 0:
                    msg += "⚠️ 🚨 **【破線且大戶倒貨】跌破季線且法人連賣，嚴格停損！**\n\n"
                else:
                    msg += "⚠️ 🚨 **【趨勢破線】已跌破 60 日季線，留意停損或減碼。**\n\n"
            elif day_high > suggest_sell or last_rsi > 70:
                msg += "🔴 🚨 **【波段停利】觸及布林上軌或 RSI 過熱，可分批獲利。**\n\n"
            elif last_rsi < target_rsi and day_low < suggest_buy:
                if fc > 0 or tc > 0:
                    msg += "✅ 🚨 **【法人抬轎買點】進入收藏區且法人連買！建議投入 3,000 元。**\n\n"
                elif current_beta > 1.3:
                    msg += "🚀 🚨 **【強烈買訊但高波動】建議先動用 1,500 元。**\n\n"
                else:
                    msg += "✅ 🚨 **【黃金獵殺點】進入收藏區！建議投入 3,000 元。**\n\n"
            elif last_rsi < target_rsi or day_low < suggest_buy:
                msg += "⚠️ 【接近買點】適合分批布局。\n\n"
            else:
                msg += "😴 【穩定】無強烈訊號，維持紀律。\n\n"
            
            time.sleep(0.3)
        
        msg += "\n"
                
    return msg

# ==========================================
# 這裡是啟動引擎，絕對不能刪掉這段！
# ==========================================
if __name__ == "__main__":
    print("🚀 啟動獵殺小隊腳本...")
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL")
    
    if discord_url:
        print("✅ Webhook URL 讀取成功！")
        final_message = run_hunting()
        print("✅ 報表彙整完畢，準備發送到 Discord...")
        send_discord_msg(final_message, discord_url)
        print("🎉 全部執行完畢！")
    else:
        print("❌ 錯誤：未設定 Discord Webhook URL 環境變數！")
