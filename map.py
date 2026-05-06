import streamlit as st
import pandas as pd
import folium
import requests
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation
from geopy.distance import geodesic
from datetime import datetime
from streamlit_gsheets import GSheetsConnection

# --- ΡΥΘΜΙΣΕΙΣ ΕΦΑΡΜΟΓΗΣ ---
st.set_page_config(page_title="Alumil Logistics Hub v13", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)

# Google Sheets URLs
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"

# --- ΑΡΧΙΚΟΠΟΙΗΣΗ SESSION STATE ---
if "password_correct" not in st.session_state: st.session_state.password_correct = False
if "user_plate" not in st.session_state: st.session_state.user_plate = None
if "display_plate" not in st.session_state: st.session_state.display_plate = None
if "start_time" not in st.session_state: st.session_state.start_time = None
if "username" not in st.session_state: st.session_state.username = None
if "route_geom" not in st.session_state: st.session_state.route_geom = None
if "route_km" not in st.session_state: st.session_state.route_km = 0
if "route_pts" not in st.session_state: st.session_state.route_pts = []

# --- ΣΥΣΤΗΜΑ ΑΣΦΑΛΕΙΑΣ ---
def check_password():
    if st.session_state.password_correct: return True
    st.title("🔐 Alumil Logistics Secure Login")
    pwd = st.text_input("Εισάγετε τον προσωπικό σας κωδικό", type="password")
    if st.button("Είσοδος", use_container_width=True):
        if "passwords" in st.secrets and pwd in st.secrets["passwords"]:
            st.session_state.password_correct = True
            st.session_state.username = st.secrets["passwords"][pwd]
            st.rerun()
        else: st.error("❌ Λανθασμένος κωδικός.")
    return False

if not check_password(): st.stop()

# --- UTILITIES ---
def get_driving_route_osrm(coords_list):
    loc_string = ";".join([f"{lon},{lat}" for lat, lon in coords_list])
    url = f"http://router.project-osrm.org/route/v1/driving/{loc_string}?overview=full&geometries=geojson"
    try:
        r = requests.get(url, timeout=10)
        res = r.json()
        if res['code'] == 'Ok':
            geometry = res['routes'][0]['geometry']['coordinates']
            return [[lat, lon] for lon, lat in geometry], res['routes'][0]['distance'] / 1000
    except: pass
    return None, 0

def clean_sheet_numeric(series):
    s = series.astype(str).str.replace(' ', '', regex=False)
    def parse_mixed(val):
        if not val or val in ['nan', '', 'None']: return 0.0
        try: return float(val.replace('.', '').replace(',', '.'))
        except: return 0.0
    return s.apply(parse_mixed)

# --- DATA PIPELINE ---
@st.cache_data(ttl=300)
def load_data():
    shipments = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    shipments['Plate_Clean'] = shipments['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    shipments['City_Clean'] = shipments['City'].astype(str).str.strip().str.upper()
    for col in ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']:
        if col in shipments.columns: shipments[col] = clean_sheet_numeric(shipments[col])
    coords_db = conn.read(spreadsheet=COORDS_URL, ttl=300)
    coords_db['City'] = coords_db['City'].astype(str).str.strip().str.upper()
    coords_db = coords_db.drop_duplicates(subset=['City'], keep='last')
    shipments = pd.merge(shipments, coords_db, left_on='City_Clean', right_on='City', how='left')
    unique_plates = shipments[['Truck License Plate', 'Plate_Clean']].drop_duplicates()
    counts = shipments.groupby('Plate_Clean')['City_Clean'].nunique().reset_index(name='Dests')
    fleet = pd.merge(unique_plates, counts, on='Plate_Clean', how='left').fillna(0)
    fleet['Label'] = fleet.apply(lambda r: f"{r['Truck License Plate']} ({int(r['Dests'])} Στάσεις)", axis=1)
    return fleet.sort_values(by='Dests', ascending=False), shipments

fleet_info, all_shipments = load_data()

# --- SIDEBAR & MENU ---
st.sidebar.title("Alumil Menu")
st.sidebar.write(f"👤 Χρήστης: **{st.session_state.username}**")
app_mode = st.sidebar.radio("Επιλογή Λειτουργίας", ["🚛 Driver Terminal", "📊 Admin Dashboard"])

if st.sidebar.button("Logout"):
    st.session_state.password_correct = False
    st.session_state.user_plate = None
    st.rerun()

# --- 1. DRIVER TERMINAL ---
if app_mode == "🚛 Driver Terminal":
    if st.session_state.user_plate is None:
        st.title("Έναρξη Βάρδιας")
        sel = st.selectbox("Επιλέξτε Φορτηγό", fleet_info['Label'])
        if st.button("Επιβεβαίωση Οχήματος", type="primary", use_container_width=True):
            row = fleet_info[fleet_info['Label'] == sel].iloc[0]
            st.session_state.user_plate = row['Plate_Clean']
            st.session_state.display_plate = row['Truck License Plate']
            st.rerun()
    else:
        st.subheader(f"🚚 Ενεργό Όχημα: {st.session_state.display_plate}")
        user_data = all_shipments[all_shipments['Plate_Clean'] == st.session_state.user_plate]
        gps = get_geolocation()
        curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps and 'coords' in gps else (41.0, 22.8)

        tab1, tab2, tab3, tab4 = st.tabs(["🌎 Χάρτης", "🗺️ Δρομολόγηση", "📦 POD Protocol", "📊 Analytics"])

        with tab1:
            m1 = folium.Map(location=curr_loc, zoom_start=7)
            for _, r in user_data.drop_duplicates(subset=['City_Clean']).iterrows():
                if pd.notna(r['Latitude']):
                    folium.Marker([r['Latitude'], r['Longitude']], popup=r['City_Clean']).add_to(m1)
            st_folium(m1, width="100%", height=450, key="ov_map")

        with tab2:
            if st.button("🚀 Υπολογισμός Οδικής Διαδρομής (OSRM)", use_container_width=True):
                stops = user_data.drop_duplicates(subset=['City_Clean']).dropna(subset=['Latitude'])
                pts = [curr_loc]
                unvisited = stops.copy()
                while not unvisited.empty:
                    unvisited['d'] = unvisited.apply(lambda r: geodesic(pts[-1], (r['Latitude'], r['Longitude'])).km, axis=1)
                    idx = unvisited['d'].idxmin()
                    pts.append((unvisited.loc[idx, 'Latitude'], unvisited.loc[idx, 'Longitude']))
                    unvisited = unvisited.drop(index=idx)
                geom, km = get_driving_route_osrm(pts)
                if geom:
                    st.session_state.route_geom, st.session_state.route_km, st.session_state.route_pts = geom, km, pts
                    st.rerun()

            if st.session_state.route_geom:
                m2 = folium.Map(location=curr_loc, zoom_start=7)
                folium.PolyLine(st.session_state.route_geom, color="blue", weight=5).add_to(m2)
                for i, p in enumerate(st.session_state.route_pts):
                    folium.Marker(p, popup=f"Στάση {i}", icon=folium.Icon(color='red' if i==0 else 'blue')).add_to(m2)
                st_folium(m2, width="100%", height=500, key="road_map")
                st.success(f"Απόσταση: {st.session_state.route_km:.1f} km")

        with tab3:
            cust = st.selectbox("Επιλογή Πελάτη", sorted(user_data['Name'].unique()))
            cust_rows = user_data[user_data['Name'] == cust]
            use_cam = st.checkbox("Άνοιγμα Κάμερας")
            photo = st.camera_input("📸 Φωτογραφία") if use_cam else None
            
            c1, c2 = st.columns(2)
            if c1.button("▶️ Άφιξη", use_container_width=True):
                st.session_state.start_time = datetime.now()
                st.toast("Η ώρα άφιξης καταγράφηκε.")
            if c2.button("⏹️ Sync POD", type="primary", use_container_width=True):
                if st.session_state.start_time:
                    dur = (datetime.now() - st.session_state.start_time).total_seconds() / 60
                    p_kg = cust_rows[['Unpainted', 'White', 'Colored']].sum().sum()
                    a_kg = cust_rows['Accessories'].sum()
                    new_log = pd.DataFrame([{
                        "Timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 
                        "Driver": st.session_state.username, "Plate": st.session_state.display_plate, 
                        "Customer": cust, "Profiles_KG": p_kg, "Accessories_KG": a_kg,
                        "Transit_Mins": 0, "Unload_Mins": round(dur, 1), 
                        "Checkin_Lat": curr_loc[0], "Checkin_Lon": curr_loc[1], "Photo": "Yes" if photo else "No"
                    }])
                    conn.update(spreadsheet=LOG_URL, data=pd.concat([conn.read(spreadsheet=LOG_URL, ttl=0), new_log], ignore_index=True))
                    st.success("✅ Συγχρονίστηκε!")
                    st.session_state.start_time = None
                else: st.error("❌ Πατήστε 'Άφιξη' πρώτα!")

        with tab4:
            st.subheader("Executive Analytics")
            tot_kg = user_data['Total KG'].sum()
            p_kg = user_data[['Unpainted', 'White', 'Colored']].sum().sum()
            a_kg = user_data['Accessories'].sum()
            c1, c2, c3 = st.columns(3)
            c1.metric("Συνολικό Βάρος", f"{tot_kg:,.1f} KG")
            c2.metric("Προφίλ", f"{p_kg:,.1f} KG")
            c3.metric("Εξαρτήματα", f"{a_kg:,.1f} KG")
            st.divider()
            st.progress(min(tot_kg/24000, 1.0))
            st.caption(f"Payload Factor: {(tot_kg/24000)*100:.1f}%")

# --- 2. ADMIN DASHBOARD ---
elif app_mode == "📊 Admin Dashboard":
    st.title("Admin Control Panel")
    logs_df = conn.read(spreadsheet=LOG_URL, ttl=0)
    st.subheader("Recent Activity Logs")
    st.dataframe(logs_df.tail(20), use_container_width=True)
    
    st.divider()
    st.subheader("📍 Live Fleet Tracking (Transit Log)")
    transit_df = conn.read(spreadsheet=LOG_URL, worksheet="Transit_Log", ttl=0)
    if not transit_df.empty:
        st.dataframe(transit_df.tail(10), use_container_width=True)
        m_admin = folium.Map(location=[transit_df['Latitude'].iloc[-1], transit_df['Longitude'].iloc[-1]], zoom_start=6)
        for _, r in transit_df.tail(15).iterrows():
            folium.Marker([r['Latitude'], r['Longitude']], popup=f"{r['Driver']} ({r['Timestamp']})", icon=folium.Icon(color='green', icon='truck', prefix='fa')).add_to(m_admin)
        st_folium(m_admin, width="100%", height=400, key="admin_map")
