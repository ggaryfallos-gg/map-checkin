import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from streamlit_gsheets import GSheetsConnection

# CUSTOM MODULES
from utils import clean_val, gr_num
from driver_terminal import show_driver_terminal
from admin_dashboard import show_admin_dashboard

# --- CONFIG ---
st.set_page_config(page_title="Alumil Hub v58", layout="wide", initial_sidebar_state="collapsed")
conn = st.connection("gsheets", type=GSheetsConnection)
GR_TIME = timezone(timedelta(hours=3))

SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"

# --- SESSION STATE ---
if "password_correct" not in st.session_state:
    st.session_state.update({"password_correct": False, "is_logged_in": False, "inspected": False, "user_plate": None, "username": None})

# --- LOGIN ---
if not st.session_state.password_correct:
    st.title("🔐 Alumil Secure Login")
    pwd = st.text_input("Κωδικός", type="password")
    if st.button("Είσοδος"):
        if pwd in st.secrets["passwords"]:
            st.session_state.password_correct = True
            st.session_state.username = st.secrets["passwords"][pwd]
            st.rerun()
    st.stop()

# --- MAIN APP ---
@st.cache_data(ttl=300)
def load_data():
    df = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    df.columns = df.columns.str.strip()
    df['Plate_Clean'] = df['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    for c in ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']:
        if c in df.columns: df[c] = df[c].apply(clean_val)
    return df

all_data = load_data()
app_mode = st.sidebar.radio("Μενού", ["🚛 Driver Terminal", "📊 Admin Dashboard"])

if app_mode == "🚛 Driver Terminal":
    if st.session_state.user_plate is None:
        st.title("Επιλογή Δρομολογίου")
        plates = sorted(all_data['Truck License Plate'].dropna().unique().tolist())
        p_sel = st.selectbox("🚚 Φορτηγό", plates)
        if st.button("🚀 Έναρξη"):
            st.session_state.user_plate = p_sel
            st.session_state.display_plate = p_sel
            st.session_state.loading_date = "Today"
            st.rerun()
    else:
        # ΚΛΗΣΗ ΤΟΥ DRIVER MODULE
        show_driver_terminal(all_data, conn, LOG_URL, GR_TIME)

elif app_mode == "📊 Admin Dashboard":
    # ΚΛΗΣΗ ΤΟΥ ADMIN MODULE
    show_admin_dashboard(all_data, conn, LOG_URL)
