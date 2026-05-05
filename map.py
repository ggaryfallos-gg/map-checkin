import streamlit as st
import pandas as pd
import os
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium
import folium
from geopy.geocoders import Nominatim
from datetime import datetime

# --- App Config ---
st.set_page_config(page_title="Aluminium Logistics Hub", layout="wide")
LOG_FILE = "checkin_log.txt"
geolocator = Nominatim(user_agent="alumil_logistics_v2")

# --- INITIALIZE SESSION STATE ---
if 'user_plate' not in st.session_state:
    st.session_state.user_plate = None
if 'display_plate' not in st.session_state:
    st.session_state.display_plate = None

# --- Data Loading ---
@st.cache_data
def load_data():
    plates = pd.read_excel('PLATES.xlsx', engine='openpyxl')
    shipments = pd.read_excel('shipments.xlsx', engine='openpyxl')
    
    # Deep Clean: Remove all whitespace and normalize to upper case
    shipments['Truck License Plate_Clean'] = shipments['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    plates['PLATE NUMBER_Clean'] = plates['PLATE NUMBER'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    
    return plates, shipments

plates_df, shipments_df = load_data()

# --- SIDEBAR: Ad Hoc Plate Control ---
with st.sidebar:
    st.header("⚙️ App Controls")
    if st.session_state.user_plate:
        st.write(f"Active Vehicle: **{st.session_state.display_plate}**")
        if st.button("🔄 Change Plate / Logout"):
            st.session_state.user_plate = None
            st.session_state.display_plate = None
            st.rerun()
    else:
        st.info("No vehicle selected.")

# --- LOGIN / INITIAL SELECTION ---
if st.session_state.user_plate is None:
    st.title("🚛 Vehicle Selection")
    selected_display_plate = st.selectbox("Select Plate Number to Initialize", plates_df['PLATE NUMBER'])
    
    if st.button("Proceed to Dispatch"):
        cleaned_version = str(selected_display_plate).replace(" ", "").upper()
        st.session_state.user_plate = cleaned_version
        st.session_state.display_plate = selected_display_plate
        st.rerun()
else:
    # --- MAIN TERMINAL ---
    plate_clean = st.session_state.user_plate
    user_shipments = shipments_df[shipments_df['Truck License Plate_Clean'] == plate_clean]
    
    if user_shipments.empty:
        st.error(f"No shipments found for plate {st.session_state.display_plate}")
        if st.button("Back to Menu"):
            st.session_state.user_plate = None
            st.rerun()
    else:
        # TABS: Overview Map vs. Unloading Terminal
        tab1, tab2 = st.tabs(["🌎 Route Overview", "📦 Unloading Terminal"])

        with tab1:
            st.subheader(f"All Destinations for {st.session_state.display_plate}")
            
            # Map with all unique cities for this truck
            cities = user_shipments['City'].unique()
            overview_map = folium.Map(location=[38.2749, 23.8103], zoom_start=6) # Centered on Greece
            
            for city in cities:
                try:
                    loc = geolocator.geocode(f"{city}, Greece")
                    if loc:
                        folium.Marker(
                            [loc.latitude, loc.longitude],
                            popup=f"Destination: {city}",
                            tooltip=city,
                            icon=folium.Icon(color="blue", icon="building", prefix="fa")
                        ).add_to(overview_map)
                except:
                    continue
            
            st_folium(overview_map, width="100%", height=500)

        with tab2:
            st.subheader("Select Delivery Point")
            customer_list = sorted(user_shipments['Name'].unique())
            selected_customer = st.selectbox("Current Customer Site", customer_list)
            
            customer_data = user_shipments[user_shipments['Name'] == selected_customer]
            
            # Metrics
            m = customer_data[['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories', 'Rest KG']].sum()
            profiles_sum = m['Unpainted'] + m['White'] + m['Colored']
            
            c1, c2 = st.columns(2)
            c1.metric("Profiles Weight", f"{profiles_sum:.2f} KG")
            c2.metric("Accessories Weight", f"{m['Accessories']:.2f} KG")

            # Check-in Logic with GPS
            st.divider()
            gps = get_geolocation()
            
            col_btn1, col_btn2 = st.columns(2)
            if col_btn1.button("▶️ Start Unloading", use_container_width=True):
                st.session_state.start_time = datetime.now()
                st.toast("Start time logged.")

            if col_btn2.button("⏹️ Finish & Record", use_container_width=True):
                if 'start_time' in st.session_state:
                    end_time = datetime.now()
                    duration = (end_time - st.session_state.start_time).total_seconds() / 60
                    
                    log_entry = (
                        f"{end_time} | {plate_clean} | {selected_customer} | "
                        f"PROF: {profiles_sum}kg | ACC: {m['Accessories']}kg | "
                        f"TIME: {duration:.2f}m | GPS: {gps['coords']['latitude'] if gps else 'N/A'}\n"
                    )
                    with open(LOG_FILE, "a", encoding="utf-8") as f:
                        f.write(log_entry)
                    st.success("Check-in recorded!")
                    del st.session_state.start_time
                else:
                    st.error("Hit Start first!")