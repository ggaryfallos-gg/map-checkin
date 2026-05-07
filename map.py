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
st.set_page_config(page_title="Alumil Logistics Hub v60", layout="wide", initial_sidebar_state="collapsed")
conn = st.connection("gsheets", type=GSheetsConnection)
GR_TIME = timezone(timedelta(hours=3))

# --- LINKS ΤΩΝ GOOGLE SHEETS ---
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"
CUSADDRESS_URL = "https://docs.google.com/spreadsheets/d/1k9-gCuo_BxVezLaVoagh04xfUoXfj26aH2Mf833qGMk/edit"
DELIVERIES_URL = "https://docs.google.com/spreadsheets/d/10uKgg3AIuSnROK2-6VnY0Rm3U4vH2xv8O4OFthgaWww/edit"

# --- UTILITIES ---
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
    if not coords or len(coords) < 2: return None, 0, 0
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

@st.cache_data(ttl=10)
def get_supplier_pickups():
    try:
        return conn.read(spreadsheet=LOG_URL, worksheet="Supplier_Pickups", ttl=10)
    except:
        return pd.DataFrame()

# ==========================================
# 🛑 PUBLIC VIEW: LIVE TRACKING
# ==========================================
if "track" in st.query_params:
    tracked_plate = st.query_params["track"].replace(' ', '').replace('%20', '').upper()
    st.title("📍 Alumil Live Delivery Tracking")
    try:
        transit = conn.read(spreadsheet=LOG_URL, worksheet="Transit_Log", ttl=5)
        transit.columns = transit.columns.str.strip()
        transit['Plate_Clean'] = transit['Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
        vehicle_log = transit[transit['Plate_Clean'] == tracked_plate]
        if not vehicle_log.empty:
            recent_logs = vehicle_log.tail(10)
            path_coords = [[float(row.get('Latitude', row.iloc[3])), float(row.get('Longitude', row.iloc[4]))] for _, row in recent_logs.iterrows()]
            if path_coords:
                curr_lat, curr_lon = path_coords[-1]
                st.info(f"Τελευταία ενημέρωση: **{recent_logs.iloc[-1].get('Timestamp', 'N/A')}**")
                m_public = folium.Map(location=[curr_lat, curr_lon], zoom_start=14)
                folium.Marker([curr_lat, curr_lon], icon=folium.Icon(color='green', icon='truck', prefix='fa')).add_to(m_public)
                st_folium(m_public, width="100%", height=500)
    except: st.warning("Αναμονή για GPS...")
    st.stop()

# --- SESSION STATE INIT ---
keys_to_init = {
    "password_correct": False, "inspected": False, "user_plate": None, 
    "loading_date": None, "username": None, "route_data": [], 
    "route_geom": None, "start_time": None, "display_plate": None
}
for key, val in keys_to_init.items():
    if key not in st.session_state: st.session_state[key] = val

# --- LOGIN SCREEN ---
if not st.session_state.password_correct:
    st.title("🔐 Alumil Secure Login")
    pwd = st.text_input("Προσωπικός Κωδικός", type="password")
    if st.button("Είσοδος", use_container_width=True):
        if "passwords" in st.secrets and pwd in st.secrets["passwords"]:
            st.session_state.password_correct = True
            st.session_state.username = st.secrets["passwords"][pwd]
            st.rerun()
    st.stop()

# --- DATA LOADING ---
@st.cache_data(ttl=300)
def load_full_data():
    ship = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    ship.columns = ship.columns.str.strip()
    ship['Plate_Clean'] = ship['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    ship['City_Clean'] = ship['City'].astype(str).str.strip().str.upper()
    for c in ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']:
        if c in ship.columns: ship[c] = ship[c].apply(clean_val)
    
    try:
        dels = conn.read(spreadsheet=DELIVERIES_URL, ttl=300)
        dels.columns = dels.columns.str.strip()
        dels['Delivery'] = dels['Delivery'].astype(str).str.strip().str.replace('.0', '', regex=False).str.lstrip('0')
        ship['Delivery'] = ship['Delivery'].astype(str).str.strip().str.replace('.0', '', regex=False).str.lstrip('0')
        ship = pd.merge(ship, dels[['Delivery', 'Act. Gds Mvmnt Date']].drop_duplicates('Delivery'), on='Delivery', how='left')
        ship['Loading_Date'] = ship['Act. Gds Mvmnt Date'].fillna('Άγνωστη').astype(str)
    except: ship['Loading_Date'] = 'Άγνωστη'

    try:
        cus_df = conn.read(spreadsheet=CUSADDRESS_URL, ttl=300)
        cus_df_sub = cus_df[['Name', 'Street', 'Telephone 1', 'Latitude', 'Longitude']].drop_duplicates('Name')
        cus_df_sub = cus_df_sub.rename(columns={'Latitude': 'Lat_exact', 'Longitude': 'Lon_exact'})
        df = pd.merge(ship, cus_df_sub, on='Name', how='left')
    except: df = ship.copy()

    coords = conn.read(spreadsheet=COORDS_URL, ttl=300)
    coords['City_Match'] = coords['City'].astype(str).str.strip().str.upper()
    df = pd.merge(df, coords[['City_Match', 'Latitude', 'Longitude']].drop_duplicates('City_Match').rename(columns={'Latitude':'Lat_city', 'Longitude':'Lon_city'}), left_on='City_Clean', right_on='City_Match', how='left')
    
    df['Final_Lat'] = df['Lat_exact'].fillna(df['Lat_city'])
    df['Final_Lon'] = df['Lon_exact'].fillna(df['Lon_city'])
    
    fleet_summary = df.groupby(['Truck License Plate', 'Plate_Clean', 'Loading_Date'])['Name'].nunique().reset_index(name='Dests')
    return fleet_summary, df

fleet_info, all_data = load_full_data()

# --- SIDEBAR ---
st.sidebar.title("Alumil Hub")
st.sidebar.write(f"👤 {st.session_state.username}")
if st.session_state.user_plate:
    if st.sidebar.button("🔄 Reset Shift"):
        for k in ["user_plate", "inspected", "route_data", "start_time"]: st.session_state[k] = keys_to_init[k]
        st.rerun()
app_mode = st.sidebar.radio("Μενού", ["🚛 Driver Terminal", "📊 Admin Dashboard"])

# ==========================================
# 🚛 1. DRIVER TERMINAL
# ==========================================
if app_mode == "🚛 Driver Terminal":
    if st.session_state.user_plate is None:
        st.title("Επιλογή Δρομολογίου")
        col1, col2 = st.columns(2)
        plates = sorted(all_data['Truck License Plate'].dropna().unique().tolist())
        p_sel = col1.selectbox("🚚 Φορτηγό", plates)
        dates = all_data[all_data['Truck License Plate'] == p_sel]['Loading_Date'].unique()
        d_sel = col2.selectbox("📅 Ημερομηνία", dates)
        if st.button("🚀 Έναρξη Βάρδιας", type="primary", use_container_width=True):
            st.session_state.user_plate = p_sel.replace(' ', '').upper()
            st.session_state.display_plate = p_sel
            st.session_state.loading_date = d_sel
            st.rerun()
            
    elif not st.session_state.inspected:
        st.header("🛡️ Safety Inspection")
        with st.container(border=True):
            st.write(f"Έλεγχος για το όχημα: **{st.session_state.display_plate}**")
            c1 = st.checkbox("🛞 Ελαστικά"); c2 = st.checkbox("🛢️ Λάδια / Ψυκτικό"); c3 = st.checkbox("📂 Έγγραφα"); c4 = st.checkbox("💧 AdBlue")
            issues = st.text_area("Παρατηρήσεις")
            if st.button("🏁 Ολοκλήρωση & Εκκίνηση", type="primary", use_container_width=True):
                if c1 and c2 and c3 and c4:
                    st.session_state.inspected = True
                    st.rerun()
                else: st.error("⚠️ Επιλέξτε όλα τα πεδία.")
        st.stop()

    else:
        st.subheader(f"🚚 {st.session_state.display_plate} | {st.session_state.loading_date}")
        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["🌎 Χάρτης", "🛣️ Δρομολόγηση", "📦 POD Protocol", "📊 Analytics", "📩 Alert", "🏭 Παραλαβές"])
        
        user_data = all_data[(all_data['Plate_Clean'] == st.session_state.user_plate) & (all_data['Loading_Date'] == st.session_state.loading_date)].copy()
        gps = get_geolocation()
        curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps and 'coords' in gps else (41.0, 22.8)

        with tab1:
            m1 = folium.Map(location=curr_loc, zoom_start=7)
            folium.Marker(curr_loc, icon=folium.Icon(color='green', icon='truck', prefix='fa')).add_to(m1)
            for _, r in user_data.drop_duplicates('Name').iterrows():
                if pd.notna(r['Final_Lat']):
                    folium.Marker([r['Final_Lat'], r['Final_Lon']], popup=r['Name'], icon=folium.Icon(color='blue')).add_to(m1)
            st_folium(m1, width="100%", height=500, key="driver_map")

        with tab3:
            st.subheader("Sync POD Data")
            active_cust = st.selectbox("Πελάτης", sorted(user_data['Name'].unique()))
            if st.button("⏹️ Sync POD", type="primary", use_container_width=True):
                new_log = pd.DataFrame([{"Timestamp": datetime.now(GR_TIME).strftime('%Y-%m-%d %H:%M:%S'), "Driver": st.session_state.username, "Plate": st.session_state.display_plate, "Customer": active_cust}])
                try:
                    old_log = conn.read(spreadsheet=LOG_URL, worksheet="Log", ttl=0)
                    conn.update(spreadsheet=LOG_URL, worksheet="Log", data=pd.concat([old_log, new_log], ignore_index=True))
                    st.success("✅ Συγχρονίστηκε!")
                except: st.error("Σφάλμα στο tab 'Log'")

        with tab6:
            st.subheader("📦 Παραλαβές Προμηθευτών")
            pickups = get_supplier_pickups()
            if not pickups.empty:
                my_p = pickups[(pickups['Assigned_Plate'].str.replace(' ','').str.upper() == st.session_state.user_plate) & (pickups['Status'] == 'Assigned')]
                st.dataframe(my_p[['Supplier_Name', 'Address', 'Area', 'Date']], use_container_width=True)
            else: st.info("Καμία παραλαβή.")

# ==========================================
# 📊 2. ADMIN DASHBOARD
# ==========================================
elif app_mode == "📊 Admin Dashboard":
    st.title("Admin Logistics Control Tower")
    at1, at2, at3 = st.tabs(["📈 Planning", "📦 Διαχείριση Παραλαβών", "📡 GPS Logs"])

    with at1:
        st.header("Capacity Planning")
        if not all_data.empty:
            plates = sorted(all_data['Truck License Plate'].dropna().unique().tolist())
            sel = st.multiselect("Επιλογή Φορτηγών", plates, default=plates[:3])
            plan_df = all_data[all_data['Truck License Plate'].isin(sel)]
            if not plan_df.empty:
                pivot = plan_df.pivot_table(index='Truck License Plate', columns='Loading_Date', values='Total KG', aggfunc='sum').fillna(0)
                st.dataframe(pivot.style.background_gradient(cmap='YlOrRd', axis=None).format(lambda x: f"{int(x)} kg"), use_container_width=True)

    with at2:
        st.header("Παραλαβές")
        with st.expander("➕ Νέα Καταχώρηση"):
            with st.form("new_p"):
                n = st.text_input("Προμηθευτής"); a = st.text_input("Διεύθυνση"); ar = st.selectbox("Περιοχή", ["Σίνδος", "Οινόφυτα", "Ασπρόπυργος"])
                if st.form_submit_button("Αποθήκευση") and n and a:
                    new_e = pd.DataFrame([{"ID": int(time.time()), "Date": datetime.now().strftime("%d/%m/%Y"), "Supplier_Name": n, "Address": a, "Area": ar, "Status": "Pending", "Assigned_Plate": ""}])
                    old_p = get_supplier_pickups()
                    conn.update(spreadsheet=LOG_URL, worksheet="Supplier_Pickups", data=pd.concat([old_p, new_e], ignore_index=True))
                    st.success("✅ Καταχωρήθηκε!"); st.rerun()
        
        st.divider()
        p_df = get_supplier_pickups()
        if not p_df.empty:
            edited = st.data_editor(p_df, column_config={"Status": st.column_config.SelectboxColumn("Status", options=["Pending", "Assigned", "Collected"]), "Assigned_Plate": st.column_config.SelectboxColumn("Φορτηγό", options=sorted(all_data['Truck License Plate'].dropna().unique().tolist()))}, hide_index=True)
            if st.button("💾 Αποθήκευση Αλλαγών"):
                conn.update(spreadsheet=LOG_URL, worksheet="Supplier_Pickups", data=edited.fillna(""))
                st.success("Ενημερώθηκε!")

    with at3:
        try:
            logs = conn.read(spreadsheet=LOG_URL, worksheet="Transit_Log", ttl=5)
            st.dataframe(logs.tail(20), use_container_width=True)
        except: st.error("Transit_Log Error")
