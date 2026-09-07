import os
import json
import requests
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import warnings
import time
from datetime import datetime, timedelta
from google import genai


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

def generate_ai_summary(json_payload):
    print("🧠 正在將結構化數據交給 Gemini 進行戰略分析...")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "⚠️ 找不到 GEMINI_API_KEY，略過 AI 生成。\n\n"

    try:
        client = genai.Client(api_key=api_key)
        
        prompt = f"""
        你是一位冷靜、紀律嚴明的量化股票經理人。請閱讀以下 JSON 格式的盤後量化數據。
        你的任務是捨棄制式化的數據重述，直接針對有「強烈訊號」的標的給出精煉的決策建議。
        
        【資金調度與決策鐵律】
        1. 006208 核心：維持每月 5,000 元定期定額。若跌破季線且 RSI 止跌，提示動用 3,000 元主動預算獵殺；若出現大盤破年線的黃金坑，可建議額外調度每月 5,000 元活存預備金支援。
        2. 長線底倉 (金融/權值)：若未跌破年線且無法人大賣，直接歸類為「長線靜默，抱緊處理」。
        3. 波段獵殺 (半導體/零組件)：跌破季線且法人倒貨須嚴格停損；若跌破下軌、RSI 止跌且出現爆量/法人買超，提示果斷動用單月 3,000 元額度獵殺。
        4. 新聞情緒防護 (最高優先級)：若標的附有 `recent_news`，請務必綜合判斷。若新聞顯示為「公司個別基本面暴雷 (如財報爆雷、掉單)」，即使技術面超跌也必須發出強烈的【防接刀警告】；若新聞偏向「大盤系統性恐慌 (如國際利空)」，則可維持原有的獵殺建議。
        
        【輸出格式】
        - 挑出最重要的 2~4 檔標的，列點給出 50 字以內的具體決策建議。
        - 若全盤無極端變化，請直接回覆：「今日全盤穩定，維持既有紀律，無須啟動主動資金。」
        
        今日量化數據 (JSON)：
        {json.dumps(json_payload, ensure_ascii=False, indent=2)}
        """
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        
        return f"🧠 **【AI 戰略決策總結】**\n{response.text.strip()}\n\n"
        
    except Exception as e:
        print(f"❌ AI 生成發生錯誤: {e}")
        return "⚠️ AI 分析暫時無法使用。\n\n"

"""
def run_hunting():
    stock_categories = {
        "📊 【ETF 與 大型權值】": {
            "006208.TW": "富邦台50",
            "2330.TW": "台積電"
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
            "3679.TW": "新至陞",
            "2354.TW": "鴻準"
        }
    }
    
    benchmark = "^TWII"  
    target_rsi = 45
    bb_std = 2.0  
    
    print("📥 開始下載大盤資料作為基準...")
    # 將資料抓取期間延長至 2 年 (2y)，確保能計算出 240 日年線
    market_df = yf.Ticker(benchmark).history(period="2y").dropna()
    if market_df.empty:
        print("⚠️ 無法獲取大盤資料，結束執行。")
        return "⚠️ 大盤資料獲取失敗，系統暫停播報。"
    
    msg = f"🔍 **獵殺與防禦系統監控中** (核心長線靜默 / 波段動態獵殺)\n\n"
    
    for category_name, stocks in stock_categories.items():
        msg += f"======== {category_name} ========\n"
        
        # 判斷該分類是否屬於「長線底倉」(ETF、權值、金融)
        is_long_term_core = "ETF" in category_name or "金融" in category_name
        
        for ticker, name in stocks.items():
            print(f"⚙️ 正在處理: {name} ({ticker})...")
            df = yf.Ticker(ticker).history(period="2y").dropna()
            
            if df.empty or len(df) < 60: 
                print(f"⚠️ {name} 抓不到足夠資料，跳過。")
                continue
                
            current_beta = calculate_beta(df, market_df)
            
            # 技術指標計算 (不縮限 tail，確保年線能完整計算)
            recent_df = df.copy()
            recent_df['RSI'] = ta.rsi(recent_df['Close'], length=14)
            bb = ta.bbands(recent_df['Close'], length=20, std=bb_std)
            recent_df['60MA'] = ta.sma(recent_df['Close'], length=60)
            recent_df['240MA'] = ta.sma(recent_df['Close'], length=240)
            
            # --- 新增：成交量與 20 日均量計算 ---
            recent_df['Volume_20MA'] = recent_df['Volume'].rolling(window=20).mean()
            current_volume = recent_df['Volume'].iloc[-1]
            mv20 = recent_df['Volume_20MA'].iloc[-1]
            is_volume_fueled = current_volume > (mv20 * 1.2)
            # ------------------------------------
            
            # 過濾無效指標 (若上市不滿一年無年線，則以季線代替作為防護)
            if bb is None or bb.empty or recent_df['60MA'].isna().iloc[-1] or pd.isna(recent_df['RSI'].iloc[-1]): 
                continue
                
            last_close = recent_df['Close'].iloc[-1]
            day_low = recent_df['Low'].iloc[-1]
            day_high = recent_df['High'].iloc[-1]
            last_rsi = recent_df['RSI'].iloc[-1]
            prev_rsi = recent_df['RSI'].iloc[-2]
            rsi_is_hooking = last_rsi > prev_rsi
            ma60 = recent_df['60MA'].iloc[-1]
            ma240 = recent_df['240MA'].iloc[-1] if not pd.isna(recent_df['240MA'].iloc[-1]) else ma60
            
            suggest_buy = bb.iloc[-1, 0]   
            suggest_sell = bb.iloc[-1, 2]  
            
            pure_ticker = ticker.split(".")[0]
            fc, tc = get_chip_trend(pure_ticker)
            
            fc_str = f"連買 {fc} 天" if fc > 0 else (f"連賣 {abs(fc)} 天" if fc < 0 else "無動向")
            tc_str = f"連買 {tc} 天" if tc > 0 else (f"連賣 {abs(tc)} 天" if tc < 0 else "無動向")
            
            # 報表加入「年線」數據，讓長線格局一目了然
            msg += f"**【{name} ({ticker})】** 收盤: `{last_close:.1f}` | 季線: `{ma60:.1f}` | 年線: `{ma240:.1f}`\n"
            msg += f"📊 RSI: `{last_rsi:.1f}` | 區間: `{suggest_buy:.1f}` ~ `{suggest_sell:.1f}`\n"
            msg += f"🏦 籌碼: 外資 `{fc_str}` | 投信 `{tc_str}`\n"
            
            if current_beta > 1.2:
                dynamic_rsi = 35
            elif current_beta < 0.8:
                dynamic_rsi = 45
            else:
                dynamic_rsi = 40

            is_confirmed_break_60 = last_close < (ma60 * 0.99)
            is_confirmed_break_240 = last_close < (ma240 * 0.99)
            heavy_dumping = (fc <= -3 or tc <= -3) or (fc < 0 and tc < 0)

            # === 1. 專屬 006208 核心防禦與獵殺 ===
            if ticker == "006208.TW":
                if last_close < ma240:
                    msg += "🚨 💎 **【十年一遇黃金坑】大盤跌破年線！全面啟動防禦，維持 5,000 元定額，可評估放大主動加碼金額！**\n\n"
                elif last_close < ma60:
                    if last_rsi < dynamic_rsi and rsi_is_hooking:
                        msg += "💎 🚨 **【波段黃金坑】破季線且 RSI 止跌！維持定額，建議動用 3,000 元主動預算獵殺！**\n\n"
                    else:
                        msg += "🛡️ 【長線防禦區】跌破季線。請無視波動，嚴格維持每月 5,000 元定期定額紀律。\n\n"
                elif last_rsi < dynamic_rsi and day_low < suggest_buy and rsi_is_hooking:
                    msg += "🎯 🚨 **【大盤獵殺點】進入甜美區間！定額外可果斷投入 3,000 元主動預算。**\n\n"
                elif day_high > suggest_sell or last_rsi > 70:
                    msg += "🔥 **【大盤過熱】保留 3,000 元底火，僅維持定額即可。**\n\n"
                else:
                    msg += "🛡️ 【穩健巡航】無極端訊號，安心維持每月 5,000 元定期定額。\n\n"
            
            # === 2. 長線底倉邏輯 (ETF、金融、大型權值) ===
            elif is_long_term_core:
                if is_confirmed_break_240 and heavy_dumping:
                    msg += "💀 🚨 **【信仰破滅】跌破年線且法人無情倒貨，長線底倉請嚴格評估減碼！**\n\n"
                elif is_confirmed_break_240:
                    msg += "⚠️ 🚨 **【年線保衛戰】已跌破 240 日年線，長線趨勢轉弱，密切關注基本面。**\n\n"
                elif is_confirmed_break_60 and heavy_dumping:
                    msg += "⚠️ 🚨 **【中期轉弱】破季線且大戶倒貨，雖未破年線但切勿在此時加碼。**\n\n"
                elif last_rsi < dynamic_rsi and day_low < suggest_buy:
                    if not rsi_is_hooking:
                        msg += f"🔪 ⚠️ **【接刀警告】跌至下軌 (RSI: {last_rsi:.1f}) 但未止跌，暫緩加碼！**\n\n"
                    else:
                        msg += "🎯 🚨 **【長線加碼點】優質資產超跌並止跌！建議動用主動預算佈局。**\n\n"
                else:
                    # 拔除停利警告，改為長線靜默
                    msg += "😴 【長線靜默】優質底倉，忽略短期波動，抱緊處理。\n\n"
            
            # === 3. 短線波段獵殺邏輯 (半導體、電子零組件等) ===
            else:
                if is_confirmed_break_60 and heavy_dumping:
                    msg += "💀 🚨 **【確認破線且無情倒貨】破季線且法人大賣，短線資金請立刻停損！**\n\n"
                elif heavy_dumping:
                    msg += "⚠️ 🚨 **【大戶倒貨中】法人無情拋售，請緊盯減碼時機。**\n\n"
                elif is_confirmed_break_60:
                    msg += "⚠️ 🚨 **【趨勢破線】實體跌破季線，觀察 3 日能否站回，準備減碼。**\n\n"
                elif day_high > suggest_sell or last_rsi > 70:
                    msg += "🔴 🚨 **【波段停利】觸及布林上軌或 RSI 過熱，波段單可分批獲利。**\n\n"
                
                # 💡 修復盲區：只要 RSI 超賣 或 跌破布林下軌，就進入嚴格審查區
                elif day_low < suggest_buy or last_rsi < dynamic_rsi:
                    if not rsi_is_hooking:
                        msg += f"🔪 ⚠️ **【接刀警告】急跌修正中 (RSI: {last_rsi:.1f}) 且未見止跌，手綁起來拒絕接刀！**\n\n"
                    
                    # 💡 結合量能與法人的「黃金爆量買點」
                    elif (fc > 0 or tc > 0) and is_volume_fueled:
                        msg += "✅ 🧲 **【大鯨魚爆量抬轎】高勝率獵殺點出現！建議直接啟動「獵殺蓄水池」總額重擊加碼。**\n\n"
                        
                    elif fc > 0 or tc > 0:
                        msg += "✅ 🚨 **【法人抬轎買點】止跌回升且法人偷買！建議動用單月 3,000 元額度佈局。**\n\n"
                    elif current_beta > 1.3:
                        msg += "🚀 🚨 **【止跌反轉但高波動】股性較妖，建議先試水溫。**\n\n"
                    else:
                        msg += "🎯 🚨 **【黃金獵殺點】打到下軌且 RSI 止跌回升！建議果斷投入。**\n\n"
                        
                # 獨立的觀察區邏輯，絕不與急跌破線衝突
                elif last_rsi < target_rsi:
                    msg += "⚠️ 【接近買點】已進入觀察區，等待止跌訊號。\n\n"
                else:
                    msg += "😴 【穩定】無強烈訊號，維持波段紀律。\n\n"
            
            time.sleep(0.5)
        
        msg += "\n"
                
    return msg
"""

def run_hunting():
    stock_categories = {
        "📊 【ETF 與 大型權值】": {
            "006208.TW": "富邦台50",
            "2330.TW": "台積電"
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
            "3679.TW": "新至陞",
            "2354.TW": "鴻準"
        }
    }
    
    benchmark = "^TWII"  
    target_rsi = 45
    bb_std = 2.0  
    
    print("📥 開始下載大盤資料作為基準...")
    # 將資料抓取期間延長至 2 年 (2y)，確保能計算出 240 日年線
    market_df = yf.Ticker(benchmark).history(period="2y").dropna()
    if market_df.empty:
        print("⚠️ 無法獲取大盤資料，結束執行。")
        return "⚠️ 大盤資料獲取失敗，系統暫停播報。"
    
    msg_raw_data = "📊 **【個股數據儀表板】**\n\n"
    ai_json_payload = [] # 用來收集給 AI 判斷的結構化陣列
    
    for category_name, stocks in stock_categories.items():
        msg_raw_data += f"======== {category_name} ========\n"
        is_long_term_core = "ETF" in category_name or "金融" in category_name
        
        for ticker, name in stocks.items():
            print(f"⚙️ 正在處理: {name} ({ticker})...")
            df = yf.Ticker(ticker).history(period="2y").dropna()
            
            if df.empty or len(df) < 60: 
                continue
                
            # 計算指標與量能
            recent_df = df.copy()
            recent_df['RSI'] = ta.rsi(recent_df['Close'], length=14)
            bb = ta.bbands(recent_df['Close'], length=20, std=2.0)
            recent_df['60MA'] = ta.sma(recent_df['Close'], length=60)
            recent_df['240MA'] = ta.sma(recent_df['Close'], length=240)
            recent_df['Volume_20MA'] = recent_df['Volume'].rolling(window=20).mean()
            
            if bb is None or bb.empty or recent_df['60MA'].isna().iloc[-1] or pd.isna(recent_df['RSI'].iloc[-1]): 
                continue
                
            # 抓取最新數值
            last_close = recent_df['Close'].iloc[-1]
            day_low = recent_df['Low'].iloc[-1]
            day_high = recent_df['High'].iloc[-1]
            last_rsi = recent_df['RSI'].iloc[-1]
            rsi_is_hooking = last_rsi > recent_df['RSI'].iloc[-2]
            ma60 = recent_df['60MA'].iloc[-1]
            ma240 = recent_df['240MA'].iloc[-1] if not pd.isna(recent_df['240MA'].iloc[-1]) else ma60
            suggest_buy = bb.iloc[-1, 0]   
            suggest_sell = bb.iloc[-1, 2]  
            
            current_volume = recent_df['Volume'].iloc[-1]
            mv20 = recent_df['Volume_20MA'].iloc[-1]
            is_volume_fueled = current_volume > (mv20 * 1.2)

            # --- 💡 新增：極端狀態下的新聞防護網 ---
            recent_news = []
            # 只有當「實體跌破季線」或「跌穿布林下軌 (超跌)」時，才去抓新聞
            if last_close < ma60 or day_low < suggest_buy:
                print(f"📰 {name} 觸發極端狀態，正在啟動新聞雷達...")
                try:
                    raw_news = yf.Ticker(ticker).news
                    # 只擷取最新的 3 則新聞標題，避免雜訊過多
                    if raw_news:
                        recent_news = [n.get('title', '無標題') for n in raw_news[:3]]
                except Exception as e:
                    print(f"⚠️ 無法抓取 {name} 新聞: {e}")
            
            pure_ticker = ticker.split(".")[0]
            fc, tc = get_chip_trend(pure_ticker)
            
            # --- 1. 組合純數據字串 (保留給你看的原始儀表板) ---
            fc_str = f"連買 {fc} 天" if fc > 0 else (f"連賣 {abs(fc)} 天" if fc < 0 else "無動向")
            tc_str = f"連買 {tc} 天" if tc > 0 else (f"連賣 {abs(tc)} 天" if tc < 0 else "無動向")
            
            msg_raw_data += f"**【{name} ({ticker})】** 收盤: `{last_close:.1f}` | 季線: `{ma60:.1f}` | 年線: `{ma240:.1f}`\n"
            msg_raw_data += f"📊 RSI: `{last_rsi:.1f}` | 區間: `{suggest_buy:.1f}` ~ `{suggest_sell:.1f}`\n"
            msg_raw_data += f"🏦 籌碼: 外資 `{fc_str}` | 投信 `{tc_str}`\n\n"
            
            # --- 2. 封裝 JSON 結構化數據 (餵給 Gemini 的核心大腦) ---
            stock_json = {
                "stock_name": name,
                "strategy_type": "核心/底倉" if is_long_term_core else "波段獵殺",
                "close_price": round(last_close, 1),
                "is_below_60MA": bool(last_close < ma60),
                "is_below_240MA": bool(last_close < ma240),
                "rsi_value": round(last_rsi, 1),
                "rsi_is_hooking": bool(rsi_is_hooking),
                "is_oversold_bb": bool(day_low < suggest_buy),
                "is_overheated": bool(last_rsi > 70 or day_high > suggest_sell),
                "foreign_chip_trend": fc,
                "trust_chip_trend": tc,
                "is_bottom_high_volume": bool(is_volume_fueled and day_low < suggest_buy),
                "recent_news": recent_news
            }
            ai_json_payload.append(stock_json)
            
            time.sleep(0.5)
            
    # 迴圈結束，將整包 JSON 丟給 AI 寫總結
    ai_final_summary = generate_ai_summary(ai_json_payload)
            
    # 將 AI 總結放在最上方，原始數據附在下方
    return ai_final_summary + "---\n" + msg_raw_data


if __name__ == "__main__":
    print("🚀 啟動獵殺小隊腳本...")
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL")
    
    if discord_url:
        print("✅ Webhook URL 讀取成功！")
        final_message = run_hunting()
        # --- 💡 新增這行：讓 AI 讀取報表並加上總結 ---
        final_message = generate_ai_summary(final_message)
        print("✅ 報表彙整完畢，準備發送到 Discord...")
        send_discord_msg(final_message, discord_url)
        print("🎉 全部執行完畢！")
    else:
        print("❌ 錯誤：未設定 Discord Webhook URL 環境變數！")
