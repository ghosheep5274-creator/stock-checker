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
    """取得外資與投信的連續買賣超天數"""
    url = "https://api.finmindtrade.com/api/v4/data"
    # 抓取近 20 天的資料以確保有足夠的交易日可供計算
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
            
            # 篩選外資與投信的資料
            df_foreign = df[df['name'].str.contains('外資')]
            df_trust = df[df['name'] == '投信']
            
            # 依日期加總淨買賣超股數
            foreign_daily = df_foreign.groupby('date')['sell_buy'].sum()
            trust_daily = df_trust.groupby('date')['sell_buy'].sum()
            
            def count_consecutive(series):
                if series.empty: return 0
                count = 0
                is_buy = series.iloc[-1] > 0
                if series.iloc[-1] == 0: return 0
                
                # 從最新的一天往前推算
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
    requests.post(webhook_url, json={"content": msg})

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
    
    market_df = yf.download(benchmark, period="1y", interval="1d", progress=False, auto_adjust=True)
    
    msg = f"🔍 **獵殺與防禦系統監控中** (買進 RSI < {target_rsi} / 賣出 RSI > 70)\n\n"
    
    for category_name, stocks in stock_categories.items():
        msg += f"======== {category_name} ========\n"
        
        for ticker, name in stocks.items():
            # yfinance 獲取技術面
            df = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
            if df.empty: continue
            
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
            
            # FinMind 獲取籌碼面 (移除 .TW / .TWO 以符合 API 格式)
            pure_ticker = ticker.split(".")[0]
            fc, tc = get_chip_trend(pure_ticker)
            
            # 將連續買賣天數轉為友善文字
            fc_str = f"連買 {fc} 天" if fc > 0 else (f"連賣 {abs(fc)} 天" if fc < 0 else "無明顯動向")
            tc_str = f"連買 {tc} 天" if tc > 0 else (f"連賣 {abs(tc)} 天" if tc < 0 else "無明顯動向")
            
            # 排版輸出
            msg += f"**【{name} ({ticker})】** 收盤: `{last_close:.1f}` | 季線: `{ma60:.1f}`\n"
            msg += f"📊 RSI: `{last_rsi:.1f}` | 區間: `{suggest_buy:.1f}` ~ `{suggest_sell:.1f}`\n"
            msg += f"🏦 籌碼: 外資 `{fc_str}` | 投信 `{tc_str}`\n"
            
            # 整合技術面與籌碼面的判斷邏輯
            if last_close < ma60:
                if fc < 0 or tc < 0:
                    msg += "⚠️ 🚨 **【破線且大戶倒貨】跌破季線且法人連賣，請嚴格執行停損或減碼！**\n\n"
                else:
                    msg += "⚠️ 🚨 **【趨勢破線】已跌破 60 日季線，請留意停損或減碼時機。**\n\n"
            elif day_high > suggest_sell or last_rsi > 70:
                msg += "🔴 🚨 **【波段停利】觸及布林上軌或 RSI 過熱，波段單可分批獲利了結。**\n\n"
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
            
            # 稍作延遲，避免密集呼叫被 API 阻擋
            time.sleep(0.3)
        
        msg += "\n"
                
    return msg

if __name__ == "__main__":
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if discord_url:
        final_message = run_hunting()
        send_discord_msg(final_message, discord_url)
    else:
        print("未設定 Discord Webhook URL")
