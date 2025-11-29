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

# 偽裝成瀏覽器的 Header，避免被 BOT 阻擋
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

FLAGS = {
    "TWD": "🇹🇼", "USD": "🇺🇸", "JPY": "🇯🇵", "EUR": "🇪🇺", "CNY": "🇨🇳",
    "HKD": "🇭🇰", "GBP": "🇬🇧", "AUD": "🇦🇺", "SGD": "🇸🇬", "KRW": "🇰🇷",
    "CAD": "🇨🇦", "CHF": "🇨🇭", "ZAR": "🇿🇦", "SEK": "🇸🇪", "NZD": "🇳🇿",
    "THB": "🇹🇭", "PHP": "🇵🇭", "IDR": "🇮🇩", "VND": "🇻🇳", "MYR": "🇲🇾"
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
    使用 ast.parse 進行安全的數學運算評估，避免使用危險的 eval()
    """
    def _eval(node):
        if isinstance(node, ast.Constant): # Python 3.8+
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("不支援的常數類型")
        if isinstance(node, ast.Num): # Python < 3.8
            return node.n
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
    若成功： (rates, None)
    若失敗： ({}, "錯誤訊息")
    """
    try:
        r = requests.get(BOT_CSV_URL, headers=HEADERS, timeout=15)
        r.encoding = 'utf-8-sig'  # 處理 Excel 常見的 BOM
        
        # 檢查狀態碼
        if r.status_code != 200:
            return {}, f"伺服器回應錯誤: {r.status_code}"

        txt = r.text
        df = pd.read_csv(io.StringIO(txt))
    except Exception as e:
        return {}, f"抓取 BOT 匯率失敗: {e}"
    
    rates = {}
    try:
        for _, row in df.iterrows():
            # 嘗試抓取不同欄位名稱（因應 CSV 格式可能變動）
            cur_field = row.get('幣別') or row.get('Currency') or ''
            
            # 解析幣別代碼 (例如 "USD")
            code = None
            if isinstance(cur_field, str):
                m = re.search(r'\((\w+)\)', cur_field) # 尋找括號內的代碼
                if m:
                    code = m.group(1)
            
            if not code:
                code = (row.get('Currency Code') or '').strip()
            
            if not code:
                continue

            # 抓取買入與賣出價
            buy = _to_float(row.get('即期買入') or row.get('Spot Buy') or None)
            sell = _to_float(row.get('即期賣出') or row.get('Spot Sell') or None)
            
            # 簡單平均作為參考匯率
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
        return {}, f"解析 CSV 失敗: {e}"
    
    rates['TWD'] = 1.0
    return rates, None

# ---------- safe rerun（相容性處理） ----------
def safe_rerun():
    """嘗試執行 rerun，相容不同版本的 Streamlit"""
    try:
        st.rerun()
    except AttributeError:
        try:
            st.experimental_rerun()
        except AttributeError:
            st.session_state._need_rerun = True

# ---------- UI 與狀態管理 ----------
st.set_page_config(page_title="即時匯率計算機", page_icon="💱", layout="wide")

# CSS 優化：針對手機介面調整按鈕大小與間距
st.markdown("""
<style>
/* 全域調整 */
.stApp { margin-top: -20px; }

/* 計算機按鍵樣式 */
div.stButton > button {
    width: 100%;
    padding: 15px 0px;
    font-size: 18px;
    font-weight: bold;
    border-radius: 8px;
    transition: all 0.2s;
}

/* 貨幣選擇按鈕稍微小一點 */
div[data-testid="column"] div.stButton > button {
    padding: 8px 0px;
    font-size: 14px;
}

/* 結果顯示區塊 */
.result-box {
    background-color: #f0f2f6;
    padding: 15px;
    border-radius: 10px;
    text-align: right;
    margin-bottom: 10px;
}
.result-expr { font-size: 1.2rem; color: #666; font-family: monospace; min-height: 1.5rem; }
.result-val { font-size: 2.5rem; font-weight: bold; color: #333; }
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

# 側邊欄資訊
st.sidebar.title("設定與資訊")
if fetch_err:
    st.sidebar.error(f"⚠️ {fetch_err}")
    st.sidebar.warning("目前使用備用匯率資料")
    # Fallback data
    if not rates:
        rates = {"TWD":1.0, "USD":32.5, "JPY":0.21, "EUR":35.0, "CNY":4.5, "HKD":4.1}
else:
    st.sidebar.success("✅ 匯率更新成功")

st.sidebar.info(f"資料來源: 台灣銀行 (BOT)\n更新時間: {st.session_state.rates_updated or time.strftime('%H:%M:%S')}")

if st.sidebar.button("🔄 強制重新抓取"):
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
cols = st.columns(5)
for i, col in enumerate(cols):
    # 確保不超出範圍
    code = st.session_state.displayed[i] if i < len(st.session_state.displayed) else 'TWD'
    flag = FLAGS.get(code, '')
    btn_label = f"{flag} {code}"
    
    # 檢查是否為當前選中
    is_active = (code == st.session_state.selected)
    
    if col.button(btn_label, key=f"cur_btn_{i}", type="primary" if is_active else "secondary"):
        # 如果切換貨幣，且已有數值，進行匯率換算
        prev_code = st.session_state.selected
        if st.session_state.last != 0 and prev_code in rates and code in rates:
            # 邏輯：先換回 TWD，再換成目標幣別
            val_in_twd = st.session_state.last * rates[prev_code]
            val_target = val_in_twd / rates[code]
            st.session_state.last = val_target
            # 清空運算式，因為數值已經變了，不再對應原本的算式
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
    # 簡單的正負號切換
    if not st.session_state.expr:
        st.session_state.expr = '-'
        return
    
    # 嘗試解析最後一個數字並反轉
    # 這裡做簡單處理：如果整個式子可以轉數字，直接乘 -1，否則加負號
    try:
        val = float(st.session_state.expr)
        if val > 0:
            st.session_state.expr = f"-{st.session_state.expr}"
        else:
            st.session_state.expr = st.session_state.expr.lstrip('-')
    except:
        st.session_state.expr += '-'

def do_calculate():
    s = st.session_state.expr.strip()
    if not s: return
    
    # 過濾非法字元，只留數字和運算符
    s_clean = re.sub(r'[^0-9+\-*/().]', '', s)
    try:
        val = safe_eval(s_clean)
        st.session_state.last = float(val)
        # 計算後，將結果變為新的運算式起點（可選，這裡選擇清空 expr 保留 last）
        # st.session_state.expr = str(val) 
    except Exception:
        st.error("運算格式錯誤")

# 5. 計算機按鍵佈局
# Row 1
c1, c2, c3, c4 = st.columns(4)
with c1: 
    if st.button("MC"): 
        st.session_state.memory = 0.0
        st.toast("記憶已清除")
with c2: 
    if st.button("MR"):
        # 從 TWD 記憶換算回當前幣別
        val = st.session_state.memory / rates.get(st.session_state.selected, 1.0)
        st.session_state.expr += str(val)
with c3: 
    if st.button("M+"):
        do_calculate() # 先算當前值
        val_twd = st.session_state.last * rates.get(st.session_state.selected, 1.0)
        st.session_state.memory += val_twd
        st.toast(f"已加入記憶 (TWD: {format_number(val_twd)})")
with c4: 
    if st.button("M-"):
        do_calculate()
        val_twd = st.session_state.last * rates.get(st.session_state.selected, 1.0)
        st.session_state.memory -= val_twd
        st.toast(f"已從記憶扣除")

st.markdown("---")

# Row 2 (Clear, Back, %, /)
r2_1, r2_2, r2_3, r2_4 = st.columns(4)
with r2_1:
    if st.button("C", type="primary"): clear_all()
with r2_2:
    if st.button("⌫"): backspace()
with r2_3: 
    if st.button("( )"): 
        # 簡單括號邏輯
        if "(" in st.session_state.expr and not st.session_state.expr.endswith(")"):
            press(")")
        else:
            press("(")
with r2_4:
    if st.button("÷"): press("/")

# Row 3 (7, 8, 9, x)
r3_1, r3_2, r3_3, r3_4 = st.columns(4)
with r3_1: st.button("7", on_click=press, args=("7",))
with r3_2: st.button("8", on_click=press, args=("8",))
with r3_3: st.button("9", on_click=press, args=("9",))
with r3_4: st.button("×", on_click=press, args=("*",))

# Row 4 (4, 5, 6, -)
r4_1, r4_2, r4_3, r4_4 = st.columns(4)
with r4_1: st.button("4", on_click=press, args=("4",))
with r4_2: st.button("5", on_click=press, args=("5",))
with r4_3: st.button("6", on_click=press, args=("6",))
with r4_4: st.button("－", on_click=press, args=("-",))

# Row 5 (1, 2, 3, +)
r5_1, r5_2, r5_3, r5_4 = st.columns(4)
with r5_1: st.button("1", on_click=press, args=("1",))
with r5_2: st.button("2", on_click=press, args=("2",))
with r5_3: st.button("3", on_click=press, args=("3",))
with r5_4: st.button("＋", on_click=press, args=("+",))

# Row 6 (0, ., ±, =)
r6_1, r6_2, r6_3, r6_4 = st.columns(4)
with r6_1: st.button("0", on_click=press, args=("0",))
with r6_2: st.button(".", on_click=press, args=(".",))
with r6_3: 
    if st.button("±"): toggle_sign()
with r6_4: 
    if st.button("＝", type="primary"): do_calculate()

# 6. 自訂貨幣列設定
with st.expander("⚙️ 自訂上方快捷貨幣列"):
    all_codes = sorted(list(rates.keys()))
    # 確保預設值存在於選項中
    valid_defaults = [c for c in st.session_state.displayed if c in all_codes]
    
    new_selection = st.multiselect(
        "選擇 5 個常用貨幣", 
        options=all_codes, 
        default=valid_defaults,
        max_selections=5
    )
    
    if st.button("更新快捷列"):
        # 補滿 5 個 (如果選不夠)
        if len(new_selection) < 5:
            for c in ['TWD', 'USD', 'JPY', 'EUR', 'CNY']:
                if c not in new_selection and c in all_codes:
                    new_selection.append(c)
                if len(new_selection) >= 5: break
        
        st.session_state.displayed = new_selection
        safe_rerun()

