import streamlit as st
import pandas as pd
import folium
import time
import urllib.parse
from streamlit_folium import st_folium
from datetime import datetime
from utils import get_osrm_data, gr_num

def show_driver_terminal(user_data, conn, LOG_URL, GR_TIME):
    # ΦΑΣΗ 2: SAFETY INSPECTION
    if not st.session_state.inspected:
        st.header("🛡️ Daily Safety Inspection")
        st.info(f"Όχημα: **{st.session_state.display_plate}** | Οδηγός: **{st.session_state.username}**")
        with st.container(border=True):
            c1 = st.checkbox("🛞 Πίεση & Κατάσταση Ελαστικών")
            c2 = st.checkbox("🛢️ Στάθμη Λαδιού & Ψυκτικού")
            c3 = st.checkbox("📂 Έγγραφα (Άδεια, Ασφάλεια, Κάρτα)")
            c4 = st.checkbox("💧 Στάθμη AdBlue / Καύσιμα")
            issues = st.text_area("Αναφορά Προβλήματος / Παρατηρήσεις")
            
            if st.button("🏁 Ολοκλήρωση & Εκκίνηση", type="primary", use_container_width=True):
                if c1 and c2 and c3 and c4:
                    try:
                        new_check = pd.DataFrame([{
                            "Timestamp": datetime.now(GR_TIME).strftime('%Y-%m-%d %H:%M:%S'),
                            "Driver": st.session_state.username, "Plate": st.session_state.display_plate,
                            "Status": "OK", "Issues": issues
                        }])
                        m_log = conn.read(spreadsheet=LOG_URL, worksheet="Maintenance_Log", ttl=0)
                        conn.update(spreadsheet=LOG_URL, worksheet="Maintenance_Log", data=pd.concat([m_log, new_check], ignore_index=True))
                        st.session_state.inspected = True
                        st.rerun()
                    except:
                        st.session_state.inspected = True
                        st.rerun()
                else: st.error("⚠️ Πρέπει να ελέγξετε όλα τα σημεία.")
        st.stop()

    # ΦΑΣΗ 3: ΤΑ TABS
    st.subheader(f"🚚 {st.session_state.display_plate} | {st.session_state.loading_date}")
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["🌎 Χάρτης", "🛣️ Δρομολόγηση", "📦 POD", "📊 Analytics", "📩 Alert", "🏭 Παραλαβές"])
    
    with tab3: # POD Protocol
        st.subheader("Sync POD Data")
        active_cust = st.selectbox("Πελάτης", user_data['Name'].unique())
        if st.button("⏹️ Sync POD", type="primary"):
            new_log = pd.DataFrame([{"Timestamp": datetime.now(GR_TIME).strftime('%Y-%m-%d %H:%M:%S'), "Driver": st.session_state.username, "Plate": st.session_state.display_plate, "Customer": active_cust}])
            curr_log = conn.read(spreadsheet=LOG_URL, worksheet="Log", ttl=0)
            conn.update(spreadsheet=LOG_URL, worksheet="Log", data=pd.concat([curr_log, new_log], ignore_index=True))
            st.success("POD Synchronized to Central Log!")

    with tab6: # Παραλαβές
        st.subheader("Εκκρεμείς Παραλαβές")
        try:
            all_p = conn.read(spreadsheet=LOG_URL, worksheet="Supplier_Pickups", ttl=10)
            my_p = all_p[all_p['Assigned_Plate'] == st.session_state.display_plate]
            st.dataframe(my_p)
        except: st.info("Δεν βρέθηκαν παραλαβές.")
