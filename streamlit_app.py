# streamlit_app_part1.py
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

# emoji 國旗（可改成圖檔）
FLAGS = {
    "TWD": "🇹🇼", "USD": "🇺🇸", "JPY": "🇯🇵", "EUR": "🇪🇺", "CNY": "🇨🇳",
    "HKD": "🇭🇰", "GBP": "🇬🇧", "AUD": "🇦🇺", "SGD": "🇸🇬", "KRW": "🇰🇷"
}

# ---------- 工具函式（先定義，避免 NameError） ----------
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

# ---------- 安全運算 evaluate（使用 ast） ----------
ALLOWED_OPERATORS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.USub: op.neg, ast.UAdd: op.pos
}

def safe_eval(expr: str):
    """
    Evaluate a numeric expression safely using ast.
    支援 + - * / ( ) 與一元正負號
    """
    def _eval(node):
        if isinstance(node, ast.Constant):  # Python 3.8+
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("不支援的常數類型")
        if isinstance(node, ast.Num):  # older versions
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

# ---------- 取得並解析 BOT CSV（快取） ----------
@st.cache_data(ttl=CACHE_TTL)
def fetch_rates():
    """
    取得 BOT CSV，解析成 dict: rates[code] = TWD per 1 unit
    若抓取或解析失敗，回傳一組 fallback rates
    """
    try:
        r = requests.get(BOT_CSV_URL, timeout=12)
        r.encoding = 'utf-8'
        txt = r.text
        df = pd.read_csv(io.StringIO(txt))
    except Exception:
        return {"TWD": 1.0, "USD": 31.2, "JPY": 0.22, "EUR": 33.5, "CNY": 4.5}
    rates = {}
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
    rates['TWD'] = 1.0
    return rates

def _to_float(x):
    if pd.isna(x):
        return None
    try:
        return float(str(x).replace(',', ''))
    except Exception:
        return None
# streamlit_app_part2.py
# 把上半段與下半段合併成一個檔案 streamlit_app.py 使用
st.set_page_config(page_title="匯率計算機", layout="centered")
st.title("匯率計算機（Streamlit）")

# session state 初始
if 'expr' not in st.session_state:
    st.session_state.expr = ''
if 'last' not in st.session_state:
    st.session_state.last = 0.0
if 'memory' not in st.session_state:
    st.session_state.memory = 0.0  # 存 TWD
if 'displayed' not in st.session_state:
    st.session_state.displayed = ['TWD', 'USD', 'JPY', 'EUR', 'CNY']  # index0 固定 TWD
if 'selected' not in st.session_state:
    st.session_state.selected = 'TWD'
if 'rates_updated' not in st.session_state:
    st.session_state.rates_updated = ''

# 取得匯率（快取）
rates = fetch_rates()
# 設定快取時間顯示（若 fetch_rates 成功，st.cache_data 會管理 TTL）
if not st.session_state.rates_updated:
    st.session_state.rates_updated = time.strftime("%Y-%m-%d %H:%M:%S")

# 手動刷新匯率按鈕（會清除 cache 並重新抓）
def refresh_rates():
    try:
        st.cache_data.clear()
    except Exception:
        try:
            fetch_rates.clear()
        except Exception:
            pass
    _ = fetch_rates()
    st.session_state.rates_updated = time.strftime("%Y-%m-%d %H:%M:%S")
    st.experimental_rerun()

st.sidebar.markdown("**匯率來源**: BOT 匯率 CSV")
st.sidebar.write("最後更新（本地快取時間）:", st.session_state.rates_updated)
st.sidebar.button("重新抓取匯率", on_click=refresh_rates)

# 畫面：顯示運算式與結果（以 selected 幣別顯示）
st.markdown("**運算式**")
st.text(st.session_state.expr or "0")
st.markdown("**結果**")
display_val = st.session_state.last
st.subheader(f"{format_number(display_val)} {st.session_state.selected}")

# 第一排五個國家按鍵（縮小寬度）
cols = st.columns(5)
for i, col in enumerate(cols):
    # 保證 displayed 長度至少 5
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
                    st.error("匯率資料不足")
            else:
                st.session_state.selected = code
            st.experimental_rerun()

# 計算機按鍵功能（簡潔實作）
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

# 按鍵列
r1 = st.columns([1, 1, 1, 1])
with r1[0]:
    if st.button("("):
        press("(")
with r1[1]:
    if st.button(")"):
        press(")")
with r1[2]:
    if st.button("⌫"):
        backspace()
with r1[3]:
    if st.button("C"):
        clear_all()

r2 = st.columns([1, 1, 1, 1])
with r2[0]:
    if st.button("7"):
        press("7")
with r2[1]:
    if st.button("8"):
        press("8")
with r2[2]:
    if st.button("9"):
        press("9")
with r2[3]:
    if st.button("÷"):
        press("/")

r3 = st.columns([1, 1, 1, 1])
with r3[0]:
    if st.button("4"):
        press("4")
with r3[1]:
    if st.button("5"):
        press("5")
with r3[2]:
    if st.button("6"):
        press("6")
with r3[3]:
    if st.button("×"):
        press("*")

r4 = st.columns([1, 1, 1, 1])
with r4[0]:
    if st.button("1"):
        press("1")
with r4[1]:
    if st.button("2"):
        press("2")
with r4[2]:
    if st.button("3"):
        press("3")
with r4[3]:
    if st.button("-"):
        press("-")

r5 = st.columns([1, 1, 1, 1])
with r5[0]:
    if st.button("0"):
        press("0")
with r5[1]:
    if st.button("."):
        press(".")
with r5[2]:
    if st.button("±"):
        toggle_sign()
with r5[3]:
    if st.button("+"):
        press("+")

r6 = st.columns([1, 2])
with r6[0]:
    if st.button("="):
        do_calculate()
with r6[1]:
    if st.button("Ans→Expr"):
        st.session_state.expr = str(st.session_state.last)

# 記憶鍵（以 TWD 為記憶基準）
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

# 確保 session_state.displayed 也都是 str
st.session_state.displayed = [str(x) for x in st.session_state.displayed]

# 過濾 displayed，只保留在 all_codes 中的項目（避免 default 包含不存在的 code）
default_displayed = [c for c in st.session_state.displayed if c in all_codes]

# 強制 TWD 在第一位
if 'TWD' not in default_displayed:
    default_displayed.insert(0, 'TWD')

# 若 default 超過 options（理論上不會），再截斷到 options 長度
if len(default_displayed) > len(all_codes):
    default_displayed = default_displayed[:len(all_codes)]

# multiselect（限制使用者選擇）
sel = st.multiselect(
    "選擇最多 5 個（包含 TWD）",
    options=all_codes,
    default=default_displayed
)

if st.button("套用選單"):
    # 確保 TWD 在選單中
    if 'TWD' not in sel:
        sel.insert(0, 'TWD')
    # 只取前 5
    new_disp = []
    if 'TWD' in sel:
        new_disp.append('TWD')
    for c in sel:
        if c == 'TWD':
            continue
        if len(new_disp) >= 5:
            break
        new_disp.append(c)
    # 若不足 5，補其他
    if len(new_disp) < 5:
        for c in all_codes:
            if c not in new_disp:
                new_disp.append(c)
            if len(new_disp) >= 5:
                break
    st.session_state.displayed = new_disp[:5]
    st.experimental_rerun()

st.markdown("---")
st.caption("提示：在第一排按鍵點選貨幣可切換顯示；若已有計算結果，點選不同貨幣會立即換算。")

# 顯示記憶值（TWD）
st.sidebar.markdown(f"記憶（TWD）: {format_number(st.session_state.memory)}")
