import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation
from geopy.geocoders import Nominatim
from datetime import datetime
import os
import time

# --- APP CONFIGURATION ---
st.set_page_config(page_title="Executive Logistics Hub", layout="wide", initial_sidebar_state="expanded")
LOG_FILE = "checkin_log.xlsx"
COORDS_FILE = "CITY_COORDINATES.xlsx"

# Ensure structured Excel log exists
if not os.path.exists(LOG_FILE):
    df_init = pd.DataFrame(columns=[
        "Timestamp", "Plate", "Customer", "Profiles_KG", 
        "Accessories_KG", "Transit_Mins", "Unload_Mins",
        "Checkin_Lat", "Checkin_Lon"
    ])
    df_init.to_excel(LOG_FILE, index=False, engine='openpyxl')

# --- STATE MANAGEMENT ---
if 'user_plate' not in st.session_state:
    st.session_state.user_plate = None
if 'display_plate' not in st.session_state:
    st.session_state.display_plate = None
if 'last_finish_time' not in st.session_state:
    st.session_state.last_finish_time = None 
if 'start_time' not in st.session_state:
    st.session_state.start_time = None
if 'current_transit_mins' not in st.session_state:
    st.session_state.current_transit_mins = 0

# --- SILENT DATA PIPELINE & AUTO-HEALING ---
def auto_heal_coordinates_silent(shipments_df, coords_df):
    """
    Silently fetches missing coordinates in the background.
    NO Streamlit UI elements (st.warning, st.toast) are allowed here
    because this runs inside a cached function.
    """
    unique_route_cities = shipments_df['City_Clean'].dropna().unique()
    existing_cities = coords_df['City'].dropna().unique()
    
    missing_cities = [c for c in unique_route_cities if c not in existing_cities]
    nan_cities = coords_df[coords_df['Latitude'].isna()]['City'].tolist()
    
    cities_to_fetch = list(set(missing_cities + nan_cities))
    
    if cities_to_fetch:
        # Micro-batch to prevent long load times
        MAX_BATCH_SIZE = 3 
        cities_to_fetch = cities_to_fetch[:MAX_BATCH_SIZE]
        
        geolocator = Nominatim(user_agent="alumil_logistics_autoheal")
        new_data = []
        
        for city in cities_to_fetch:
            try:
                loc = geolocator.geocode(f"{city}, Greece", timeout=3)
                if loc:
                    new_data.append({"City": city, "Latitude": loc.latitude, "Longitude": loc.longitude})
                time.sleep(1) # Strict Nominatim policy
            except Exception:
                pass # Fail silently
        
        if new_data:
            new_df = pd.DataFrame(new_data)
            updated_coords = pd.concat([coords_df, new_df], ignore_index=True)
            updated_coords = updated_coords.dropna(subset=['Latitude']).drop_duplicates(subset=['City'], keep='last')
            
            updated_coords.to_excel(COORDS_FILE, index=False, engine='openpyxl')
            return updated_coords
            
    return coords_df

@st.cache_data
def load_and_optimize():
    """Main data loading pipeline with caching."""
    plates = pd.read_excel('PLATES.xlsx', engine='openpyxl')
    shipments = pd.read_excel('shipments.xlsx', engine='openpyxl')
    
    # Deep Clean
    shipments['Plate_Clean'] = shipments['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    plates['Plate_Clean'] = plates['PLATE NUMBER'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    shipments['City_Clean'] = shipments['City'].astype(str).str.strip().str.upper()
    
    # Initialize or Load Coordinates DB
    if os.path.exists(COORDS_FILE):
        coords_db = pd.read_excel(COORDS_FILE, engine='openpyxl')
        coords_db['City'] = coords_db['City'].astype(str).str.strip().str.upper()
    else:
        coords_db = pd.DataFrame(columns=['City', 'Latitude', 'Longitude'])
    
    # Trigger Silent Auto-Healing
    coords_db = auto_heal_coordinates_silent(shipments, coords_db)
    
    # Relational Merge
    shipments = pd.merge(shipments, coords_db, left_on='City_Clean', right_on='City', how='left')
    
    # Analytics for Sorting
    counts = shipments.groupby('Plate_Clean')['City_Clean'].nunique().reset_index(name='Dests')
    merged_plates = pd.merge(plates, counts, on='Plate_Clean', how='left').fillna(0)
    merged_plates = merged_plates.sort_values(by='Dests', ascending=False)
    merged_plates['Label'] = merged_plates.apply(lambda r: f"{r['PLATE NUMBER']} ({int(r['Dests'])} Destinations)", axis=1)
    
    return merged_plates, shipments

# Execute Data Pipeline
try:
    plate_info, shipments_df = load_and_optimize()
except Exception as e:
    st.error(f"Data Pipeline Error. Details: {e}")
    st.stop()

# --- 1. DISPATCH / LOGIN SCREEN ---
if st.session_state.user_plate is None:
    st.title("🚛 Fleet Dispatch Terminal")
    st.caption("Select a vehicle to initialize workflow. Sorted by highest logistical load.")
    
    sel = st.selectbox("Active Fleet Vehicles", plate_info['Label'])
    
    if st.button("Initialize Vehicle Session", type="primary", use_container_width=True):
        row = plate_info[plate_info['Label'] == sel].iloc[0]
        st.session_state.user_plate = row['Plate_Clean']
        st.session_state.display_plate = row['PLATE NUMBER']
        st.session_state.last_finish_time = None 
        st.rerun()

# --- 2. ACTIVE TERMINAL ---
else:
    with st.sidebar:
        st.header("Terminal Control")
        st.success(f"Active: **{st.session_state.display_plate}**")
        if st.button("🔄 Swap Vehicle / Logout", use_container_width=True):
            for key in ['user_plate', 'display_plate', 'last_finish_time', 'start_time']:
                st.session_state[key] = None
            st.rerun()
            
    user_data = shipments_df[shipments_df['Plate_Clean'] == st.session_state.user_plate]
    
    # Global GPS Capture
    gps = get_geolocation()
    curr_lat, curr_lon = None, None
    if gps and 'coords' in gps:
        curr_lat = gps['coords']['latitude']
        curr_lon = gps['coords']['longitude']
    
    tab1, tab2, tab3 = st.tabs(["🌎 Route Map", "📦 Unloading Protocol", "📊 Executive Analytics"])
    
    # --- TAB 1: ZERO-LATENCY MAP ---
    with tab1:
        st.subheader("Optimized Route Overview")
        m = folium.Map(location=[39.0, 22.0], zoom_start=6)
        
        map_points = user_data[['Latitude', 'Longitude', 'City_Clean']].drop_duplicates()
        missing_cities = []
        
        for _, row in map_points.iterrows():
            if pd.notna(row['Latitude']) and pd.notna(row['Longitude']):
                folium.Marker(
                    [row['Latitude'], row['Longitude']], 
                    popup=f"📍 Target: {row['City_Clean']}",
                    icon=folium.Icon(color='blue', icon='truck', prefix='fa')
                ).add_to(m)
            else:
                missing_cities.append(row['City_Clean'])
                
        # Move the warning OUTSIDE the cached function
        if missing_cities:
            st.warning(f"⚠️ Missing coordinates for: {', '.join(missing_cities)}. The background healer will fetch these gradually.")
            
        if curr_lat and curr_lon:
            folium.Marker(
                [curr_lat, curr_lon], 
                popup="🔴 Live Driver Location",
                icon=folium.Icon(color='red', icon='crosshairs', prefix='fa')
            ).add_to(m)
            
        try:
            log_df = pd.read_excel(LOG_FILE, engine='openpyxl')
            truck_log = log_df[log_df['Plate'] == st.session_state.display_plate]
            for _, row in truck_log.iterrows():
                if pd.notna(row.get('Checkin_Lat')) and pd.notna(row.get('Checkin_Lon')):
                    folium.Marker(
                        [row['Checkin_Lat'], row['Checkin_Lon']], 
                        popup=f"✅ Cleared: {row['Customer']}<br>Time: {row['Timestamp']}",
                        icon=folium.Icon(color='green', icon='check', prefix='fa')
                    ).add_to(m)
        except Exception:
            pass

        st_folium(m, width="100%", height=450, key="fast_map")

    # --- TAB 2: CHECK-IN & TIMING LOGIC ---
    with tab2:
        st.subheader("Delivery Node Configuration")
        
        cust_list = sorted(user_data['Name'].unique())
        selected_cust = st.selectbox("Select Unloading Target", cust_list)
        cust_data = user_data[user_data['Name'] == selected_cust]
        
        total_kg = cust_data['Total KG'].sum()
        profiles = cust_data[['Unpainted', 'White', 'Colored']].sum().sum()
        accs = cust_data['Accessories'].sum()
        
        st.markdown("---")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Load", f"{total_kg:,.1f} KG")
        c2.metric("Profiles (U/W/C)", f"{profiles:,.1f} KG")
        c3.metric("Accessories", f"{accs:,.1f} KG")
        st.markdown("---")
        
        col_start, col_end = st.columns(2)
        
        if col_start.button("▶️ Record Arrival (Start Unloading)", use_container_width=True):
            now = datetime.now()
            st.session_state.start_time = now
            
            if st.session_state.last_finish_time:
                transit_dur = (now - st.session_state.last_finish_time).total_seconds() / 60
                st.session_state.current_transit_mins = transit_dur
                st.toast(f"Arrival logged. Transit took {transit_dur:.0f} mins.", icon="⏱️")
            else:
                st.session_state.current_transit_mins = 0
                st.toast("Initial arrival logged.", icon="🏁")

        if col_end.button("⏹️ Record Departure (Finish Unloading)", type="primary", use_container_width=True):
            if st.session_state.start_time is not None:
                now = datetime.now()
                unload_dur = (now - st.session_state.start_time).total_seconds() / 60
                transit_dur = st.session_state.current_transit_mins
                
                new_record = pd.DataFrame([{
                    "Timestamp": now.strftime('%Y-%m-%d %H:%M:%S'),
                    "Plate": st.session_state.display_plate,
                    "Customer": selected_cust,
                    "Profiles_KG": profiles,
                    "Accessories_KG": accs,
                    "Transit_Mins": round(transit_dur, 1),
                    "Unload_Mins": round(unload_dur, 1),
                    "Checkin_Lat": curr_lat,
                    "Checkin_Lon": curr_lon
                }])
                
                try:
                    existing_log = pd.read_excel(LOG_FILE, engine='openpyxl')
                    updated_log = pd.concat([existing_log, new_record], ignore_index=True)
                    updated_log.to_excel(LOG_FILE, index=False, engine='openpyxl')
                    
                    st.success(f"Node Cleared! GPS Location Saved. Unloading: {unload_dur:.1f}m")
                except Exception as e:
                    st.error(f"I/O Error writing to Excel: {e}")
                
                st.session_state.last_finish_time = now
                st.session_state.start_time = None
            else:
                st.error("Protocol Error: You must 'Record Arrival' before finishing.")

    # --- TAB 3: EXECUTIVE ANALYTICS ---
    with tab3:
        st.subheader("Real-Time Fleet Analytics")
        try:
            log_df = pd.read_excel(LOG_FILE, engine='openpyxl')
            
            if not log_df.empty:
                avg_unload = log_df['Unload_Mins'].mean()
                total_prof_delivered = log_df['Profiles_KG'].sum()
                total_stops = len(log_df)
                
                ec1, ec2, ec3 = st.columns(3)
                ec1.metric("Nodes Cleared", total_stops)
                ec2.metric("Avg Unloading Time", f"{avg_unload:.1f} min")
                ec3.metric("Profiles Delivered", f"{total_prof_delivered:,.0f} KG")
                
                st.caption("Raw Delivery Ledger")
                st.dataframe(log_df.sort_values(by="Timestamp", ascending=False).head(10), use_container_width=True)
            else:
                st.info("Log repository is empty. Complete a delivery to generate analytics.")
        except Exception as e:
            st.warning(f"Unable to parse analytics. Ensure {LOG_FILE} is accessible. ({e})")