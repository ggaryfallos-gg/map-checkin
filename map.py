import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from datetime import datetime
import time
from streamlit_gsheets import GSheetsConnection

# --- APP CONFIGURATION ---
st.set_page_config(page_title="Executive Logistics Hub", layout="wide", initial_sidebar_state="expanded")

conn = st.connection("gsheets", type=GSheetsConnection)

SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit" 

# --- STATE MANAGEMENT ---
if 'user_plate' not in st.session_state:
    st.session_state.user_plate = None
if 'display_plate' not in st.session_state:
    st.session_state.display_plate = None
if 'start_time' not in st.session_state:
    st.session_state.start_time = None

# --- ROBUST NUMERIC CLEANER ---
def clean_sheet_numeric(series):
    """
    Handles Greek/European formatting issues.
    Converts '1.200,50' or '1,200.50' correctly to 1200.5
    """
    s = series.astype(str).str.replace(' ', '', regex=False)
    
    # If there's a comma AND a dot (e.g. 1.200,50)
    # We assume the last one is the decimal separator
    def parse_mixed(val):
        if not val or val == 'nan': return 0.0
        # Remove thousands separator (assuming it's the first one found)
        if ',' in val and '.' in val:
            if val.find('.') < val.find(','): # 1.200,50
                val = val.replace('.', '').replace(',', '.')
            else: # 1,200.50
                val = val.replace(',', '')
        # Handle single separator cases
        elif ',' in val: # 1200,50 -> 1200.50
            val = val.replace(',', '.')
        try:
            return float(val)
        except:
            return 0.0

    return s.apply(parse_mixed)

# --- DATA PIPELINE ---
@st.cache_data(ttl=300)
def load_and_fix_data():
    shipments = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    
    shipments['Plate_Clean'] = shipments['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    shipments['City_Clean'] = shipments['City'].astype(str).str.strip().str.upper()
    
    # Applying the Robust Cleaner to weight columns
    weight_cols = ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']
    for col in weight_cols:
        if col in shipments.columns:
            shipments[col] = clean_sheet_numeric(shipments[col])
            
    try:
        coords_db = conn.read(spreadsheet=COORDS_URL, ttl=300)
        coords_db['City'] = coords_db['City'].astype(str).str.strip().str.upper()
        coords_db['Latitude'] = pd.to_numeric(coords_db['Latitude'], errors='coerce')
        coords_db['Longitude'] = pd.to_numeric(coords_db['Longitude'], errors='coerce')
        coords_db = coords_db.drop_duplicates(subset=['City'], keep='last').dropna(subset=['Latitude'])
    except Exception:
        coords_db = pd.DataFrame(columns=['City', 'Latitude', 'Longitude'])
    
    # Safe Merge
    shipments = pd.merge(shipments, coords_db, left_on='City_Clean', right_on='City', how='left')
    
    unique_plates = shipments[['Truck License Plate', 'Plate_Clean']].drop_duplicates()
    counts = shipments.groupby('Plate_Clean')['City_Clean'].nunique().reset_index(name='Dests')
    fleet = pd.merge(unique_plates, counts, on='Plate_Clean', how='left').fillna(0)
    fleet = fleet.sort_values(by='Dests', ascending=False)
    fleet['Label'] = fleet.apply(lambda r: f"{r['Truck License Plate']} ({int(r['Dests'])} Stops)", axis=1)
    
    return fleet, shipments

def calculate_tsp(start_coords, destinations_df):
    unvisited = destinations_df.copy().dropna(subset=['Latitude', 'Longitude'])
    if unvisited.empty: return unvisited, 0
    route, current_loc, total_km = [], start_coords, 0
    while not unvisited.empty:
        unvisited['d'] = unvisited.apply(lambda r: geodesic(current_loc, (r['Latitude'], r['Longitude'])).km, axis=1)
        nearest_idx = unvisited['d'].idxmin()
        node = unvisited.loc[nearest_idx]
        route.append(node)
        total_km += node['d']
        current_loc = (node['Latitude'], node['Longitude'])
        unvisited = unvisited.drop(index=nearest_idx)
    return pd.DataFrame(route).reset_index(drop=True), total_km

# --- UI EXECUTION ---
fleet, all_shipments = load_and_fix_data()

if st.session_state.user_plate is None:
    st.title("🚛 Fleet Control Hub")
    sel = st.selectbox("Assign Vehicle Profile", fleet['Label'])
    if st.button("Initialize Terminal", type="primary", width="stretch"):
        row = fleet[fleet['Label'] == sel].iloc[0]
        st.session_state.user_plate = row['Plate_Clean']
        st.session_state.display_plate = row['Truck License Plate']
        st.rerun()
else:
    with st.sidebar:
        st.success(f"Truck: {st.session_state.display_plate}")
        if st.button("Logout", width="stretch"):
            st.session_state.user_plate = None
            st.rerun()
        
    user_data = all_shipments[all_shipments['Plate_Clean'] == st.session_state.user_plate]
    gps = get_geolocation()
    curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps and 'coords' in gps else (41.0, 22.8)
    
    t1, t2, t3 = st.tabs(["🌎 Map", "🗺️ Routing", "📦 Unloading"])

    with t1:
        st.subheader("Regional Stop Distribution")
        m1 = folium.Map(location=curr_loc, zoom_start=7)
        for _, r in user_data.drop_duplicates(subset=['City_Clean']).iterrows():
            if pd.notna(r['Latitude']):
                folium.Marker([r['Latitude'], r['Longitude']], popup=r['City_Clean']).add_to(m1)
        st_folium(m1, width="100%", height=450)

    with t2:
        st.subheader("Optimal Sequential Path")
        if st.button("⚙️ Calculate & Draw Route", type="primary", width="stretch"):
            route, km = calculate_tsp(curr_loc, user_data.drop_duplicates(subset=['City_Clean']))
            if not route.empty:
                m2 = folium.Map(location=curr_loc, zoom_start=7)
                path = [curr_loc]
                folium.Marker(curr_loc, popup="START", icon=folium.Icon(color='black')).add_to(m2)
                for i, r in route.iterrows():
                    pos = (r['Latitude'], r['Longitude'])
                    path.append(pos)
                    folium.Marker(pos, icon=folium.DivIcon(html=f"""<div style="background-color:blue; color:white; border-radius:50%; width:28px; height:28px; display:flex; align-items:center; justify-content:center; border:2px solid white; font-weight:bold;">{i+1}</div>""")).add_to(m2)
                folium.PolyLine(path, color="blue", weight=3, dash_array='10').add_to(m2)
                st.success(f"Trip: {km:.1f} km")
                st_folium(m2, width="100%", height=500, key="route_map")

    with t3:
        st.subheader("Check-in Protocol")
        customer = st.selectbox("Select Target Customer", sorted(user_data['Name'].unique()))
        cust_rows = user_data[user_data['Name'] == customer]
        
        # Corrected Aggregation
        tot_kg = cust_rows['Total KG'].sum()
        prof_kg = cust_rows[['Unpainted', 'White', 'Colored']].sum().sum()
        acc_kg = cust_rows['Accessories'].sum()
        
        st.markdown("---")
        col1, col2, col3 = st.columns(3)
        col1.metric("Corrected Load", f"{tot_kg:,.1f} KG")
        col2.metric("Profiles", f"{prof_kg:,.1f} KG")
        col3.metric("Accessories", f"{acc_kg:,.1f} KG")
        st.markdown("---")
        
        if st.button("▶️ Start Unloading", width="stretch"):
            st.session_state.start_time = datetime.now()
            st.toast("Timer Active.")
        if st.button("⏹️ Complete & Sync", type="primary", width="stretch"):
            if st.session_state.start_time:
                dur = (datetime.now() - st.session_state.start_time).total_seconds() / 60
                log_entry = pd.DataFrame([{"Timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'), "Plate": st.session_state.display_plate, "Customer": customer, "Unload_Mins": round(dur, 1)}])
                conn.update(spreadsheet=LOG_URL, data=pd.concat([conn.read(spreadsheet=LOG_URL, ttl=0), log_entry], ignore_index=True))
                st.success(f"Logged: {dur:.1f} mins.")
                st.session_state.start_time = None
