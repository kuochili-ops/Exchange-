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
    
    s2 = ("{:.8f}".format(s)).rstrip('0').rstrip('.')
    parts = s2.split('.')
    try:
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
    """使用 ast.parse 進行安全的數學運算評估"""
    def _eval(node):
        if isinstance(node, (ast.Constant, ast.Num)):
            if isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, ast.Num):
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
    """回傳 (rates_dict, error_message)"""
    try:
        r = requests.get(BOT_CSV_URL, headers=HEADERS, timeout=15)
        r.encoding = 'utf-8-sig'
        
        if r.status_code != 200:
            return {}, f"伺服器回應錯誤: {r.status_code}"

        txt = r.text
        df = pd.read_csv(io.StringIO(txt))
        
    except requests.exceptions.RequestException as e:
        return {}, f"網路請求失敗，請檢查連線或 BOT 網站。錯誤: {e}"
    except Exception as e:
        return {}, f"解析 CSV 失敗，可能格式已變動。錯誤: {e}"
    
    rates = {}
    try:
        for _, row in df.iterrows():
            cur_field = row.get('幣別') or row.get('Currency') or ''
            code = None
            if isinstance(cur_field, str):
                m = re.search(r'\((\w+)\)', cur_field) 
                if m:
                    code = m.group(1)
            
            if not code:
                code = (row.get('Currency Code') or '').strip()
            
            if not code:
                continue

            buy = _to_float(row.get('即期買入') or row.get('Spot Buy') or None)
            sell = _to_float(row.get('即期賣出') or row.get('Spot Sell') or None)
            
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
            pass

# ---------- UI 與狀態管理 ----------
st.set_page_config(page_title="即時匯率計算機", page_icon="💱", layout="wide")

# CSS 優化：移除可能造成衝突的 padding，確保按鈕能填滿欄位
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
    /* 調整字體大小與邊緣圓角 */
    font-size: 16px;
    font-weight: bold;
    border-radius: 8px;
    /* 關鍵：避免固定 padding 擠壓窄螢幕排版 */
    padding-top: 10px;
    padding-bottom: 10px;
}

/* 貨幣選擇按鈕 */
div[data-testid="column"] div.stButton > button {
    font-size: 14px;
    padding-top: 6px;
    padding-bottom: 6px;
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
    st.sidebar.error("❌ 匯率抓取失敗，請檢查 BOT 網站連線或點擊下方刷新按鈕。")
    st.sidebar.warning("⚠️ 目前使用備用匯率資料 (TWD=1, USD=32.5, JPY=0.21, EUR=35.0)")
    rates = {"TWD":1.0, "USD":32.5, "JPY":0.21, "EUR":35.0, "CNY":4.5, "HKD":4.1}
else:
    st.sidebar.success("✅ 匯率更新成功")

# 側邊欄資訊
st.sidebar.title("設定與資訊")
st.sidebar.info(f"資料來源: 台灣銀行 (BOT)\n更新時間: {st.session_state.rates_updated or time.strftime('%H:%M:%S')}")

if st.sidebar.button("🔄 強制重新抓取匯率"):
    st.cache_data.clear()
    st.session_state.rates_updated = time.strftime("%Y-%m-%d %H:%M:%S")
    safe_rerun()

st.sidebar.markdown("---")
st.sidebar.write(f"**目前記憶 (TWD)**: {format_number(st.session_state.memory)}")

# 主標題
st.title("💱 匯率計算機")

# 2. 顯示結果區
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
cols = st.columns(5)
for i, col in enumerate(cols):
    code = st.session_state.displayed[i] if i < len(st.session_state.displayed) else 'TWD'
    flag = FLAGS.get(code, '')
    btn_label = f"{flag} {code}"
    is_active = (code == st.session_state.selected)
    
    # 關鍵修正：點擊按鈕後立即處理換算邏輯
    if col.button(btn_label, 
                  key=f"cur_btn_{i}", 
                  type="primary" if is_active else "secondary", 
                  use_container_width=True):
        
        prev_code = st.session_state.selected
        if st.session_state.last != 0 and prev_code in rates and code in rates:
            val_in_twd = st.session_state.last * rates[prev_code]
            val_target = val_in_twd / rates[code]
            st.session_state.last = val_target
            st.session_state.expr = str(val_target)
        
        st.session_state.selected = code
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
    # 嘗試將整個運算式取負號
    try:
        val = safe_eval(st.session_state.expr)
        if val == 0: return
        st.session_state.expr = str(-val)
        st.session_state.last = -val
    except:
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
        st.session_state.expr = str(float(val))
    except ValueError as e:
        st.error(f"運算格式錯誤: {e}")
    except Exception:
        st.error("發生未預期運算錯誤")

# 5. 計算機按鍵佈局
st.markdown("---")

# Row M (記憶鍵)
c1, c2, c3, c4 = st.columns(4)
with c1: 
    if st.button("MC", use_container_width=True):
        st.session_state.memory = 0.0
        st.toast("記憶已清除")
with c2: 
    if st.button("MR", use_container_width=True):
        recalled = st.session_state.memory / rates.get(st.session_state.selected, 1.0)
        st.session_state.expr = str(recalled)
        st.session_state.last = recalled
with c3: 
    if st.button("M+", use_container_width=True):
        do_calculate()
        val_twd = st.session_state.last * rates.get(st.session_state.selected, 1.0)
        st.session_state.memory += val_twd
        st.toast(f"已加入記憶 (TWD: {format_number(val_twd)})")
with c4: 
    if st.button("M-", use_container_width=True):
        do_calculate()
        val_twd = st.session_state.last * rates.get(st.session_state.selected, 1.0)
        st.session_state.memory -= val_twd
        st.toast(f"已從記憶扣除")

# Row 1 (功能鍵)
r1_1, r1_2, r1_3, r1_4 = st.columns(4)
with r1_1:
    if st.button("C", type="primary", use_container_width=True): clear_all()
with r1_2:
    if st.button("⌫", use_container_width=True): backspace()
with r1_3: 
    if st.button("( )", use_container_width=True): press("(")
with r1_4:
    if st.button("÷", use_container_width=True): press("/")

# Row 2 (7, 8, 9, x)
r2_1, r2_2, r2_3, r2_4 = st.columns(4)
with r2_1: 
    if st.button("7", use_container_width=True): press("7")
with r2_2: 
    if st.button("8", use_container_width=True): press("8")
with r2_3: 
    if st.button("9", use_container_width=True): press("9")
with r2_4: 
    if st.button("×", use_container_width=True): press("*")

# Row 3 (4, 5, 6, -)
r3_1, r3_2, r3_3, r3_4 = st.columns(4)
with r3_1: 
    if st.button("4", use_container_width=True): press("4")
with r3_2: 
    if st.button("5", use_container_width=True): press("5")
with r3_3: 
    if st.button("6", use_container_width=True): press("6")
with r3_4: 
    if st.button("－", use_container_width=True): press("-")

# Row 4 (1, 2, 3, +)
r4_1, r4_2, r4_3, r4_4 = st.columns(4)
with r4_1: 
    if st.button("1", use_container_width=True): press("1")
with r4_2: 
    if st.button("2", use_container_width=True): press("2")
with r4_3: 
    if st.button("3", use_container_width=True): press("3")
with r4_4: 
    if st.button("＋", use_container_width=True): press("+")

# Row 5 (0, ., ±, =)
r5_1, r5_2, r5_3, r5_4 = st.columns(4)
with r5_1: 
    if st.button("0", use_container_width=True): press("0")
with r5_2: 
    if st.button(".", use_container_width=True): press(".")
with r5_3: 
    if st.button("±", use_container_width=True): toggle_sign()
with r5_4: 
    if st.button("＝", type="primary", use_container_width=True): do_calculate()

st.markdown("---")

# 6. 自訂貨幣列設定
with st.expander("⚙️ 自訂上方快捷貨幣列"):
    all_codes = sorted(list(rates.keys()))
    valid_defaults = [c for c in st.session_state.displayed if c in all_codes]
    
    new_selection = st.multiselect(
        "選擇最多 5 個常用貨幣", 
        options=all_codes, 
        default=valid_defaults,
        max_selections=5,
        key="currency_multiselect"
    )
    
    if st.button("更新快捷列", key="update_display"):
        final_list = []
        if 'TWD' in new_selection:
            final_list.append('TWD')
            
        for c in new_selection:
            if c != 'TWD' and len(final_list) < 5:
                 final_list.append(c)
        
        if len(final_list) < 5:
            for c in ['USD', 'JPY', 'EUR', 'CNY', 'HKD']:
                if c not in final_list and c in all_codes:
                    final_list.append(c)
                if len(final_list) >= 5: break
        
        st.session_state.displayed = final_list
        safe_rerun()

