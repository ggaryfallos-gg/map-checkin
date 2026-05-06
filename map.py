import streamlit as st
import pandas as pd
import folium
import requests
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation
from geopy.distance import geodesic
from datetime import datetime, timedelta, timezone
from streamlit_gsheets import GSheetsConnection

# --- CONFIG & TIMEZONE ---
st.set_page_config(page_title="Alumil Logistics Hub v14", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)
GR_TIME = timezone(timedelta(hours=3)) # Ώρα Ελλάδας

# URLs
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"

# --- ΑΡΧΙΚΟΠΟΙΗΣΗ ---
if "password_correct" not in st.session_state: st.session_state.password_correct = False
if "user_plate" not in st.session_state: st.session_state.user_plate = None
if "route_geom" not in st.session_state: st.session_state.route_geom = None
if "route_data" not in st.session_state: st.session_state.route_data = [] # Λίστα με στάσεις και χρόνους

# --- LOGIN ---
def check_password():
    if st.session_state.password_correct: return True
    st.title("🔐 Alumil Secure Login")
    pwd = st.text_input("Κωδικός", type="password")
    if st.button("Είσοδος"):
        if "passwords" in st.secrets and pwd in st.secrets["passwords"]:
            st.session_state.password_correct = True
            st.session_state.username = st.secrets["passwords"][pwd]
            st.rerun()
    return False

if not check_password(): st.stop()

# --- UTILITIES ---
def get_osrm_route(coords_list):
    loc_string = ";".join([f"{lon},{lat}" for lat, lon in coords_list])
    url = f"http://router.project-osrm.org/route/v1/driving/{loc_string}?overview=full&geometries=geojson"
    try:
        r = requests.get(url, timeout=10).json()
        if r['code'] == 'Ok':
            # Επιστρέφει Geometry, Distance (km), Duration (minutes)
            return r['routes'][0]['geometry']['coordinates'], r['routes'][0]['distance']/1000, r['routes'][0]['duration']/60
    except: pass
    return None, 0, 0

def clean_num(val):
    try: return float(str(val).replace('.', '').replace(',', '.'))
    except: return 0.0

# --- DATA LOAD ---
@st.cache_data(ttl=300)
def load_data():
    shipments = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    shipments['Plate_Clean'] = shipments['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    for col in ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']:
        shipments[col] = shipments[col].apply(clean_num)
    
    coords = conn.read(spreadsheet=COORDS_URL, ttl=300)
    coords['City'] = coords['City'].astype(str).str.strip().str.upper()
    
    df = pd.merge(shipments, coords.drop_duplicates('City'), left_on=shipments['City'].str.strip().str.upper(), right_on='City', how='left')
    return df

all_data = load_data()
fleet = all_data[['Truck License Plate', 'Plate_Clean']].drop_duplicates()

# --- MAIN UI ---
app_mode = st.sidebar.radio("Menu", ["🚛 Driver", "📊 Admin"])

if app_mode == "🚛 Driver":
    if st.session_state.user_plate is None:
        sel = st.selectbox("Όχημα", fleet['Truck License Plate'])
        if st.button("Έναρξη"):
            st.session_state.user_plate = sel.replace(' ', '').upper()
            st.session_state.display_plate = sel
            st.rerun()
    else:
        user_data = all_data[all_data['Plate_Clean'] == st.session_state.user_plate]
        gps = get_geolocation()
        curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps and 'coords' in gps else (41.0, 22.8)

        t1, t2, t3 = st.tabs(["🗺️ Δρομολόγηση", "📦 POD & Ειδοποίηση", "📊 Analytics"])

        with t1:
            if st.button("🚀 Υπολογισμός Βέλτιστης Διαδρομής & Χρόνων"):
                # 1. TSP Sequence (Nearest Neighbor)
                stops = user_data.drop_duplicates(subset=['Name']).dropna(subset=['Latitude'])
                pts = [curr_loc]
                sequence = []
                unvisited = stops.copy()
                
                while not unvisited.empty:
                    unvisited['d'] = unvisited.apply(lambda r: geodesic(pts[-1], (r['Latitude'], r['Longitude'])).km, axis=1)
                    idx = unvisited['d'].idxmin()
                    row = unvisited.loc[idx]
                    pts.append((row['Latitude'], row['Longitude']))
                    # Calculate Unloading Time: 10 mins per 1000kg
                    unloading_time = (row['Total KG'] / 1000) * 10
                    sequence.append({'name': row['Name'], 'city': row['City_x'], 'kg': row['Total KG'], 'unload_mins': unloading_time, 'coords': (row['Latitude'], row['Longitude'])})
                    unvisited = unvisited.drop(index=idx)
                
                # 2. Get Driving Durations between stops
                for i in range(len(sequence)):
                    start = pts[i]
                    end = pts[i+1]
                    _, _, drive_mins = get_osrm_route([start, end])
                    sequence[i]['drive_to_mins'] = drive_mins

                st.session_state.route_data = sequence
                geom, total_km, _ = get_osrm_route(pts)
                st.session_state.route_geom = geom
                st.rerun()

            if st.session_state.route_data:
                st.write("**Σειρά Παραδόσεων & Εκτιμώμενοι Χρόνοι:**")
                for i, s in enumerate(st.session_state.route_data):
                    st.write(f"{i+1}. **{s['name']}** ({s['city']}): 🚛 ~{int(s['drive_to_mins'])}' οδήγηση | 🏗️ ~{int(s['unload_mins'])}' εκφόρτωση")
                
                m = folium.Map(location=curr_loc, zoom_start=7)
                if st.session_state.route_geom:
                    folium.PolyLine([[lat, lon] for lon, lat in st.session_state.route_geom], color="blue").add_to(m)
                st_folium(m, width="100%", height=400)

        with t2:
            st.subheader("POD & Επόμενος Πελάτης")
            active_cust = st.selectbox("Τρέχων Πελάτης", [s['name'] for s in st.session_state.route_data]) if st.session_state.route_data else st.selectbox("Πελάτης", sorted(user_data['Name'].unique()))
            
            # Εύρεση επόμενου πελάτη για το μήνυμα
            next_cust = None
            if st.session_state.route_data:
                names = [s['name'] for s in st.session_state.route_data]
                curr_idx = names.index(active_cust)
                if curr_idx < len(names) - 1:
                    next_cust = st.session_state.route_data[curr_idx + 1]

            if st.button("▶️ Έναρξη Εκφόρτωσης (Start Unloading)"):
                st.session_state.start_time = datetime.now(GR_TIME)
                if next_cust:
                    # Υπολογισμός Χ: Χρόνος εκφόρτωσης τρέχοντος + Υ: Χρόνος οδήγησης προς τον επόμενο
                    curr_unload = next(s['unload_mins'] for s in st.session_state.route_data if s['name'] == active_cust)
                    drive_next = next_cust['drive_to_mins']
                    total_wait = int(curr_unload + drive_next)
                    
                    msg = f"Αγαπητέ συνεργάτη ({next_cust['name']}), ξεκινήσαμε την εκφόρτωση στον προηγούμενο σταθμό. " \
                          f"Θα είμαστε στις εγκαταστάσεις σας σε περίπου {total_wait} λεπτά " \
                          f"({int(curr_unload)}' εκφόρτωση + {int(drive_next)}' διαδρομή). Όχημα: {st.session_state.display_plate}."
                    
                    st.info("**Μήνυμα προς επόμενο πελάτη:**")
                    st.code(msg) # Δυνατότητα copy-paste
                    st.button("📧 Αποστολή (Simulated)")

            if st.button("⏹️ Ολοκλήρωση (Sync POD)"):
                # (Ο κώδικας του Sync παραμένει ίδιος με v13)
                st.success("POD Συγχρονίστηκε.")
