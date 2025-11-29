# streamlit_app.py
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
CACHE_TTL = 10 * 60  # 10 分鐘快取

FLAGS = {
    "TWD": "🇹🇼", "USD": "🇺🇸", "JPY": "🇯🇵", "EUR": "🇪🇺", "CNY": "🇨🇳",
    "HKD": "🇭🇰", "GBP": "🇬🇧", "AUD": "🇦🇺", "SGD": "🇸🇬", "KRW": "🇰🇷"
}

# ---------- 工具函式 ----------
def format_number(n):
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

ALLOWED_OPERATORS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.USub: op.neg, ast.UAdd: op.pos
}

def safe_eval(expr: str):
    def _eval(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("不支援的常數類型")
        if isinstance(node, ast.Num):
            return node.n
        if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_OPERATORS:
            return ALLOWED_OPERATORS[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_OPERATORS:
            return ALLOWED_OPERATORS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        raise ValueError("不支援的運算")
    node = ast.parse(expr, mode='eval')
    return _eval(node)

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
    rates_dict: { 'USD': 31.2, ... } 或空 dict
    error_message: None 或字串
    """
    try:
        r = requests.get(BOT_CSV_URL, timeout=12)
        r.encoding = 'utf-8-sig'  # 處理 BOM
        txt = r.text
        df = pd.read_csv(io.StringIO(txt))
    except Exception as e:
        return {}, f"抓取 BOT 匯率失敗: {e}"
    rates = {}
    try:
        for _, row in df.iterrows():
            cur_field = row.get('幣別') or row.get('Currency') or ''
            m = None
            if isinstance(cur_field, str):
                m = re.search(r'`\((\w+)\)`', cur_field)
            code = m.group(1) if m else (row.get('Currency Code') or '').strip()
            if not code:
                continue
            buy = _to_float(row.get('即期買入') or row.get('Spot Buy') or None)
            sell = _to_float(row.get('即期賣出') or row.get('Spot Sell') or None)
            val = None
            if buy is not None and sell is not None:
                val = (buy + sell) / 2.0
            elif sell is not None:
                val = sell
            elif buy is not None:
                val = buy
            if val is not None:
                rates[code] = val
    except Exception as e:
        return {}, f"解析 CSV 失敗: {e}"
    rates['TWD'] = 1.0
    return rates, None

# ---------- Streamlit UI 與狀態管理 ----------
st.set_page_config(page_title="匯率計算機", layout="wide")
# 少量 CSS 改善窄螢幕顯示
st.markdown("""
<style>
/* 讓按鈕字體小一點、按鈕間距更緊湊 */
.stButton>button { padding: 6px 8px; font-size: 14px; }
div.row-widget.stRadio > label { font-size:14px; }
</style>
""", unsafe_allow_html=True)

st.title("匯率計算機（Streamlit）")

# session state 初始
if 'expr' not in st.session_state: st.session_state.expr = ''
if 'last' not in st.session_state: st.session_state.last = 0.0
if 'memory' not in st.session_state: st.session_state.memory = 0.0
if 'displayed' not in st.session_state:
    st.session_state.displayed = ['TWD', 'USD', 'JPY', 'EUR', 'CNY']
if 'selected' not in st.session_state: st.session_state.selected = 'TWD'
if 'rates_updated' not in st.session_state: st.session_state.rates_updated = ''

# 取得匯率（回傳可能是 (rates, error) 或 {}）
rates_result = fetch_rates()
# fetch_rates 使用 st.cache_data 回傳 (rates, error) 或 {}，兼容性處理：
if isinstance(rates_result, tuple):
    rates, fetch_err = rates_result
else:
    # 舊版或 fallback 回傳 dict only
    rates = rates_result if isinstance(rates_result, dict) else {}
    fetch_err = None if rates else "無匯率資料"

# 顯示匯率狀態在 sidebar，方便除錯
st.sidebar.markdown("**匯率來源**: BOT 匯率 CSV")
if fetch_err:
    st.sidebar.error(fetch_err)
    st.sidebar.write("使用 fallback 或暫無資料")
else:
    st.sidebar.success("匯率抓取成功")
st.sidebar.write("最後更新（本地快取時間）:", st.session_state.rates_updated or time.strftime("%Y-%m-%d %H:%M:%S"))
st.sidebar.write("可用幣別：", ", ".join(sorted(list(rates.keys()))[:20]))

# 手動刷新匯率
def refresh_rates():
    try:
        st.cache_data.clear()
    except Exception:
        try:
            fetch_rates.clear()
        except Exception:
            pass
    st.session_state.rates_updated = time.strftime("%Y-%m-%d %H:%M:%S")
    st.experimental_rerun()

if st.sidebar.button("重新抓取匯率"):
    refresh_rates()

# 若沒抓到匯率，顯示錯誤並用 fallback
if not rates:
    st.error("目前無法取得匯率資料，已使用內建 fallback。請按側邊欄「重新抓取匯率」或稍後再試。")
    rates = {"TWD":1.0, "USD":31.2, "JPY":0.22, "EUR":33.5, "CNY":4.5}

# 畫面：運算式與結果
left, right = st.columns([2,1])
with left:
    st.markdown("**運算式**")
    st.text(st.session_state.expr or "0")
    st.markdown("**結果**")
    display_val = st.session_state.last
    st.subheader(f"{format_number(display_val)} {st.session_state.selected}")

# 第一排五個國家按鍵（縮小寬度）
cols = st.columns(5)
for i, col in enumerate(cols):
    if i >= len(st.session_state.displayed):
        code = 'TWD'
    else:
        code = st.session_state.displayed[i] or 'TWD'
    flag = FLAGS.get(code, '')
    is_active = (code == st.session_state.selected)
    btn_label = f"{flag} {code}"
    if is_active:
        if col.button(btn_label, key=f"cur_{i}", help="已選擇"):
            pass
    else:
        if col.button(btn_label, key=f"cur_{i}"):
            prev = st.session_state.selected
            if st.session_state.expr != '' and st.session_state.last != 0:
                if prev in rates and code in rates:
                    twd = st.session_state.last * rates[prev]
                    converted = twd / rates[code]
                    st.session_state.selected = code
                    st.session_state.last = converted
                    st.session_state.expr = str(converted)
                else:
                    st.error("匯率資料不足，無法換算")
            else:
                st.session_state.selected = code
            st.experimental_rerun()

# 計算機按鍵功能
def press(ch):
    st.session_state.expr = st.session_state.expr + ch

def backspace():
    st.session_state.expr = st.session_state.expr[:-1]

def clear_all():
    st.session_state.expr = ''
    st.session_state.last = 0.0

def toggle_sign():
    m = st.session_state.expr
    if m == '':
        st.session_state.expr = '-'
    else:
        match = re.search(r'(-?\d+\.?\d*)$', m)
        if match:
            num = match.group(1)
            toggled = num[1:] if num.startswith('-') else '-' + num
            st.session_state.expr = m[:-len(num)] + toggled
        else:
            st.session_state.expr = '-' + m

def do_calculate():
    s = st.session_state.expr.strip()
    if s == '':
        st.session_state.last = 0.0
        return
    s2 = re.sub(r'[^0-9+\-*/().]', '', s)
    try:
        val = safe_eval(s2)
        st.session_state.last = float(val)
    except Exception:
        st.error("運算錯誤，請檢查輸入")

# 按鍵列（使用 columns 排版）
r1 = st.columns([1,1,1,1])
with r1[0]:
    if st.button("("): press("(")
with r1[1]:
    if st.button(")"): press(")")
with r1[2]:
    if st.button("⌫"): backspace()
with r1[3]:
    if st.button("C"): clear_all()

r2 = st.columns([1,1,1,1])
with r2[0]:
    if st.button("7"): press("7")
with r2[1]:
    if st.button("8"): press("8")
with r2[2]:
    if st.button("9"): press("9")
with r2[3]:
    if st.button("÷"): press("/")

r3 = st.columns([1,1,1,1])
with r3[0]:
    if st.button("4"): press("4")
with r3[1]:
    if st.button("5"): press("5")
with r3[2]:
    if st.button("6"): press("6")
with r3[3]:
    if st.button("×"): press("*")

r4 = st.columns([1,1,1,1])
with r4[0]:
    if st.button("1"): press("1")
with r4[1]:
    if st.button("2"): press("2")
with r4[2]:
    if st.button("3"): press("3")
with r4[3]:
    if st.button("-"): press("-")

r5 = st.columns([1,1,1,1])
with r5[0]:
    if st.button("0"): press("0")
with r5[1]:
    if st.button("."): press(".")
with r5[2]:
    if st.button("±"): toggle_sign()
with r5[3]:
    if st.button("+"): press("+")

r6 = st.columns([1,2])
with r6[0]:
    if st.button("="): do_calculate()
with r6[1]:
    if st.button("Ans→Expr"): st.session_state.expr = str(st.session_state.last)

# 記憶鍵
mcols = st.columns(4)
with mcols[0]:
    if st.button("M+"):
        do_calculate()
        st.session_state.memory += st.session_state.last * rates.get(st.session_state.selected, 1.0)
        st.success("已加入記憶")
with mcols[1]:
    if st.button("M-"):
        do_calculate()
        st.session_state.memory -= st.session_state.last * rates.get(st.session_state.selected, 1.0)
        st.success("已從記憶扣除")
with mcols[2]:
    if st.button("MR"):
        recalled = st.session_state.memory / rates.get(st.session_state.selected, 1.0)
        st.session_state.expr = str(recalled)
        st.session_state.last = recalled
with mcols[3]:
    if st.button("MC"):
        st.session_state.memory = 0.0
        st.success("已清除記憶")

st.markdown("---")
st.markdown("**國家選單（替換除 TWD 外的四個國家）**")

# 準備 options（確保都是 str），並排序
all_codes = sorted([str(k) for k in rates.keys()])

# 確保 session_state.displayed 也都是 str 並過濾
st.session_state.displayed = [str(x) for x in st.session_state.displayed]
default_displayed = [c for c in st.session_state.displayed if c in all_codes]
if 'TWD' not in default_displayed:
    default_displayed.insert(0, 'TWD')
if len(default_displayed) > len(all_codes):
    default_displayed = default_displayed[:len(all_codes)]

sel = st.multiselect("選擇最多 5 個（包含 TWD）", options=all_codes, default=default_displayed)

if st.button("套用選單"):
    if 'TWD' not in sel:
        sel.insert(0, 'TWD')
    new_disp = []
    if 'TWD' in sel:
        new_disp.append('TWD')
    for c in sel:
        if c == 'TWD': continue
        if len(new_disp) >= 5: break
        new_disp.append(c)
    if len(new_disp) < 5:
        for c in all_codes:
            if c not in new_disp:
                new_disp.append(c)
            if len(new_disp) >= 5: break
    st.session_state.displayed = new_disp[:5]
    st.experimental_rerun()

st.markdown("---")
st.caption("提示：第一排按鍵點選貨幣可切換顯示；若已有計算結果，點選不同貨幣會立即換算。")
st.sidebar.markdown(f"記憶（TWD）: {format_number(st.session_state.memory)}")
