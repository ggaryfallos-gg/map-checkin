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

# --- APP CONFIGURATION & CLOUD DB LINKS ---
st.set_page_config(page_title="Executive Logistics Hub", layout="wide", initial_sidebar_state="expanded")

# Initialize Cloud Connection
conn = st.connection("gsheets", type=GSheetsConnection)

# Define Database Endpoints
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit?usp=sharing" 

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

# --- DATA PIPELINE & CLOUD AUTO-HEALING ---
def auto_heal_coordinates_cloud(shipments_df, coords_df):
    """Background fallback healer (Silently fetches max 3 nodes to prevent timeouts)."""
    unique_route_cities = shipments_df['City_Clean'].dropna().unique()
    existing_cities = coords_df['City'].dropna().unique()
    
    missing_cities = [c for c in unique_route_cities if c not in existing_cities]
    nan_cities = coords_df[coords_df['Latitude'].isna()]['City'].tolist()
    
    cities_to_fetch = list(set(missing_cities + nan_cities))
    
    if cities_to_fetch:
        MAX_BATCH_SIZE = 3 
        cities_to_fetch = cities_to_fetch[:MAX_BATCH_SIZE]
        
        geolocator = Nominatim(user_agent="alumil_logistics_autoheal")
        new_data = []
        
        for city in cities_to_fetch:
            try:
                loc = geolocator.geocode(f"{city}, Greece", timeout=3)
                if loc:
                    new_data.append({"City": city, "Latitude": loc.latitude, "Longitude": loc.longitude})
                time.sleep(1)
            except Exception:
                pass 
        
        if new_data:
            new_df = pd.DataFrame(new_data)
            updated_coords = pd.concat([coords_df, new_df], ignore_index=True)
            updated_coords = updated_coords.dropna(subset=['Latitude']).drop_duplicates(subset=['City'], keep='last')
            
            conn.update(spreadsheet=COORDS_URL, data=updated_coords)
            return updated_coords
            
    return coords_df

@st.cache_data(ttl=600)
def load_and_optimize():
    """Main data loading pipeline reading directly from Google Sheets."""
    shipments = conn.read(spreadsheet=SHIPMENTS_URL, ttl=600)
    
    # MATH SANITIZATION
    weight_columns = ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']
    for col in weight_columns:
        if col in shipments.columns:
            shipments[col] = pd.to_numeric(
                shipments[col].astype(str).str.replace(',', '').str.replace(' ', ''), 
                errors='coerce'
            ).fillna(0.0)
    
    # Dynamic Fleet Extraction
    unique_plates = shipments['Truck License Plate'].dropna().unique()
    plates = pd.DataFrame({'PLATE NUMBER': unique_plates})

    # Deep Clean
    shipments['Plate_Clean'] = shipments['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    plates['Plate_Clean'] = plates['PLATE NUMBER'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    shipments['City_Clean'] = shipments['City'].astype(str).str.strip().str.upper()
    
    # Load Coordinates DB
    try:
        coords_db = conn.read(spreadsheet=COORDS_URL, ttl=600)
        coords_db['City'] = coords_db['City'].astype(str).str.strip().str.upper()
        coords_db['Latitude'] = pd.to_numeric(coords_db['Latitude'], errors='coerce')
        coords_db['Longitude'] = pd.to_numeric(coords_db['Longitude'], errors='coerce')
    except Exception:
        coords_db = pd.DataFrame(columns=['City', 'Latitude', 'Longitude'])
    
    coords_db = auto_heal_coordinates_cloud(shipments, coords_db)
    shipments = pd.merge(shipments, coords_db, left_on='City_Clean', right_on='City', how='left')
    
    counts = shipments.groupby('Plate_Clean')['City_Clean'].nunique().reset_index(name='Dests')
    merged_plates = pd.merge(plates, counts, on='Plate_Clean', how='left').fillna(0)
    merged_plates = merged_plates.sort_values(by='Dests', ascending=False)
    merged_plates['Label'] = merged_plates.apply(lambda r: f"{r['PLATE NUMBER']} ({int(r['Dests'])} Destinations)", axis=1)
    
    return merged_plates, shipments

# --- ROUTE OPTIMIZATION ALGORITHM (TSP) ---
def calculate_optimal_route(start_coords, destinations_df):
    unvisited = destinations_df.copy().dropna(subset=['Latitude', 'Longitude'])
    if unvisited.empty:
        return unvisited, 0

    route_sequence = []
    current_loc = start_coords
    total_distance_km = 0

    while not unvisited.empty:
        unvisited['Dist_from_current'] = unvisited.apply(
            lambda row: geodesic(current_loc, (row['Latitude'], row['Longitude'])).kilometers, 
            axis=1
        )
        nearest_idx = unvisited['Dist_from_current'].idxmin()
        nearest_node = unvisited.loc[nearest_idx]
        
        route_sequence.append(nearest_node)
        total_distance_km += nearest_node['Dist_from_current']
        current_loc = (nearest_node['Latitude'], nearest_node['Longitude'])
        
        unvisited = unvisited.drop(index=nearest_idx)

    ordered_route_df = pd.DataFrame(route_sequence).reset_index(drop=True)
    return ordered_route_df, total_distance_km

# --- EXECUTE DATA PIPELINE ---
try:
    plate_info, shipments_df = load_and_optimize()
except Exception as e:
    st.error(f"Cloud Connection Error. Ensure your Secrets are configured. Details: {e}")
    st.stop()

# --- 1. DISPATCH / LOGIN SCREEN ---
if st.session_state.user_plate is None:
    st.title("🚛 Fleet Dispatch Terminal")
    st.caption("Select a vehicle to initialize workflow. Sorted by highest logistical load.")
    
    sel = st.selectbox("Active Fleet Vehicles", plate_info['Label'])
    
    if st.button("Initialize Vehicle Session", type="primary", width="stretch"):
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
        
        if st.button("🔄 Swap Vehicle / Logout", width="stretch"):
            for key in ['user_plate', 'display_plate', 'last_finish_time', 'start_time']:
                st.session_state[key] = None
            st.rerun()
            
        st.markdown("---")
        st.subheader("Database Management")
        
        # EXPLICIT MANUAL OVERRIDE SYNC BUTTON
        if st.button("🌐 Force Sync Coordinates", width="stretch"):
            with st.spinner("Bypassing cache. Scanning Cloud Database..."):
                try:
                    temp_shipments = conn.read(spreadsheet=SHIPMENTS_URL, ttl=0)
                    temp_coords = conn.read(spreadsheet=COORDS_URL, ttl=0)
                    
                    temp_shipments['City_Clean'] = temp_shipments['City'].astype(str).str.strip().str.upper()
                    temp_coords['City'] = temp_coords['City'].astype(str).str.strip().str.upper()
                    
                    unique_route_cities = temp_shipments['City_Clean'].dropna().unique()
                    existing_cities = temp_coords['City'].dropna().unique()
                    
                    missing_cities = [c for c in unique_route_cities if c not in existing_cities]
                    nan_cities = temp_coords[pd.to_numeric(temp_coords['Latitude'], errors='coerce').isna()]['City'].tolist()
                    
                    cities_to_fetch = list(set(missing_cities + nan_cities))
                    
                    if cities_to_fetch:
                        st.info(f"Targeting {len(cities_to_fetch)} unmapped nodes. Initializing API sequence...")
                        geolocator = Nominatim(user_agent="alumil_logistics_explicit_sync")
                        new_data = []
                        
                        progress_bar = st.progress(0)
                        
                        for i, city in enumerate(cities_to_fetch):
                            try:
                                loc = geolocator.geocode(f"{city}, Greece", timeout=5)
                                if loc:
                                    new_data.append({"City": city, "Latitude": loc.latitude, "Longitude": loc.longitude})
                                time.sleep(1) # Strict Nominatim policy
                            except Exception:
                                pass
                            progress_bar.progress((i + 1) / len(cities_to_fetch))
                        
                        if new_data:
                            new_df = pd.DataFrame(new_data)
                            updated_coords = pd.concat([temp_coords, new_df], ignore_index=True)
                            updated_coords = updated_coords.dropna(subset=['Latitude']).drop_duplicates(subset=['City'], keep='last')
                            
                            conn.update(spreadsheet=COORDS_URL, data=updated_coords)
                            st.success(f"Successfully injected {len(new_data)} geographic signatures to Cloud Database.")
                        else:
                            st.warning("API Timeout or invalid city names. Try again later.")
                    else:
                        st.success("All geographic nodes are already 100% synchronized.")
                        
                    # Purge cache and force hard reload
                    load_and_optimize.clear()
                    time.sleep(1.5)
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"Sync failed: {e}")
            
    user_data = shipments_df[shipments_df['Plate_Clean'] == st.session_state.user_plate]
    
    # Global GPS Capture
    gps = get_geolocation()
    curr_lat, curr_lon = None, None
    if gps and 'coords' in gps:
        curr_lat = gps['coords']['latitude']
        curr_lon = gps['coords']['longitude']
    
    tab1, tab2, tab3, tab4 = st.tabs(["🌎 Daily Manifest", "🗺️ Optimal Routing", "📦 Unloading Protocol", "📊 Analytics"])
    
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
                
        if missing_cities:
            st.warning(f"⚠️ Missing coordinates for: {', '.join(missing_cities)}. Click 'Force Sync Coordinates' in the sidebar to resolve instantly.")
            
        if curr_lat and curr_lon:
            folium.Marker(
                [curr_lat, curr_lon], 
                popup="🔴 Live Driver Location",
                icon=folium.Icon(color='red', icon='crosshairs', prefix='fa')
            ).add_to(m)
            
        try:
            log_df = conn.read(spreadsheet=LOG_URL, ttl=0)
            if not log_df.empty:
                log_df['Checkin_Lat'] = pd.to_numeric(log_df['Checkin_Lat'], errors='coerce')
                log_df['Checkin_Lon'] = pd.to_numeric(log_df['Checkin_Lon'], errors='coerce')
                
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

    # --- TAB 2: OPTIMAL ROUTING ENGINE ---
    with tab2:
        st.subheader("Algorithmic Route Optimization")
        
        if curr_lat and curr_lon:
            start_coords = (curr_lat, curr_lon)
            start_label = "Live GPS Location"
        else:
            valid_nodes = user_data[['Latitude', 'Longitude']].dropna()
            if not valid_nodes.empty:
                first_node = valid_nodes.iloc[0]
                start_coords = (first_node['Latitude'], first_node['Longitude'])
                start_label = "Assumed Starting Point"
            else:
                start_coords = (41.0000, 22.8833) # Fallback to Kilkis HQ
                start_label = "KILKIS HQ"

        if st.button("⚙️ Execute TSP Optimization", type="primary", width="stretch"):
            with st.spinner("Calculating optimal vector path..."):
                ordered_route, total_km = calculate_optimal_route(
                    start_coords, 
                    user_data[['City_Clean', 'Latitude', 'Longitude']].drop_duplicates(subset=['City_Clean'])
                )
                
                if not ordered_route.empty:
                    opt_map = folium.Map(location=start_coords, zoom_start=7)
                    
                    folium.Marker(
                        start_coords, 
                        popup=f"🏁 START: {start_label}",
                        icon=folium.Icon(color='black', icon='play', prefix='fa')
                    ).add_to(opt_map)
                    
                    path_coordinates = [start_coords]
                    
                    for index, row in ordered_route.iterrows():
                        step_num = index + 1
                        coords = (row['Latitude'], row['Longitude'])
                        path_coordinates.append(coords)
                        
                        folium.Marker(
                            coords,
                            popup=f"Stop {step_num}: {row['City_Clean']}",
                            icon=folium.plugins.BeautifyIcon(
                                number=step_num,
                                border_color='blue',
                                text_color='blue',
                                inner_icon_style='margin-top:0;'
                            )
                        ).add_to(opt_map)
                    
                    folium.PolyLine(
                        path_coordinates,
                        weight=4,
                        color='blue',
                        dash_array='10',
                        opacity=0.8
                    ).add_to(opt_map)
                    
                    st.success(f"Path Optimized! Estimated Flight Distance: {total_km:,.1f} km")
                    st_folium(opt_map, width="100%", height=450, key="optimized_map")
                    
                    st.markdown("### Suggested Execution Order")
                    ordered_route.index += 1 
                    st.dataframe(ordered_route[['City_Clean', 'Dist_from_current']], width="stretch")
                else:
                    st.warning("No valid geographic destinations found to route.")

    # --- TAB 3: CHECK-IN & TIMING LOGIC ---
    with tab3:
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
        
        if col_start.button("▶️ Record Arrival (Start Unloading)", width="stretch"):
            now = datetime.now()
            st.session_state.start_time = now
            
            if st.session_state.last_finish_time:
                transit_dur = (now - st.session_state.last_finish_time).total_seconds() / 60
                st.session_state.current_transit_mins = transit_dur
                st.toast(f"Arrival logged. Transit took {transit_dur:.0f} mins.", icon="⏱️")
            else:
                st.session_state.current_transit_mins = 0
                st.toast("Initial arrival logged.", icon="🏁")

        if col_end.button("⏹️ Record Departure (Finish Unloading)", type="primary", width="stretch"):
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
                    existing_log = conn.read(spreadsheet=LOG_URL, ttl=0)
                    updated_log = pd.concat([existing_log, new_record], ignore_index=True)
                    conn.update(spreadsheet=LOG_URL, data=updated_log)
                    
                    st.success(f"Node Cleared! Cloud Database Updated. Unloading: {unload_dur:.1f}m")
                except Exception as e:
                    st.error(f"Cloud Sync Error: {e}")
                
                st.session_state.last_finish_time = now
                st.session_state.start_time = None
            else:
                st.error("Protocol Error: You must 'Record Arrival' before finishing.")

    # --- TAB 4: EXECUTIVE ANALYTICS ---
    with tab4:
        st.subheader("Real-Time Fleet Analytics")
        try:
            log_df = conn.read(spreadsheet=LOG_URL, ttl=0)
            
            if not log_df.empty:
                avg_unload = log_df['Unload_Mins'].mean()
                total_prof_delivered = log_df['Profiles_KG'].sum()
                total_stops = len(log_df)
                
                ec1, ec2, ec3 = st.columns(3)
                ec1.metric("Nodes Cleared", total_stops)
                ec2.metric("Avg Unloading Time", f"{avg_unload:.1f} min")
                ec3.metric("Profiles Delivered", f"{total_prof_delivered:,.0f} KG")
                
                st.caption("Live Cloud Ledger")
                st.dataframe(log_df.sort_values(by="Timestamp", ascending=False).head(10), width="stretch")
            else:
                st.info("Log repository is empty. Complete a delivery to generate analytics.")
        except Exception as e:
            st.warning(f"Unable to fetch cloud analytics. ({e})")
