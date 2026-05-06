import streamlit as st
import pandas as pd
import folium
import requests
import urllib.parse
import time
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation
from geopy.distance import geodesic
from datetime import datetime, timedelta, timezone
from streamlit_gsheets import GSheetsConnection

# --- CONFIG & TIMEZONE ---
st.set_page_config(page_title="Alumil Logistics Hub v39", layout="wide", initial_sidebar_state="collapsed")
conn = st.connection("gsheets", type=GSheetsConnection)
GR_TIME = timezone(timedelta(hours=3))

# --- LINKS ΤΩΝ GOOGLE SHEETS ---
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"
CUSADDRESS_URL = "https://docs.google.com/spreadsheets/d/1k9-gCuo_BxVezLaVoagh04xfUoXfj26aH2Mf833qGMk/edit" 
DELIVERIES_URL = "https://docs.google.com/spreadsheets/d/10uKgg3AIuSnROK2-6VnY0Rm3U4vH2xv8O4OFthgaWww/edit"

# --- HELPERS / UTILITIES ---
@st.cache_data(ttl=86400)
def geocode_address(street, city):
    if not street or str(street).lower() in ['nan', 'none', '']: return None, None
    street_clean = str(street).split(',')[0].strip()
    city_clean = str(city).strip()
    queries = [f"{street_clean}, {city_clean}, Greece", f"{street_clean}, Greece"]
    for q in queries:
        url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(q)}&format=json&limit=1"
        try:
            r = requests.get(url, headers={'User-Agent': 'AlumilLogisticsApp/1.0'}, timeout=4).json()
            if r: return float(r[0]['lat']), float(r[0]['lon'])
            time.sleep(0.3)
        except: pass
    return None, None

def get_osrm_data(coords):
    if len(coords) < 2: return None, 0, 0
    locs = ";".join([f"{lon},{lat}" for lat, lon in coords])
    url = f"http://router.project-osrm.org/route/v1/driving/{locs}?overview=full&geometries=geojson"
    try:
        r = requests.get(url, timeout=10).json()
        if r['code'] == 'Ok':
            return r['routes'][0]['geometry']['coordinates'], r['routes'][0]['distance']/1000, r['routes'][0]['duration']/60
    except: pass
    return None, 0, 0

def clean_val(v):
    if pd.isna(v): return 0.0
    if isinstance(v, (int, float)): return float(v)
    v_str = str(v).strip()
    if not v_str or v_str.lower() in ['nan', 'none', '']: return 0.0
    if ',' in v_str: v_str = v_str.replace('.', '').replace(',', '.')
    try: return float(v_str)
    except: return 0.0

def gr_num(val, decimals=1):
    s = f"{val:,.{decimals}f}"
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')

# ==========================================
# 🛑 PUBLIC VIEW: SMART LIVE TRACKING
# ==========================================
if "track" in st.query_params:
    tracked_plate = st.query_params["track"].upper().replace(' ', '')
    st.title("📍 Alumil Live Delivery Tracking")
    
    try:
        ship_df = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
        pod_logs = conn.read(spreadsheet=LOG_URL, worksheet="Sheet1", ttl=0) 
        transit_logs = conn.read(spreadsheet=LOG_URL, worksheet="Transit_Log", ttl=0)
        
        ship_df.columns = ship_df.columns.str.strip()
        ship_df['Plate_Clean'] = ship_df['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
        
        # 1. Εύρεση τρέχοντος στόχου (Sequence Check)
        truck_route = ship_df[ship_df['Plate_Clean'] == tracked_plate].drop_duplicates('Name')
        finished = pod_logs[pod_logs['Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper() == tracked_plate]['Customer'].tolist()
        pending = truck_route[~truck_route['Name'].isin(finished)]
        
        if pending.empty:
            st.success("🏁 Το δρομολόγιο ολοκληρώθηκε. Ευχαριστούμε!")
            st.stop()
            
        target = pending.iloc[0]
        st.subheader(f"Προορισμός: {target['Name']}")

        # 2. GPS Data & Geofencing
        transit_logs['Plate_Clean'] = transit_logs['Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
        v_log = transit_logs[transit_logs['Plate_Clean'] == tracked_plate].copy()
        v_log['Latitude'] = pd.to_numeric(v_log['Latitude'], errors='coerce')
        v_log['Longitude'] = pd.to_numeric(v_log['Longitude'], errors='coerce')
        v_log = v_log.dropna(subset=['Latitude', 'Longitude'])

        if not v_log.empty:
            recent = v_log.tail(10)
            curr_pos = (recent.iloc[-1]['Latitude'], recent.iloc[-1]['Longitude'])
            
            # Fallback coords για τον πελάτη
            t_lat, t_lon = geocode_address(target.get('Street', ''), target['City'])
            
            if t_lat:
                dist = geodesic(curr_pos, (t_lat, t_lon)).km
                if dist > 20:
                    st.warning(f"🚚 Το φορτηγό απέχει {int(dist)} χλμ. Ο χάρτης ενεργοποιείται κάτω από τα 20 χλμ.")
                else:
                    st.success(f"✨ Το φορτηγό πλησιάζει! Απόσταση: {gr_num(dist, 1)} χλμ.")
                    m_pub = folium.Map(location=curr_pos, zoom_start=14)
                    c_list = [[r['Latitude'], r['Longitude']] for _, r in recent.iterrows()]
                    geom, _, _ = get_osrm_data(c_list)
                    if geom: folium.PolyLine([[p[1], p[0]] for p in geom], color="#E3000F", weight=5).add_to(m_pub)
                    folium.Marker(curr_pos, icon=folium.Icon(color='red', icon='truck', prefix='fa')).add_to(m_pub)
                    folium.Marker([t_lat, t_lon], icon=folium.Icon(color='green', icon='home')).add_to(m_pub)
                    st_folium(m_pub, width="100%", height=500)
            else: st.warning("Υπολογισμός θέσης...")
        else: st.warning("Αναμονή για σήμα GPS...")
    except Exception as e: st.error(f"Tracking Error: {e}")
    st.stop()

# ==========================================
# 🚛 PRIVATE VIEW: DRIVER & ADMIN
# ==========================================
if "password_correct" not in st.session_state: st.session_state.password_correct = False
if "user_plate" not in st.session_state: st.session_state.user_plate = None
if "sel_date" not in st.session_state: st.session_state.sel_date = "Όλες"
if "filter_plate" not in st.session_state: st.session_state.filter_plate = "Όλα"
if "filter_date" not in st.session_state: st.session_state.filter_date = "Όλες"
if "route_data" not in st.session_state: st.session_state.route_data = []
if "route_geom" not in st.session_state: st.session_state.route_geom = None
if "draft_sequence" not in st.session_state: st.session_state.draft_sequence = None
if "start_time" not in st.session_state: st.session_state.start_time = None

def check_password():
    if st.session_state.password_correct: return True
    st.title("🔐 Alumil Secure Login")
    pwd = st.text_input("Προσωπικός Κωδικός", type="password")
    if st.button("Είσοδος", use_container_width=True):
        if "passwords" in st.secrets and pwd in st.secrets["passwords"]:
            st.session_state.password_correct = True
            st.session_state.username = st.secrets["passwords"][pwd]
            st.rerun()
    return False

if not check_password(): st.stop()

# --- MASTER DATA PIPELINE ---
@st.cache_data(ttl=300)
def load_master_data():
    ship = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    ship.columns = ship.columns.str.strip()
    ship['Plate_Clean'] = ship['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    ship['City_Clean'] = ship['City'].astype(str).str.strip().str.upper()
    ship['Delivery'] = ship['Delivery'].astype(str).str.strip().str.replace('.0', '', regex=False).str.lstrip('0')
    for c in ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']:
        if c in ship.columns: ship[c] = ship[c].apply(clean_val)
    
    try:
        dels = conn.read(spreadsheet=DELIVERIES_URL, ttl=300)
        dels.columns = dels.columns.str.strip()
        dels['Delivery'] = dels['Delivery'].astype(str).str.strip().str.replace('.0', '', regex=False).str.lstrip('0')
        dels_sub = dels[['Delivery', 'Act. Gds Mvmnt Date']].drop_duplicates('Delivery')
        ship = pd.merge(ship, dels_sub, on='Delivery', how='left')
        ship['Loading_Date'] = ship['Act. Gds Mvmnt Date'].fillna('Άγνωστη Ημ/νία').astype(str)
        ship['Loading_Date'] = ship['Loading_Date'].replace(['nan', 'NaT', 'None', ''], 'Άγνωστη Ημ/νία')
    except: ship['Loading_Date'] = 'Άγνωστη Ημ/νία'

    try:
        cus = conn.read(spreadsheet=CUSADDRESS_URL, ttl=300)
        cus.columns = cus.columns.str.strip()
        for col in ['Name', 'Street', 'Telephone 1', 'Latitude', 'Longitude']:
            if col not in cus.columns: cus[col] = ''
        cus['Latitude'] = pd.to_numeric(cus['Latitude'], errors='coerce')
        cus['Longitude'] = pd.to_numeric(cus['Longitude'], errors='coerce')
        df = pd.merge(ship, cus[['Name', 'Street', 'Telephone 1', 'Latitude', 'Longitude']].drop_duplicates('Name'), on='Name', how='left')
    except: df = ship.copy()

    coords_db = conn.read(spreadsheet=COORDS_URL, ttl=300)
    coords_db.columns = coords_db.columns.str.strip()
    coords_db['City_Match'] = coords_db['City'].astype(str).str.strip().str.upper()
    df = pd.merge(df, coords_db[['City_Match', 'Latitude', 'Longitude']].rename(columns={'Latitude':'Lat_city', 'Longitude':'Lon_city'}), left_on='City_Clean', right_on='City_Match', how='left')
    
    df['Final_Lat'] = df['Latitude'].fillna(df['Lat_city'])
    df['Final_Lon'] = df['Longitude'].fillna(df['Lon_city'])
    
    fleet = df.groupby(['Truck License Plate', 'Plate_Clean', 'Loading_Date'])['Name'].nunique().reset_index(name='Dests')
    unique_routes = df[['Truck License Plate', 'Plate_Clean', 'Loading_Date']].drop_duplicates()
    fleet_summary = pd.merge(unique_routes, fleet, on=['Truck License Plate', 'Plate_Clean', 'Loading_Date'], how='left').fillna(0)
    return fleet_summary, df

fleet_info, all_data = load_master_data()

def reset_shift():
    st.session_state.user_plate = None
    st.session_state.loading_date = None
    st.session_state.sel_date = "Όλες"
    st.session_state.route_data = []
    st.session_state.route_geom = None
    st.session_state.draft_sequence = None
    st.session_state.start_time = None

# --- SIDEBAR ---
st.sidebar.title("Alumil Hub")
st.sidebar.write(f"👤 **{st.session_state.username}**")
app_mode = st.sidebar.radio("Μενού", ["🚛 Driver Terminal", "📊 Admin Dashboard"])
if st.sidebar.button("🔄 Αλλαγή Οχήματος"):
    reset_shift()
    st.rerun()

# --- 1. DRIVER TERMINAL ---
if app_mode == "🚛 Driver Terminal":
    if st.session_state.user_plate is None:
        st.title("Επιλογή Δρομολογίου")
        
        col1, col2 = st.columns(2)
        avail_dates = ["Όλες"] + sorted(fleet_info['Loading_Date'].unique().tolist())
        date_sel = col2.selectbox("📅 Ημερομηνία", avail_dates, index=avail_dates.index(st.session_state.filter_date))
        
        f_fleet = fleet_info[fleet_info['Loading_Date'] == date_sel] if date_sel != "Όλες" else fleet_info
        plate_options = ["Όλλα"] + [f"{r['Truck License Plate']} ({int(r['Dests'])} Στάσεις)" for _, r in f_fleet.iterrows()]
        sel_p_disp = col1.selectbox("🚚 Φορτηγό", plate_options)

        if st.button("🚀 Έναρξη Βάρδιας", type="primary", use_container_width=True):
            if sel_p_disp != "Όλλα":
                selected = f_fleet[f_fleet['Truck License Plate'] == sel_p_disp.split(' (')[0]].iloc[0]
                st.session_state.user_plate = selected['Plate_Clean']
                st.session_state.display_plate = selected['Truck License Plate']
                st.session_state.loading_date = selected['Loading_Date']
                st.session_state.sel_date = date_sel
                st.rerun()
    else:
        user_data = all_data[(all_data['Plate_Clean'] == st.session_state.user_plate) & (all_data['Loading_Date'] == st.session_state.loading_date)].copy()
        gps = get_geolocation()
        curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps else (40.64, 22.94)

        # Batch Geocoding & Auto-Save
        new_coords = []
        for idx, row in user_data.iterrows():
            if pd.isna(row['Latitude']) and str(row.get('Street','')).lower() not in ['nan','']:
                lat, lon = geocode_address(row['Street'], row['City'])
                if lat:
                    user_data.at[idx, 'Final_Lat'], user_data.at[idx, 'Final_Lon'] = lat, lon
                    new_coords.append({'Name': row['Name'], 'Latitude': lat, 'Longitude': lon})

        if new_coords:
            with st.spinner("Ενημέρωση χάρτη..."):
                master_cus = conn.read(spreadsheet=CUSADDRESS_URL, ttl=0)
                for nc in new_coords:
                    master_cus.loc[master_cus['Name'] == nc['Name'], ['Latitude', 'Longitude']] = [nc['Latitude'], nc['Longitude']]
                conn.update(spreadsheet=CUSADDRESS_URL, data=master_cus)
                st.toast("✅ Νέες διευθύνσεις αποθηκεύτηκαν!")

        t1, t2, t3, t4, t5 = st.tabs(["🌎 Χάρτης", "🛣️ Δρομολόγηση", "📦 POD Protocol", "📊 Analytics", "📩 Ειδοποίηση"])
        
        with t1:
            m = folium.Map(location=curr_loc, zoom_start=8)
            folium.Marker(curr_loc, icon=folium.Icon(color='green', icon='truck', prefix='fa')).add_to(m)
            for _, r in user_data.drop_duplicates('Name').iterrows():
                if pd.notna(r['Final_Lat']):
                    folium.Marker([r['Final_Lat'], r['Final_Lon']], popup=r['Name']).add_to(m)
            st_folium(m, width="100%", height=500, key="driver_map")

        with t2:
            st.subheader("Βελτιστοποίηση Διαδρομής")
            if st.button("🤖 Αυτόματη Πρόταση Σειράς (OSRM)", use_container_width=True):
                stops = user_data.drop_duplicates('Name').dropna(subset=['Final_Lat']).copy()
                path, pts = [], [curr_loc]
                while not stops.empty:
                    stops['d'] = stops.apply(lambda r: geodesic(pts[-1], (r['Final_Lat'], r['Final_Lon'])).km, axis=1)
                    idx = stops['d'].idxmin()
                    row = stops.loc[idx]
                    path.append({'Name': row['Name'], 'Street': row['Street'], 'KG': row['Total KG'], 'Lat': row['Final_Lat'], 'Lon': row['Final_Lon']})
                    pts.append((row['Final_Lat'], row['Final_Lon']))
                    stops = stops.drop(idx)
                
                df_seq = pd.DataFrame(path)
                df_seq.insert(0, 'Σειρά', range(1, len(df_seq)+1))
                st.session_state.draft_sequence = df_seq
                geom, _, _ = get_osrm_data(pts)
                st.session_state.route_geom = geom
                st.rerun()

            if st.session_state.draft_sequence is not None:
                edited = st.data_editor(st.session_state.draft_sequence, use_container_width=True, hide_index=True)
                if st.button("✅ Εφαρμογή Σειράς & Υπολογισμός", type="primary", use_container_width=True):
                    st.session_state.route_data = edited.sort_values('Σειρά').to_dict('records')
                    st.rerun()

            if st.session_state.route_data:
                for i, r in enumerate(st.session_state.route_data):
                    c1, c2 = st.columns([0.7, 0.3])
                    c1.write(f"**{i+1}. {r['Name']}** ({gr_num(r['KG'],0)} KG)")
                    gmaps = f"https://www.google.com/maps/dir/?api=1&destination={r['Lat']},{r['Lon']}"
                    waze = f"https://waze.com/ul?ll={r['Lat']},{r['Lon']}&navigate=yes"
                    c2.markdown(f"[🗺️ G-Maps]({gmaps}) | [🧭 Waze]({waze})")

        with t3:
            st.subheader("Proof of Delivery (POD)")
            active_cust = st.selectbox("Επιλογή Πελάτη", user_data['Name'].unique())
            c_rows = user_data[user_data['Name'] == active_cust]
            
            col1, col2 = st.columns(2)
            if col1.button("▶️ Άφιξη", use_container_width=True):
                st.session_state.start_time = datetime.now(GR_TIME)
                st.success("Άφιξη καταγράφηκε.")
            
            use_cam = st.checkbox("Ενεργοποίηση Κάμερας")
            photo = st.camera_input("📸 Φωτογραφία") if use_cam else None

            if col2.button("⏹️ Sync POD", type="primary", use_container_width=True):
                dur = (datetime.now(GR_TIME) - st.session_state.start_time).total_seconds()/60 if st.session_state.start_time else 0
                new_log = pd.DataFrame([{
                    "Timestamp": datetime.now(GR_TIME).strftime('%Y-%m-%d %H:%M:%S'),
                    "Driver": st.session_state.username, 
                    "Plate": st.session_state.display_plate, 
                    "Customer": active_cust,
                    "Profiles_KG": c_rows[['Unpainted', 'White', 'Colored']].sum().sum(),
                    "Accessories_KG": c_rows['Accessories'].sum(),
                    "Unload_Mins": round(dur, 1)
                }])
                conn.update(spreadsheet=LOG_URL, data=pd.concat([conn.read(spreadsheet=LOG_URL, ttl=0), new_log]))
                st.success(f"POD Sync για {active_cust}!")

        with t4:
            tot = user_data['Total KG'].sum()
            prof = user_data[['Unpainted', 'White', 'Colored']].sum().sum()
            acc = user_data['Accessories'].sum()
            c1, c2, c3 = st.columns(3)
            c1.metric("Σύνολο", f"{gr_num(tot, 1)} KG")
            c2.metric("Προφίλ", f"{gr_num(prof, 1)} KG")
            c3.metric("Εξαρτήματα", f"{gr_num(acc, 1)} KG")
            st.progress(min(tot/24000, 1.0))
            st.caption(f"Load Factor: {gr_num((tot/24000)*100, 1)}%")

        with t5:
            base_url = "https://map-checkin-wmw4nmixyyu8mgfrnhmusm.streamlit.app/"
            track_link = f"{base_url}?track={st.session_state.user_plate}"
            st.info(f"Tracking Link: {track_link}")
            if st.button("📧 Email Ειδοποίησης", use_container_width=True):
                subject = urllib.parse.quote(f"Alumil Delivery: {st.session_state.display_plate}")
                body = urllib.parse.quote(f"Το φορτηγό μας πλησιάζει. Παρακολουθήστε το εδώ: {track_link}")
                st.markdown(f'<a href="mailto:?subject={subject}&body={body}" style="padding:10px; background:#007bff; color:white; border-radius:5px; text-decoration:none;">📧 Αποστολή</a>', unsafe_allow_html=True)

# --- 2. ADMIN DASHBOARD ---
elif app_mode == "📊 Admin Dashboard":
    st.title("Admin Fleet Monitor")
    logs = conn.read(spreadsheet=LOG_URL, ttl=0)
    st.dataframe(logs.tail(20), use_container_width=True)
