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
st.set_page_config(page_title="Alumil Logistics Hub v37", layout="wide", initial_sidebar_state="collapsed")
conn = st.connection("gsheets", type=GSheetsConnection)
GR_TIME = timezone(timedelta(hours=3))

# --- LINKS ΤΩΝ GOOGLE SHEETS ---
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"
CUSADDRESS_URL = "https://docs.google.com/spreadsheets/d/1k9-gCuo_BxVezLaVoagh04xfUoXfj26aH2Mf833qGMk/edit" 
DELIVERIES_URL = "https://docs.google.com/spreadsheets/d/10uKgg3AIuSnROK2-6VnY0Rm3U4vH2xv8O4OFthgaWww/edit"

# --- HELPERS ---
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
    """Επιστρέφει Geometry, Distance (km), Duration (min)"""
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
# 🛑 PUBLIC VIEW: SMART LIVE TRACKING (CUSTOMERS)
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

        # 2. GPS Data Cleaning
        transit_logs['Plate_Clean'] = transit_logs['Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
        v_log = transit_logs[transit_logs['Plate_Clean'] == tracked_plate].copy()
        v_log['Latitude'] = pd.to_numeric(v_log['Latitude'], errors='coerce')
        v_log['Longitude'] = pd.to_numeric(v_log['Longitude'], errors='coerce')
        v_log = v_log.dropna(subset=['Latitude', 'Longitude'])

        if not v_log.empty:
            recent = v_log.tail(10) # Παίρνουμε τα τελευταία 10 σημεία
            curr_pos = (recent.iloc[-1]['Latitude'], recent.iloc[-1]['Longitude'])
            
            # Εύρεση συντεταγμένων στόχου
            t_lat, t_lon = geocode_address(target.get('Street', ''), target['City'])
            
            if t_lat:
                dist = geodesic(curr_pos, (t_lat, t_lon)).km
                
                # 3. GEOFENCING (20km Rule)
                if dist > 20:
                    st.warning(f"🚚 Το φορτηγό απέχει {int(dist)} χλμ. Ο χάρτης θα ενεργοποιηθεί μόλις πλησιάσει στα 20 χλμ.")
                else:
                    st.info(f"✨ Το φορτηγό πλησιάζει! Απόσταση: {gr_num(dist, 1)} χλμ.")
                    m_pub = folium.Map(location=curr_pos, zoom_start=14)
                    
                    # 4. OSRM DRIVING PATH (Breadcrumbs)
                    c_list = [[r['Latitude'], r['Longitude']] for _, r in recent.iterrows()]
                    geom, _, _ = get_osrm_data(c_list)
                    if geom:
                        folium.PolyLine([[p[1], p[0]] for p in geom], color="#E3000F", weight=5, opacity=0.8).add_to(m_pub)
                    
                    folium.Marker(curr_pos, icon=folium.Icon(color='red', icon='truck', prefix='fa')).add_to(m_pub)
                    folium.Marker([t_lat, t_lon], icon=folium.Icon(color='green', icon='home')).add_to(m_pub)
                    st_folium(m_pub, width="100%", height=500, key="public_map")
            else:
                st.warning("Υπολογισμός θέσης προορισμού...")
        else:
            st.warning("Αναμονή για λήψη σήματος GPS...")
    except Exception as e:
        st.error(f"Tracking Error: {e}")
    st.stop()

# ==========================================
# 🚛 PRIVATE VIEW: DRIVER TERMINAL (LOGIN)
# ==========================================
if "password_correct" not in st.session_state: st.session_state.password_correct = False
if "user_plate" not in st.session_state: st.session_state.user_plate = None
if "filter_plate" not in st.session_state: st.session_state.filter_plate = "Όλα"
if "filter_date" not in st.session_state: st.session_state.filter_date = "Όλες"
if "route_data" not in st.session_state: st.session_state.route_data = []
if "draft_sequence" not in st.session_state: st.session_state.draft_sequence = None

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
        ship = pd.merge(ship, dels[['Delivery', 'Act. Gds Mvmnt Date']].drop_duplicates('Delivery'), on='Delivery', how='left')
        ship['Loading_Date'] = ship['Act. Gds Mvmnt Date'].fillna('Άγνωστη Ημ/νία').astype(str)
    except: ship['Loading_Date'] = 'Άγνωστη Ημ/νία'

    try:
        cus = conn.read(spreadsheet=CUSADDRESS_URL, ttl=300)
        cus.columns = cus.columns.str.strip()
        cus['Latitude'] = pd.to_numeric(cus['Latitude'], errors='coerce')
        cus['Longitude'] = pd.to_numeric(cus['Longitude'], errors='coerce')
        df = pd.merge(ship, cus[['Name', 'Street', 'Telephone 1', 'Latitude', 'Longitude']].drop_duplicates('Name'), on='Name', how='left')
    except: df = ship.copy()

    coords_db = conn.read(spreadsheet=COORDS_URL, ttl=300)
    coords_db['City_Match'] = coords_db['City'].astype(str).str.strip().str.upper()
    df = pd.merge(df, coords_db[['City_Match', 'Latitude', 'Longitude']].rename(columns={'Latitude':'Lat_city', 'Longitude':'Lon_city'}), left_on='City_Clean', right_on='City_Match', how='left')
    
    df['Final_Lat'] = df['Latitude'].fillna(df['Lat_city'])
    df['Final_Lon'] = df['Longitude'].fillna(df['Lon_city'])
    
    fleet = df.groupby(['Truck License Plate', 'Plate_Clean', 'Loading_Date'])['Name'].nunique().reset_index(name='Dests')
    return fleet, df

fleet_info, all_data = load_master_data()

# --- SIDEBAR ---
st.sidebar.title("Alumil Hub")
app_mode = st.sidebar.radio("Μενού", ["🚛 Driver Terminal", "📊 Admin Dashboard"])
if st.sidebar.button("🔄 Αλλαγή Οχήματος"):
    st.session_state.user_plate = None
    st.rerun()

# --- 1. DRIVER TERMINAL ---
if app_mode == "🚛 Driver Terminal":
    if st.session_state.user_plate is None:
        st.title("Επιλογή Δρομολογίου")
        
        # Interactive Filtering
        avail_dates = ["Όλες"] + sorted(fleet_info['Loading_Date'].unique().tolist())
        sel_date = st.selectbox("📅 Ημερομηνία", avail_dates)
        f_fleet = fleet_info[fleet_info['Loading_Date'] == sel_date] if sel_date != "Όλες" else fleet_info
        
        plate_opts = [f"{r['Truck License Plate']} ({int(r['Dests'])} Στάσεις)" for _, r in f_fleet.iterrows()]
        sel_p_disp = st.selectbox("🚚 Φορτηγό", plate_opts)
        
        if st.button("🚀 Έναρξη Βάρδιας", type="primary", use_container_width=True):
            st.session_state.user_plate = sel_p_disp.split(' (')[0].replace(' ', '').upper()
            st.session_state.display_plate = sel_p_disp.split(' (')[0]
            st.session_state.sel_date = sel_date
            st.rerun()
    else:
        user_data = all_data[all_data['Plate_Clean'] == st.session_state.user_plate]
        if st.session_state.sel_date != "Όλες":
            user_data = user_data[user_data['Loading_Date'] == st.session_state.sel_date]
        
        gps = get_geolocation()
        curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps else (40.64, 22.94)

        # Auto-Geocoding Engine
        new_coords = []
        for idx, row in user_data.iterrows():
            if pd.isna(row['Latitude']) and str(row.get('Street','')).lower() not in ['nan','']:
                lat, lon = geocode_address(row['Street'], row['City'])
                if lat:
                    user_data.at[idx, 'Final_Lat'] = lat
                    user_data.at[idx, 'Final_Lon'] = lon
                    new_coords.append({'Name': row['Name'], 'Latitude': lat, 'Longitude': lon})

        if new_coords:
            with st.spinner("Ενημέρωση τοποθεσιών..."):
                master_cus = conn.read(spreadsheet=CUSADDRESS_URL, ttl=0)
                for nc in new_coords:
                    master_cus.loc[master_cus['Name'] == nc['Name'], ['Latitude', 'Longitude']] = [nc['Latitude'], nc['Longitude']]
                conn.update(spreadsheet=CUSADDRESS_URL, data=master_cus)

        tab1, tab2, tab3, tab4, tab5 = st.tabs(["🌎 Χάρτης", "🛣️ Δρομολόγηση", "📦 POD", "📊 Analytics", "📩 Ειδοποίηση"])
        
        with tab1:
            m = folium.Map(location=curr_loc, zoom_start=8)
            folium.Marker(curr_loc, icon=folium.Icon(color='green', icon='truck', prefix='fa')).add_to(m)
            for _, r in user_data.drop_duplicates('Name').iterrows():
                if pd.notna(r['Final_Lat']):
                    folium.Marker([r['Final_Lat'], r['Final_Lon']], popup=r['Name']).add_to(m)
            st_folium(m, width="100%", height=500, key="driver_map")

        with tab2:
            st.subheader("Βελτιστοποίηση Διαδρομής")
            if st.button("🤖 Αυτόματη Πρόταση Σειράς", use_container_width=True):
                stops = user_data.drop_duplicates('Name').dropna(subset=['Final_Lat']).copy()
                path = []
                last = curr_loc
                while not stops.empty:
                    stops['d'] = stops.apply(lambda r: geodesic(last, (r['Final_Lat'], r['Final_Lon'])).km, axis=1)
                    next_idx = stops['d'].idxmin()
                    row = stops.loc[next_idx]
                    path.append({'Name': row['Name'], 'KG': row['Total KG'], 'Lat': row['Final_Lat'], 'Lon': row['Final_Lon']})
                    last = (row['Final_Lat'], row['Final_Lon'])
                    stops = stops.drop(next_idx)
                df_seq = pd.DataFrame(path)
                df_seq.insert(0, 'Σειρά', range(1, len(df_seq)+1))
                st.session_state.draft_sequence = df_seq

            if st.session_state.draft_sequence is not None:
                edited = st.data_editor(st.session_state.draft_sequence, use_container_width=True, hide_index=True)
                if st.button("✅ Εφαρμογή Σειράς", type="primary", use_container_width=True):
                    st.session_state.route_data = edited.sort_values('Σειρά').to_dict('records')
                    st.rerun()

            if st.session_state.route_data:
                for i, r in enumerate(st.session_state.route_data):
                    c1, c2 = st.columns([0.7, 0.3])
                    c1.write(f"**{i+1}. {r['Name']}**")
                    gmaps = f"https://www.google.com/maps/dir/?api=1&destination={r['Lat']},{r['Lon']}"
                    c2.markdown(f"[🗺️ Nav]({gmaps})")

        with tab3:
            active_cust = st.selectbox("Πελάτης", user_data['Name'].unique())
            if st.button("⏹️ Sync POD", type="primary", use_container_width=True):
                new_log = pd.DataFrame([{"Timestamp": datetime.now(GR_TIME).strftime('%Y-%m-%d %H:%M:%S'), "Driver": st.session_state.username, "Plate": st.session_state.display_plate, "Customer": active_cust}])
                conn.update(spreadsheet=LOG_URL, data=pd.concat([conn.read(spreadsheet=LOG_URL, ttl=0), new_log]))
                st.success("POD Καταγράφηκε!")

        with tab4:
            st.metric("Συνολικά Κιλά", f"{gr_num(user_data['Total KG'].sum(), 0)} KG")

        with tab5:
            base_url = "https://map-checkin-wmw4nmixyyu8mgfrnhmusm.streamlit.app/"
            track_link = f"{base_url}?track={st.session_state.user_plate}"
            st.info(f"Link: {track_link}")
            if st.button("📧 Email στο επόμενο σημείο", use_container_width=True):
                subject = urllib.parse.quote(f"Alumil Delivery: {st.session_state.display_plate}")
                body = urllib.parse.quote(f"Το φορτηγό μας πλησιάζει. Παρακολουθήστε το εδώ: {track_link}")
                st.markdown(f'<a href="mailto:?subject={subject}&body={body}" style="padding:10px; background:#007bff; color:white; border-radius:5px; text-decoration:none;">📧 Αποστολή</a>', unsafe_allow_html=True)

# --- 2. ADMIN DASHBOARD ---
elif app_mode == "📊 Admin Dashboard":
    st.title("Εποπτεία Logistics")
    logs = conn.read(spreadsheet=LOG_URL, ttl=0)
    st.dataframe(logs.tail(20), use_container_width=True)
