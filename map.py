import streamlit as st
import pandas as pd
import os
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium
import folium
from geopy.geocoders import Nominatim
from datetime import datetime

# --- App Config ---
st.set_page_config(page_title="Logistics Hub", layout="wide")
LOG_FILE = "checkin_log.txt"
geolocator = Nominatim(user_agent="alumil_logistics_v3")

# --- INITIALIZE CACHE ---
if 'geo_cache' not in st.session_state:
    st.session_state.geo_cache = {}

# --- Data Loading & Sorting Logic ---
@st.cache_data
def load_data_and_sort():
    plates = pd.read_excel('PLATES.xlsx', engine='openpyxl')
    shipments = pd.read_excel('shipments.xlsx', engine='openpyxl')
    
    # 1. Clean Data
    shipments['Plate_Clean'] = shipments['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    plates['Plate_Clean'] = plates['PLATE NUMBER'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    
    # 2. Calculate Destination Counts
    dest_counts = shipments.groupby('Plate_Clean')['City'].nunique().reset_index()
    dest_counts.columns = ['Plate_Clean', 'Count']
    
    # 3. Merge and Sort
    merged = pd.merge(plates, dest_counts, on='Plate_Clean', how='left').fillna(0)
    merged['Count'] = merged['Count'].astype(int)
    merged = merged.sort_values(by='Count', ascending=False)
    
    # 4. Create Informative Labels
    merged['Label'] = merged.apply(lambda r: f"{r['PLATE NUMBER']} ({r['Count']} Dests)", axis=1)
    
    return merged, shipments

plate_info_df, shipments_df = load_data_and_sort()

# --- HELPER: Geocoding Cache ---
def get_coords(city):
    if city in st.session_state.geo_cache: return st.session_state.geo_cache[city]
    try:
        loc = geolocator.geocode(f"{city}, Greece", timeout=3)
        if loc:
            st.session_state.geo_cache[city] = (loc.latitude, loc.longitude)
            return st.session_state.geo_cache[city]
    except: return None
    return None

# --- MAIN APP LOGIC ---
if 'user_plate' not in st.session_state:
    st.session_state.user_plate = None

if st.session_state.user_plate is None:
    st.title("🚛 Smart Vehicle Selection")
    st.caption("Vehicles are sorted by total destination load.")
    
    # Use the new informative labels for the selectbox
    selected_label = st.selectbox("Select Vehicle", plate_info_df['Label'])
    
    if st.button("Initialize Logistics Hub"):
        # Retrieve original plate and cleaned version from the selection
        row = plate_info_df[plate_info_df['Label'] == selected_label].iloc[0]
        st.session_state.user_plate = row['Plate_Clean']
        st.session_state.display_plate = row['PLATE NUMBER']
        st.rerun()
else:
    # Sidebar for Ad Hoc Changes
    with st.sidebar:
        st.header("Active Session")
        st.info(f"🚚 {st.session_state.display_plate}")
        if st.button("Change Vehicle"):
            st.session_state.user_plate = None
            st.rerun()

    # Data for selected truck
    user_shipments = shipments_df[shipments_df['Plate_Clean'] == st.session_state.user_plate]
    
    tab1, tab2 = st.tabs(["🌎 Map Overview", "📦 Unloading"])

    with tab1:
        cities = user_shipments['City'].unique()
        st.subheader(f"Routing Map: {len(cities)} Unique Cities")
        
        # Start Map (Greece Center)
        m = folium.Map(location=[38.2, 23.8], zoom_start=7)
        for city in cities:
            coords = get_coords(city)
            if coords:
                folium.Marker(coords, popup=city, icon=folium.Icon(color='blue', icon='truck', prefix='fa')).add_to(m)
        st_folium(m, width="100%", height=450, key="main_map")

    with tab2:
        # Unloading details
        cust_list = sorted(user_shipments['Name'].unique())
        sel_cust = st.selectbox("Select Customer Site", cust_list)
        cust_data = user_shipments[user_shipments['Name'] == sel_cust]
        
        # Aggregation
        m_vals = cust_data[['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']].sum()
        p_sum = m_vals['Unpainted'] + m_vals['White'] + m_vals['Colored']
        
        c1, c2 = st.columns(2)
        c1.metric("Profiles", f"{p_sum:.1f} KG")
        c2.metric("Accessories", f"{m_vals['Accessories']:.1f} KG")
        
        # Recording Logic (Shortened for brevity)
        st.divider()
        if st.button("▶️ Start Unloading"):
            st.session_state.start_time = datetime.now()
            st.toast("Started!")