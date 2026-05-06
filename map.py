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
st.set_page_config(page_title="Alumil Logistics: POD & Planning Hub", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)

# Google Sheets URLs
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"

# --- ΣΥΣΤΗΜΑ ΑΣΦΑΛΕΙΑΣ (MULTI-USER) ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if st.session_state.password_correct:
        return True

    st.title("🔐 Alumil Logistics Secure Login")
    pwd = st.text_input("Εισάγετε τον προσωπικό σας κωδικό", type="password")
    
    if st.button("Είσοδος", use_container_width=True):
        if "passwords" in st.secrets and pwd in st.secrets["passwords"]:
            st.session_state.password_correct = True
            st.session_state.username = st.secrets["passwords"][pwd]
            st.rerun()
        else:
            st.error("❌ Λανθασμένος κωδικός.")
    return False

if not check_password():
    st.stop()

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
    except:
        pass
    return None, 0

def clean_sheet_numeric(series):
    s = series.astype(str).str.replace(' ', '', regex=False)
    def parse_mixed(val):
        if not val or val == 'nan': return 0.0
        try: return float(val.replace(',', '.'))
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
    
    shipments = pd.merge(shipments, coords_db, left_on='City_Clean', right_on='City', how='left')
    unique_plates = shipments[['Truck License Plate', 'Plate_Clean']].drop_duplicates()
    return unique_plates, shipments

# --- UI EXECUTION ---
plates, all_shipments = load_data()

if 'user_plate' not in st.session_state:
    st.title(f"🚛 Alumil Logistics Hub")
    st.write(f"Καλώς ήρθες, **{st.session_state.username}**")
    
    col_a, col_b = st.columns(2)
    with col_a:
        sel = st.selectbox("Επιλογή Οχήματος", plates['Truck License Plate'])
        if st.button("Έναρξη Βάρδιας", type="primary", use_container_width=True):
            st.session_state.user_plate = sel.replace(' ', '').upper()
            st.session_state.display_plate = sel
            st.rerun()
    with col_b:
        st.info("**Δυνατότητες Εφαρμογής:**\n"
                "* Real-road Routing (OSRM)\n"
                "* Electronic POD με Φωτογραφία\n"
                "* Capacity Planning (KG Analysis)\n"
                "* Multi-user Tracking")

else:
    user_data = all_shipments[all_shipments['Plate_Clean'] == st.session_state.user_plate]
    gps = get_geolocation()
    curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps and 'coords' in gps else (41.0, 22.8)
    
    st.sidebar.write(f"👤 Οδηγός: {st.session_state.username}")
    st.sidebar.write(f"🚚 Όχημα: {st.session_state.display_plate}")
    if st.sidebar.button("Logout"):
        st.session_state.user_plate = None
        st.rerun()

    tab1, tab2, tab3, tab4 = st.tabs(["🌎 Χάρτης", "🗺️ Δρομολόγηση", "📦 POD", "📊 Φορτίο"])

    with tab2:
        if st.button("🚀 Βελτιστοποίηση Διαδρομής (Real Roads)", use_container_width=True):
            stops = user_data.drop_duplicates(subset=['City_Clean']).dropna(subset=['Latitude'])
            route_pts = [curr_loc]
            unvisited = stops.copy()
            while not unvisited.empty:
                unvisited['d'] = unvisited.apply(lambda r: geodesic(route_pts[-1], (r['Latitude'], r['Longitude'])).km, axis=1)
                idx = unvisited['d'].idxmin()
                route_pts.append((unvisited.loc[idx, 'Latitude'], unvisited.loc[idx, 'Longitude']))
                unvisited = unvisited.drop(index=idx)
            
            geom, km = get_driving_route_osrm(route_pts)
            if geom:
                m2 = folium.Map(location=curr_loc, zoom_start=7)
                folium.PolyLine(geom, color="blue", weight=5).add_to(m2)
                for i, p in enumerate(route_pts):
                    folium.Marker(p, popup=f"Στάση {i}").add_to(m2)
                st_folium(m2, width="100%", height=500, key="route_map")
                st.success(f"Απόσταση: {km:.1f} km")

    with tab3:
        cust = st.selectbox("Πελάτης", sorted(user_data['Name'].unique()))
        photo = st.camera_input("📸 Λήψη Παραστατικού")
        c1, c2 = st.columns(2)
        if c1.button("▶️ Άφιξη", use_container_width=True):
            st.session_state.start_time = datetime.now()
        if c2.button("⏹️ Ολοκλήρωση & Sync", type="primary", use_container_width=True):
            if st.session_state.start_time:
                dur = (datetime.now() - st.session_state.start_time).total_seconds() / 60
                new_log = pd.DataFrame([{
                    "Timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 
                    "Driver": st.session_state.username,
                    "Plate": st.session_state.display_plate, 
                    "Customer": cust, 
                    "Mins": round(dur, 1),
                    "Photo": "Yes" if photo else "No"
                }])
                conn.update(spreadsheet=LOG_URL, data=pd.concat([conn.read(spreadsheet=LOG_URL, ttl=0), new_log], ignore_index=True))
                st.success("Το POD συγχρονίστηκε!")
                st.session_state.start_time = None

    with tab4:
        tot = user_data['Total KG'].sum()
        st.metric("Συνολικό Βάρος", f"{tot:,.1f} KG")
        st.progress(min(tot/24000, 1.0))
