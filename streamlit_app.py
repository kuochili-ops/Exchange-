import streamlit as st
import requests
import pandas as pd
import io
import time
import ast
import operator as op
import re

# ---------- 設定 ----------
BOT_CSV_URL = "https://rate.bot.com.tw/xrt/flcsv/0/day"
CACHE_TTL = 600  # 10 分鐘快取

# 偽裝成瀏覽器的 Header，避免被 BOT 阻擋連線（尤其 Streamlit Cloud 主機在國外）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
}

FLAGS = {
    "TWD": "🇹🇼", "USD": "🇺🇸", "JPY": "🇯🇵", "EUR": "🇪🇺", "CNY": "🇨🇳",
    "HKD": "🇭🇰", "GBP": "🇬🇧", "AUD": "🇦🇺", "SGD": "🇸🇬", "KRW": "🇰🇷",
    "CAD": "🇨🇦", "CHF": "🇨🇭", "ZAR": "🇿🇦", "SEK": "🇸🇪", "NZD": "🇳🇿",
    "THB": "🇹🇭", "PHP": "🇵🇭", "IDR": "🇮🇩", "VND": "🇻🇳", "MYR": "🇲🇾",
    "DKK": "🇩🇰", "IDR": "🇮🇩", "INR": "🇮🇳", "RUB": "🇷🇺", "SAR": "🇸🇦",
}

# ---------- 工具函式 ----------
def format_number(n):
    """格式化數字：去除多餘的零，加上千分位"""
    try:
        s = float(n)
    except Exception:
        return "0"
    
    # 格式化為字串，保留足夠小數位以免精度丟失
    s2 = ("{:.8f}".format(s)).rstrip('0').rstrip('.')
    parts = s2.split('.')
    try:
        # 整數部分加千分位
        parts[0] = "{:,}".format(int(parts[0])) if parts[0] != '' else '0'
    except Exception:
        parts[0] = parts[0]
    
    return parts[0] + ('.' + parts[1] if len(parts) > 1 else '')

# 安全運算算子白名單
ALLOWED_OPERATORS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.USub: op.neg, ast.UAdd: op.pos
}

def safe_eval(expr: str):
    """
    使用 ast.parse 進行安全的數學運算評估
    """
    def _eval(node):
        if isinstance(node, (ast.Constant, ast.Num)):
            if isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, ast.Num): # Python < 3.8 fallback
                 return node.n
            raise ValueError("不支援的常數類型")
        if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_OPERATORS:
            return ALLOWED_OPERATORS[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_OPERATORS:
            return ALLOWED_OPERATORS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        raise ValueError("不支援的運算")

    try:
        if not expr: return 0
        node = ast.parse(expr, mode='eval')
        return _eval(node)
    except ZeroDivisionError:
        return float('inf')
    except Exception:
        raise ValueError("計算錯誤")

def _to_float(x):
    if pd.isna(x):
        return None
    try:
        return float(str(x).replace(',', ''))
    except Exception:
        return None

# ---------- 取得並解析 BOT CSV（快取） ----------
@st.cache_data(ttl=CACHE_TTL)
def fetch_rates():
    """
    回傳 (rates_dict, error_message)
    """
    try:
        r = requests.get(BOT_CSV_URL, headers=HEADERS, timeout=15)
        r.encoding = 'utf-8-sig'  # 處理 Excel 常見的 BOM
        
        if r.status_code != 200:
            return {}, f"伺服器回應錯誤: {r.status_code}"

        txt = r.text
        # 嘗試讀取 CSV
        df = pd.read_csv(io.StringIO(txt))
        
    except requests.exceptions.RequestException as e:
        return {}, f"網路請求失敗，請檢查連線或 BOT 網站: {e}"
    except Exception as e:
        st.error(f"解析 CSV 失敗，可能格式已變動。原始回應開頭: {txt[:200]}...")
        return {}, f"解析 CSV 失敗: {e}"
    
    rates = {}
    try:
        for _, row in df.iterrows():
            cur_field = row.get('幣別') or row.get('Currency') or ''
            
            # 優先從括號內抓取代碼 (e.g. 美金(USD))
            code = None
            if isinstance(cur_field, str):
                m = re.search(r'\((\w+)\)', cur_field) 
                if m:
                    code = m.group(1)
            
            # 如果找不到，嘗試使用 'Currency Code' 欄位 (e.g. USD)
            if not code:
                code = (row.get('Currency Code') or '').strip()
            
            if not code:
                continue

            # 抓取即期買入與賣出價
            buy = _to_float(row.get('即期買入') or row.get('Spot Buy') or None)
            sell = _to_float(row.get('即期賣出') or row.get('Spot Sell') or None)
            
            # 使用平均價作為參考匯率
            val = None
            if buy is not None and sell is not None and buy > 0 and sell > 0:
                val = (buy + sell) / 2.0
            elif sell is not None and sell > 0:
                val = sell
            elif buy is not None and buy > 0:
                val = buy
            
            if val is not None:
                rates[code] = val
                
    except Exception as e:
        return {}, f"內部解析錯誤: {e}"
    
    # 設置 TWD 為基底 (1.0)
    rates['TWD'] = 1.0
    return rates, None

# ---------- safe rerun（相容性處理） ----------
def safe_rerun():
    """嘗試執行 Streamlit rerun，相容不同版本"""
    try:
        st.rerun()
    except AttributeError:
        try:
            st.experimental_rerun()
        except AttributeError:
            pass # 無法 Rerun，等待下次互動

# ---------- UI 與狀態管理 ----------
st.set_page_config(page_title="即時匯率計算機", page_icon="💱", layout="wide")

# CSS 優化：確保手機上按鈕顯示正常且間距合適
st.markdown("""
<style>
/* 確保主內容區塊在手機上有足夠 padding */
section.main .block-container {
    padding-left: 1rem;
    padding-right: 1rem;
    padding-top: 1rem;
}

/* 計算機按鍵樣式 */
div.stButton > button {
    /* 增加垂直填充，讓按鈕更好點擊 */
    padding: 10px 0px; 
    font-size: 16px;
    font-weight: bold;
    border-radius: 8px;
    transition: all 0.1s;
}

/* 貨幣選擇按鈕稍微小一點 */
div[data-testid="column"] div.stButton > button {
    padding: 6px 0px; 
    font-size: 14px;
}

/* 結果顯示區塊 */
.result-box {
    background-color: #f0f2f6; /* 淺灰色背景 */
    padding: 15px;
    border-radius: 10px;
    text-align: right;
    margin-bottom: 15px;
}
.result-expr { font-size: 1.1rem; color: #666; font-family: monospace; min-height: 1.4rem; }
.result-val { font-size: 2.2rem; font-weight: bold; color: #333; }
.result-cur { font-size: 1rem; color: #888; }
</style>
""", unsafe_allow_html=True)

# 初始化 Session State
if 'expr' not in st.session_state: st.session_state.expr = ''
if 'last' not in st.session_state: st.session_state.last = 0.0
if 'memory' not in st.session_state: st.session_state.memory = 0.0 # 始終以 TWD 儲存
if 'displayed' not in st.session_state:
    st.session_state.displayed = ['TWD', 'USD', 'JPY', 'EUR', 'CNY']
if 'selected' not in st.session_state: st.session_state.selected = 'TWD'
if 'rates_updated' not in st.session_state: st.session_state.rates_updated = ''

# 1. 取得匯率
rates, fetch_err = fetch_rates()

# Fallback data
if not rates:
    st.sidebar.warning("⚠️ 使用備用匯率資料 (TWD=1, USD=32.5, JPY=0.21, EUR=35.0)")
    rates = {"TWD":1.0, "USD":32.5, "JPY":0.21, "EUR":35.0, "CNY":4.5, "HKD":4.1}

# 側邊欄資訊
st.sidebar.title("設定與資訊")
if fetch_err:
    st.sidebar.error(f"❌ 匯率抓取失敗: {fetch_err}")
else:
    st.sidebar.success("✅ 匯率更新成功")

st.sidebar.info(f"資料來源: 台灣銀行 (BOT)\n更新時間: {st.session_state.rates_updated or time.strftime('%H:%M:%S')}")

if st.sidebar.button("🔄 強制重新抓取匯率"):
    st.cache_data.clear()
    st.session_state.rates_updated = time.strftime("%Y-%m-%d %H:%M:%S")
    safe_rerun()

st.sidebar.markdown("---")
st.sidebar.write(f"**目前記憶 (TWD)**: {format_number(st.session_state.memory)}")

# 主標題
st.title("💱 匯率計算機")

# 2. 顯示結果區 (模擬計算機螢幕)
current_currency = st.session_state.selected
current_flag = FLAGS.get(current_currency, '')

st.markdown(f"""
<div class="result-box">
    <div class="result-expr">{st.session_state.expr if st.session_state.expr else '&nbsp;'}</div>
    <div class="result-val">{format_number(st.session_state.last)}</div>
    <div class="result-cur">{current_flag} {current_currency}</div>
</div>
""", unsafe_allow_html=True)

# 3. 貨幣切換列 (Top 5)
def handle_currency_switch(code, prev_code):
    """處理貨幣切換與換算邏輯"""
    if st.session_state.last != 0 and prev_code in rates and code in rates:
        # 邏輯：先換回 TWD，再換成目標幣別
        val_in_twd = st.session_state.last * rates[prev_code]
        val_target = val_in_twd / rates[code]
        st.session_state.last = val_target
        # 清空運算式，因為數值已經變了，不再對應原本的算式
        st.session_state.expr = str(val_target)
    
    st.session_state.selected = code

cols = st.columns(5)
for i, col in enumerate(cols):
    code = st.session_state.displayed[i] if i < len(st.session_state.displayed) else 'TWD'
    flag = FLAGS.get(code, '')
    btn_label = f"{flag} {code}"
    
    is_active = (code == st.session_state.selected)
    
    if col.button(btn_label, 
                  key=f"cur_btn_{i}", 
                  type="primary" if is_active else "secondary", 
                  use_container_width=True,
                  on_click=handle_currency_switch,
                  args=(code, st.session_state.selected)):
        safe_rerun()


# 4. 計算機邏輯函式
def press(ch):
    st.session_state.expr += str(ch)

def backspace():
    st.session_state.expr = st.session_state.expr[:-1]

def clear_all():
    st.session_state.expr = ''
    st.session_state.last = 0.0

def toggle_sign():
    # 簡單的正負號切換
    if not st.session_state.expr:
        st.session_state.expr = '-'
        return
    
    # 嘗試解析整個式子並反轉
    try:
        val = safe_eval(st.session_state.expr)
        if val == 0: return
        st.session_state.expr = str(-val)
        st.session_state.last = -val
    except:
        # 如果無法解析成單一數字，嘗試在前面加負號
        st.session_state.expr += '-'

def do_calculate():
    s = st.session_state.expr.strip()
    if not s: 
        st.session_state.last = 0.0
        return
    
    s_clean = re.sub(r'[^0-9+\-*/().]', '', s)
    try:
        val = safe_eval(s_clean)
        st.session_state.last = float(val)
        st.session_state.expr = str(float(val)) # 將運算結果轉為下一個起點
    except ValueError as e:
        st.error(f"運算格式錯誤: {e}")
    except Exception:
        st.error("發生未預期運算錯誤")

def memory_add():
    do_calculate()
    val_twd = st.session_state.last * rates.get(st.session_state.selected, 1.0)
    st.session_state.memory += val_twd
    st.toast(f"已加入記憶 (TWD: {format_number(val_twd)})")

def memory_subtract():
    do_calculate()
    val_twd = st.session_state.last * rates.get(st.session_state.selected, 1.0)
    st.session_state.memory -= val_twd
    st.toast(f"已從記憶扣除 (TWD: {format_number(val_twd)})")

def memory_recall():
    recalled = st.session_state.memory / rates.get(st.session_state.selected, 1.0)
    st.session_state.expr = str(recalled)
    st.session_state.last = recalled

def memory_clear():
    st.session_state.memory = 0.0
    st.toast("記憶已清除")

def ans_to_expr():
    st.session_state.expr = str(st.session_state.last)

# 5. 計算機按鍵佈局
st.markdown("---")

# Row M (記憶鍵)
c1, c2, c3, c4 = st.columns(4)
with c1: 
    st.button("MC", use_container_width=True, on_click=memory_clear)
with c2: 
    st.button("MR", use_container_width=True, on_click=memory_recall)
with c3: 
    st.button("M+", use_container_width=True, on_click=memory_add)
with c4: 
    st.button("M-", use_container_width=True, on_click=memory_subtract)

# Row 1 (功能鍵)
r1_1, r1_2, r1_3, r1_4 = st.columns(4)
with r1_1:
    st.button("C", type="primary", use_container_width=True, on_click=clear_all)
with r1_2:
    st.button("⌫", use_container_width=True, on_click=backspace)
with r1_3: 
    st.button("( )", use_container_width=True, on_click=press, args=("(",)) # 簡化為只按 (
with r1_4:
    st.button("÷", use_container_width=True, on_click=press, args=("/",))

# Row 2 (7, 8, 9, x)
r2_1, r2_2, r2_3, r2_4 = st.columns(4)
with r2_1: st.button("7", use_container_width=True, on_click=press, args=("7",))
with r2_2: st.button("8", use_container_width=True, on_click=press, args=("8",))
with r2_3: st.button("9", use_container_width=True, on_click=press, args=("9",))
with r2_4: st.button("×", use_container_width=True, on_click=press, args=("*",))

# Row 3 (4, 5, 6, -)
r3_1, r3_2, r3_3, r3_4 = st.columns(4)
with r3_1: st.button("4", use_container_width=True, on_click=press, args=("4",))
with r3_2: st.button("5", use_container_width=True, on_click=press, args=("5",))
with r3_3: st.button("6", use_container_width=True, on_click=press, args=("6",))
with r3_4: st.button("－", use_container_width=True, on_click=press, args=("-",))

# Row 4 (1, 2, 3, +)
r4_1, r4_2, r4_3, r4_4 = st.columns(4)
with r4_1: st.button("1", use_container_width=True, on_click=press, args=("1",))
with r4_2: st.button("2", use_container_width=True, on_click=press, args=("2",))
with r4_3: st.button("3", use_container_width=True, on_click=press, args=("3",))
with r4_4: st.button("＋", use_container_width=True, on_click=press, args=("+",))

# Row 5 (0, ., ±, =)
r5_1, r5_2, r5_3, r5_4 = st.columns(4)
with r5_1: st.button("0", use_container_width=True, on_click=press, args=("0",))
with r5_2: st.button(".", use_container_width=True, on_click=press, args=(".",))
with r5_3: 
    st.button("±", use_container_width=True, on_click=toggle_sign)
with r5_4: 
    st.button("＝", type="primary", use_container_width=True, on_click=do_calculate)

st.markdown("---")

# 6. 自訂貨幣列設定
with st.expander("⚙️ 自訂上方快捷貨幣列"):
    all_codes = sorted(list(rates.keys()))
    valid_defaults = [c for c in st.session_state.displayed if c in all_codes]
    
    new_selection = st.multiselect(
        "選擇 5 個常用貨幣", 
        options=all_codes, 
        default=valid_defaults,
        max_selections=5,
        key="currency_multiselect"
    )
    
    if st.button("更新快捷列", key="update_display"):
        # 確保 TWD 在列中 (如果它存在的話)
        final_list = []
        if 'TWD' in new_selection:
            final_list.append('TWD')
            
        for c in new_selection:
            if c != 'TWD' and len(final_list) < 5:
                 final_list.append(c)
        
        # 如果不足 5 個，用其他熱門幣別補滿
        if len(final_list) < 5:
            for c in ['USD', 'JPY', 'EUR', 'CNY', 'HKD']:
                if c not in final_list and c in all_codes:
                    final_list.append(c)
                if len(final_list) >= 5: break
        
        st.session_state.displayed = final_list
        safe_rerun()

