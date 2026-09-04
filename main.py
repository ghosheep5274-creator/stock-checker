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
    # 剝離時區，確保合併時日期能完美對齊
    stock_close = stock_data['Close'].copy()
    market_close = market_data['Close'].copy()
    stock_close.index = stock_close.index.tz_localize(None)
    market_close.index = market_close.index.tz_localize(None)
    
    combined = pd.concat([stock_close, market_close], axis=1).dropna()
    combined.columns = ['Stock', 'Market']
    returns = combined.pct_change().dropna()
    
    covariance = returns['Stock'].cov(returns['Market'])
    market_variance = returns['Market'].var()
    return covariance / market_variance

def get_chip_trend(stock_id):
    url = "https://api.finmindtrade.com/api/v4/data"
    start_date = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
    parameter = {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "data_id": stock_id,
        "start_date": start_date,
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        resp = requests.get(url, params=parameter, headers=headers, timeout=10)
        data = resp.json()
        
        if data.get("msg") == "success":
            raw_data = data.get("data", [])
            if len(raw_data) == 0:
                print(f"⚠️ [{stock_id}] API 呼叫成功，但近期無法人買賣資料。")
                return 0, 0
                
            df = pd.DataFrame(raw_data)
            
            # 強制將所有相關欄位轉為數值，計算買賣超
            if 'buy' in df.columns and 'sell' in df.columns:
                df['buy'] = pd.to_numeric(df['buy'], errors='coerce').fillna(0)
                df['sell'] = pd.to_numeric(df['sell'], errors='coerce').fillna(0)
                df['sell_buy'] = df['buy'] - df['sell']
            elif 'sell_buy' in df.columns:
                df['sell_buy'] = pd.to_numeric(df['sell_buy'], errors='coerce').fillna(0)
            else:
                return 0, 0
                
            # 破案關鍵：改為比對 API 回傳的英文法人名稱
            df_foreign = df[df['name'] == 'Foreign_Investor']
            df_trust = df[df['name'] == 'Investment_Trust']
            
            foreign_daily = df_foreign.sort_values('date').groupby('date')['sell_buy'].sum()
            trust_daily = df_trust.sort_values('date').groupby('date')['sell_buy'].sum()
            
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
            
    except Exception as e:
        print(f"❌ [{stock_id}] 籌碼計算發生錯誤: {e}")
        
    return 0, 0
    
def send_discord_msg(msg, webhook_url):
    lines = msg.split('\n')
    chunks = []
    current_chunk = ""
    
    for line in lines:
        if len(current_chunk) + len(line) + 1 > 1900:
            chunks.append(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
            
    if current_chunk.strip():
        chunks.append(current_chunk)

    print(f"準備發送訊息，依行數安全切分為 {len(chunks)} 段...")
    
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
    # 改用 Ticker().history 寫法，避免 MultiIndex 問題
    market_df = yf.Ticker(benchmark).history(period="1y")
    if market_df.empty:
        print("⚠️ 無法獲取大盤資料，結束執行。")
        return "⚠️ 大盤資料獲取失敗，系統暫停播報。"
    
    msg = f"🔍 **獵殺與防禦系統監控中** (買進 RSI < {target_rsi} / 賣出 RSI > 70)\n\n"
    
    for category_name, stocks in stock_categories.items():
        msg += f"======== {category_name} ========\n"
        
        for ticker, name in stocks.items():
            print(f"⚙️ 正在處理: {name} ({ticker})...")
            # 改用 Ticker().history 確保穩定抓取個股
            df = yf.Ticker(ticker).history(period="1y")
            
            if df.empty or len(df) < 60: 
                print(f"⚠️ {name} 抓不到足夠資料，跳過。")
                continue
                
            current_beta = calculate_beta(df, market_df)
            recent_df = df.tail(100).copy()
            recent_df['RSI'] = ta.rsi(recent_df['Close'], length=14)
            bb = ta.bbands(recent_df['Close'], length=20, std=bb_std)
            recent_df['60MA'] = ta.sma(recent_df['Close'], length=60)
            
            # 過濾無效指標
            if bb is None or bb.empty or recent_df['60MA'].isna().iloc[-1] or pd.isna(recent_df['RSI'].iloc[-1]): 
                continue
            
            last_close = recent_df['Close'].iloc[-1]
            day_low = recent_df['Low'].iloc[-1]
            day_high = recent_df['High'].iloc[-1]
            last_rsi = recent_df['RSI'].iloc[-1]
            prev_rsi = recent_df['RSI'].iloc[-2]         # 新增：前一日 RSI
            rsi_is_hooking = last_rsi > prev_rsi         # 新增：判斷 RSI 是否勾頭向上
            ma60 = recent_df['60MA'].iloc[-1]
            suggest_buy = bb.iloc[-1, 0]   
            suggest_sell = bb.iloc[-1, 2]  
            beta_status = "穩健" if current_beta < 1 else "激進"
            
            pure_ticker = ticker.split(".")[0]
            fc, tc = get_chip_trend(pure_ticker)
            
            fc_str = f"連買 {fc} 天" if fc > 0 else (f"連賣 {abs(fc)} 天" if fc < 0 else "無明顯動向")
            tc_str = f"連買 {tc} 天" if tc > 0 else (f"連賣 {abs(tc)} 天" if tc < 0 else "無明顯動向")
            
            msg += f"**【{name} ({ticker})】** 收盤: `{last_close:.2f}` | 季線: `{ma60:.2f}`\n"
            msg += f"📊 RSI: `{last_rsi:.1f}` | 區間: `{suggest_buy:.2f}` ~ `{suggest_sell:.2f}`\n"
            msg += f"🏦 籌碼: 外資 `{fc_str}` | 投信 `{tc_str}`\n"
            
            # === 整合技術面與籌碼面的判斷邏輯 ===
            
            # --- 1. 抓取所需指標 ---
            last_rsi = recent_df['RSI'].iloc[-1]
            prev_rsi = recent_df['RSI'].iloc[-2]
            rsi_is_hooking = last_rsi > prev_rsi
            ma60 = recent_df['60MA'].iloc[-1]
            
            # --- 2. 根據 Beta 動態調整 RSI 甜美門檻 ---
            if current_beta > 1.2:
                dynamic_rsi = 35
            elif current_beta < 0.8:
                dynamic_rsi = 45
            else:
                dynamic_rsi = 40
                
            # --- 3. 專屬 006208 核心防禦與獵殺邏輯 ---
            if ticker == "006208.TW":
                if last_close < ma60:
                    if last_rsi < dynamic_rsi and rsi_is_hooking:
                        msg += "💎 🚨 **【黃金坑出現】006208 破線且 RSI 止跌回升！維持每月 5,000 元定期定額，並建議動用 3,000 元主動預算獵殺加碼！**\n\n"
                    else:
                        msg += "🛡️ 🚨 **【長線防禦區】006208 已跌破季線。請無視短期波動，嚴格維持每月 5,000 元定期定額紀律，切勿恐慌停損。**\n\n"
                elif last_rsi < dynamic_rsi and day_low < suggest_buy and rsi_is_hooking:
                    msg += "🎯 🚨 **【大盤獵殺點】進入甜美區間！除了 5,000 元定期定額，可果斷投入 3,000 元主動預算。**\n\n"
                elif day_high > suggest_sell or last_rsi > 70:
                    msg += "🔥 **【大盤過熱】觸及布林上軌或 RSI 過熱。保留 3,000 元底火，僅維持 5,000 元定期定額即可。**\n\n"
                else:
                    msg += "🛡️ 【穩健巡航】大盤無極端訊號，安心維持每月 5,000 元定期定額。\n\n"
            
            # --- 4. 一般個股的攻防邏輯 ---
            else:
                is_confirmed_break = last_close < (ma60 * 0.99)
                heavy_dumping = (fc <= -3 or tc <= -3) or (fc < 0 and tc < 0)
                
                if last_close < ma60:
                    if is_confirmed_break and heavy_dumping:
                        msg += "💀 🚨 **【確認破線且無情倒貨】跌破季線 1% 且法人大賣，請立刻嚴格停損！**\n\n"
                    elif heavy_dumping:
                        msg += "⚠️ 🚨 **【大戶倒貨中】法人無情拋售，雖未完全破線，請緊盯減碼時機。**\n\n"
                    elif is_confirmed_break:
                        msg += "⚠️ 🚨 **【趨勢破線】已實體跌破 60 日季線，觀察 3 日內能否站回，準備減碼。**\n\n"
                    else:
                        msg += "⚠️ 【季線保衛戰】剛觸碰季線邊緣，注意法人後續動向。\n\n"
                elif day_high > suggest_sell or last_rsi > 70:
                    msg += "🔴 🚨 **【波段停利】觸及布林上軌或 RSI 過熱，波段單可分批獲利了結。**\n\n"
                elif last_rsi < dynamic_rsi and day_low < suggest_buy:
                    if not rsi_is_hooking:
                        msg += f"🔪 ⚠️ **【接刀警告】已跌至布林下軌 (RSI: {last_rsi:.1f})，但仍未止跌，請暫緩動用資金！**\n\n"
                    elif fc > 0 or tc > 0:
                        msg += "✅ 🚨 **【法人抬轎買點】止跌回升且法人偷買！建議果斷投入。**\n\n"
                    elif current_beta > 1.3:
                        msg += "🚀 🚨 **【止跌反轉但高波動】RSI 已勾頭，但股性較妖，建議先試水溫。**\n\n"
                    else:
                        msg += "🎯 🚨 **【黃金獵殺點】打到下軌且 RSI 止跌回升！建議果斷投入。**\n\n"
                elif last_rsi < target_rsi or day_low < suggest_buy:
                    msg += "⚠️ 【接近買點】已進入觀察區，等待止跌訊號。\n\n"
                else:
                    msg += "😴 【穩定】無強烈訊號，維持紀律。\n\n"
            
            time.sleep(0.5)
        
        msg += "\n"
                
    return msg

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
