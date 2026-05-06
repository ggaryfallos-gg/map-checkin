import streamlit as st
import pandas as pd
import folium
import requests
import urllib.parse
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation
from geopy.distance import geodesic
from datetime import datetime, timedelta, timezone
from streamlit_gsheets import GSheetsConnection

# --- CONFIG & TIMEZONE ---
st.set_page_config(page_title="Alumil Logistics Hub v15", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)
GR_TIME = timezone(timedelta(hours=3))

# URLs
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"

# --- SESSION STATE INIT ---
if "password_correct" not in st.session_state: st.session_state.password_correct = False
if "user_plate" not in st.session_state: st.session_state.user_plate = None
if "route_data" not in st.session_state: st.session_state.route_data = []
if "route_geom" not in st.session_state: st.session_state.route_geom = None

# --- LOGIN SCREEN ---
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

# --- UTILITIES ---
def get_osrm_data(coords):
    locs = ";".join([f"{lon},{lat}" for lat, lon in coords])
    url = f"http://router.project-osrm.org/route/v1/driving/{locs}?overview=full&geometries=geojson"
    try:
        r = requests.get(url, timeout=10).json()
        if r['code'] == 'Ok':
            return r['routes'][0]['geometry']['coordinates'], r['routes'][0]['distance']/1000, r['routes'][0]['duration']/60
    except: pass
    return None, 0, 0

@st.cache_data(ttl=300)
def load_full_data():
    ship = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    ship['Plate_Clean'] = ship['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    for c in ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']:
        ship[c] = pd.to_numeric(ship[c].astype(str).str.replace('.', '').str.replace(',', '.'), errors='coerce').fillna(0)
    coords = conn.read(spreadsheet=COORDS_URL, ttl=300)
    coords['City'] = coords['City'].astype(str).str.strip().str.upper()
    return pd.merge(ship, coords.drop_duplicates('City'), left_on=ship['City'].str.strip().str.upper(), right_on='City', how='left')

all_data = load_full_data()

# --- SIDEBAR ---
app_mode = st.sidebar.radio("Μενού", ["🚛 Driver Terminal", "📊 Admin Dashboard"])
if st.sidebar.button("Logout"):
    st.session_state.password_correct = False
    st.session_state.user_plate = None
    st.rerun()

# --- DRIVER TERMINAL ---
if app_mode == "🚛 Driver Terminal":
    if st.session_state.user_plate is None:
        st.title("Επιλογή Οχήματος")
        fleet = all_data[['Truck License Plate', 'Plate_Clean']].drop_duplicates()
        sel = st.selectbox("Πινακίδα", fleet['Truck License Plate'])
        if st.button("Έναρξη", type="primary"):
            st.session_state.user_plate = sel.replace(' ', '').upper()
            st.session_state.display_plate = sel
            st.rerun()
    else:
        user_data = all_data[all_data['Plate_Clean'] == st.session_state.user_plate]
        gps = get_geolocation()
        curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps and 'coords' in gps else (41.0, 22.8)

        t1, t2, t3, t4 = st.tabs(["🗺️ Δρομολόγηση", "📦 POD Protocol", "📊 Analytics", "📩 Ειδοποίηση"])

        with t1:
            if st.button("🚀 Υπολογισμός Διαδρομής & Χρόνων", use_container_width=True):
                stops = user_data.drop_duplicates('Name').dropna(subset=['Latitude'])
                pts, sequence = [curr_loc], []
                unvisited = stops.copy()
                while not unvisited.empty:
                    unvisited['d'] = unvisited.apply(lambda r: geodesic(pts[-1], (r['Latitude'], r['Longitude'])).km, axis=1)
                    idx = unvisited['d'].idxmin()
                    row = unvisited.loc[idx]
                    pts.append((row['Latitude'], row['Longitude']))
                    # Estimation: 10 mins per tone
                    un_time = (row['Total KG'] / 1000) * 10
                    sequence.append({'name': row['Name'], 'city': row['City_x'], 'kg': row['Total KG'], 'unload': un_time, 'coords': (row['Latitude'], row['Longitude'])})
                    unvisited = unvisited.drop(index=idx)
                
                for i in range(len(sequence)):
                    _, _, d_min = get_osrm_data([pts[i], pts[i+1]])
                    sequence[i]['drive_to'] = d_min
                
                st.session_state.route_data = sequence
                geom, _, _ = get_osrm_data(pts)
                st.session_state.route_geom = geom
                st.rerun()

            if st.session_state.route_data:
                for i, s in enumerate(st.session_state.route_data):
                    st.write(f"**{i+1}. {s['name']}**: 🚛 {int(s['drive_to'])}' | 🏗️ {int(s['unload'])}'")
                m = folium.Map(location=curr_loc, zoom_start=7)
                if st.session_state.route_geom:
                    folium.PolyLine([[l, lon] for lon, l in st.session_state.route_geom], color="blue").add_to(m)
                st_folium(m, width="100%", height=400)

        with t3:
            st.subheader("Analytics Φορτίου")
            tot = user_data['Total KG'].sum()
            prof = user_data[['Unpainted', 'White', 'Colored']].sum().sum()
            acc = user_data['Accessories'].sum()
            c1, c2, c3 = st.columns(3)
            c1.metric("Σύνολο", f"{tot:,.0f} KG")
            c2.metric("Προφίλ", f"{prof:,.0f} KG")
            c3.metric("Εξαρτήματα", f"{acc:,.0f} KG")
            st.progress(min(tot/24000, 1.0))

        with t4:
            st.subheader("📩 Ειδοποίηση Επόμενου Πελάτη")
            active = st.selectbox("Τρέχων Πελάτης", [s['name'] for s in st.session_state.route_data]) if st.session_state.route_data else None
            if active and st.session_state.route_data:
                idx = [s['name'] for s in st.session_state.route_data].index(active)
                if idx < len(st.session_state.route_data) - 1:
                    nxt = st.session_state.route_data[idx+1]
                    curr_unload = next(s['unload'] for s in st.session_state.route_data if s['name'] == active)
                    total_wait = int(curr_unload + nxt['drive_to'])
                    
                    body = f"Γεια σας από την Alumil. Ξεκινήσαμε εκφόρτωση στον προηγούμενο πελάτη. Εκτιμώμενη άφιξη σε εσάς σε {total_wait} λεπτά. Όχημα: {st.session_state.display_plate}."
                    st.info(body)
                    
                    # MAILTO LINK
                    mail_link = f"mailto:?subject=Ενημέρωση Παράδοσης Alumil&body={urllib.parse.quote(body)}"
                    st.markdown(f'<a href="{mail_link}" target="_blank" style="padding: 10px; background-color: #007bff; color: white; border-radius: 5px; text-decoration: none;">📧 Αποστολή Email</a>', unsafe_allow_html=True)
                else:
                    st.write("Δεν υπάρχει επόμενος πελάτης στο δρομολόγιο.")

# --- ADMIN DASHBOARD ---
elif app_mode == "📊 Admin Dashboard":
    st.title("Admin Control Panel")
    logs = conn.read(spreadsheet=LOG_URL, ttl=0)
    st.dataframe(logs.tail(15), use_container_width=True)
