import streamlit as st
import datetime # Κράτα μόνο αυτό για ημερομηνίες
from datetime import timedelta, timezone # Κρίσιμο για το GR_TIME
from streamlit_gsheets import GSheetsConnection

# Custom Imports
from data_loader import load_full_data
from driver_ui import render_driver_terminal
from admin_ui import render_admin_dashboard
from utils import render_public_tracking




# --- CONFIG ---
st.set_page_config(page_title="Alumil Hub v60", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)
GR_TIME = timezone(timedelta(hours=3))

# --- LINKS ΤΩΝ GOOGLE SHEETS ---
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"
CUSADDRESS_URL = "https://docs.google.com/spreadsheets/d/1k9-gCuo_BxVezLaVoagh04xfUoXfj26aH2Mf833qGMk/edit"
DELIVERIES_URL = "https://docs.google.com/spreadsheets/d/10uKgg3AIuSnROK2-6VnY0Rm3U4vH2xv8O4OFthgaWww/edit"

# --- SESSION STATE INIT ---
if "password_correct" not in st.session_state: st.session_state.password_correct = False
if "is_logged_in" not in st.session_state: st.session_state.is_logged_in = False
if "inspected" not in st.session_state: st.session_state.inspected = False
if "user_plate" not in st.session_state: st.session_state.user_plate = None
if "loading_date" not in st.session_state: st.session_state.loading_date = None
if "username" not in st.session_state: st.session_state.username = None
if "route_data" not in st.session_state: st.session_state.route_data = []
if "route_geom" not in st.session_state: st.session_state.route_geom = None
if "start_time" not in st.session_state: st.session_state.start_time = None
if "draft_sequence" not in st.session_state: st.session_state.draft_sequence = None
if "filter_plate" not in st.session_state: st.session_state.filter_plate = "Όλα"
if "filter_date" not in st.session_state: st.session_state.filter_date = "Όλες"

# --- LOGIN SCREEN ---
def check_password():
    if st.session_state.password_correct: return True
    st.set_page_config(initial_sidebar_state="expanded") if not st.session_state.password_correct else None
    st.title("🔐 Alumil Secure Login")
    pwd = st.text_input("Προσωπικός Κωδικός", type="password")
    if st.button("Είσοδος", use_container_width=True):
        if "passwords" in st.secrets and pwd in st.secrets["passwords"]:
            st.session_state.password_correct = True
            st.session_state.username = st.secrets["passwords"][pwd]
            st.rerun()
    return False

def main():
    # 1. Live Tracking Mode (Bypass)
    params = st.query_params
    if "track" in params:
        render_public_tracking(params["track"], conn, LOG_URL)
        st.stop()

    # 2. Login
    if not check_password():
        st.stop()
    
    # 3. Φόρτωση Δεδομένων
    fleet_info, all_data = load_full_data(conn, SHIPMENTS_URL, DELIVERIES_URL, CUSADDRESS_URL, COORDS_URL)
    
    # --- SIDEBAR ---
    with st.sidebar:
        col1, col2 = st.columns([1, 1])
        if col1.button("🔒 Log Out"):
            st.session_state.password_correct = False
            st.rerun()
        
        import datetime
        now_gr = datetime.datetime.now(GR_TIME)
        col2.write(f"📅 {now_gr.strftime('%d/%m/%Y')}")
        
        st.markdown("---")
        app_mode = st.radio("📑 Μενού", ["🚛 Driver Terminal", "📊 Admin Dashboard"])
        
        st.markdown("---")
        st.subheader("🔍 Φιλτράρισμα")

        
        
        # Καθαρό φίλτρο πινακίδας
        all_plates = ["Όλα"] + sorted([str(p) for p in fleet_info['Truck License Plate'].unique()])
        
        # Χρησιμοποιούμε το index για να κρατάμε την επιλογή σωστά
        if st.session_state.filter_plate not in all_plates:
            st.session_state.filter_plate = "Όλα"
            
        selected_plate = st.selectbox(
            "Επιλογή Πινακίδας", 
            options=all_plates,
            index=all_plates.index(st.session_state.filter_plate)
        )
        st.session_state.filter_plate = selected_plate

    # --- ROUTING ---
    if app_mode == "🚛 Driver Terminal":
        # Στέλνουμε το ΜΑΜΑ data, χωρίς φίλτρα εδώ. 
        # Το driver_ui θα διαβάσει το st.session_state.filter_plate και θα κάνει τα δικά του.
        render_driver_terminal(all_data, fleet_info, conn, LOG_URL, CUSADDRESS_URL, GR_TIME)
    else:
        render_admin_dashboard(all_data, conn, LOG_URL)


# --- ΕΚΤΕΛΕΣΗ ΤΗΣ ΜΑΙΝ ---
if __name__ == "__main__":
    main()

