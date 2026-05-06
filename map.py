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
st.set_page_config(page_title="Alumil Logistics Hub v20", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)
GR_TIME = timezone(timedelta(hours=3))

# URLs
SHIPMENTS_URL = "https://docs.google.com/spreadsheets/d/1ZIZgYar_VcrhqzpdWRTKwmF2WmumU240DUD3zSsU8xc/edit"
COORDS_URL = "https://docs.google.com/spreadsheets/d/1u1HKa5P97ywlMZM0tCyPgRGmMf0fgVnQZU_rpVnhRZU/edit"
LOG_URL = "https://docs.google.com/spreadsheets/d/1NSB1XvK8PX0DOAK5OgjDGQxvHpdL1jVSR_nzovJfjuM/edit"

# --- SESSION STATE INIT ---
if "password_correct" not in st.session_state: st.session_state.password_correct = False
if "user_plate" not in st.session_state: st.session_state.user_plate = None
if "username" not in st.session_state: st.session_state.username = None
if "route_data" not in st.session_state: st.session_state.route_data = []
if "route_geom" not in st.session_state: st.session_state.route_geom = None
if "start_time" not in st.session_state: st.session_state.start_time = None
if "draft_sequence" not in st.session_state: st.session_state.draft_sequence = None

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

def clean_val(v):
    # System logic: Μετατρέπει 1.500,50 σε 1500.50 για να μπορεί να κάνει πράξεις
    try: return float(str(v).replace('.', '').replace(',', '.'))
    except: return 0.0

def gr_num(val, decimals=1):
    # UI logic: Εμφανίζει το νούμερο με τελεία στις χιλιάδες και κόμμα στα δεκαδικά
    s = f"{val:,.{decimals}f}"
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')

@st.cache_data(ttl=300)
def load_full_data():
    ship = conn.read(spreadsheet=SHIPMENTS_URL, ttl=300)
    ship['Plate_Clean'] = ship['Truck License Plate'].astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    ship['City_Clean'] = ship['City'].astype(str).str.strip().str.upper()
    for c in ['Total KG', 'Unpainted', 'White', 'Colored', 'Accessories']:
        if c in ship.columns: ship[c] = ship[c].apply(clean_val)
    
    coords = conn.read(spreadsheet=COORDS_URL, ttl=300)
    coords['City_Match'] = coords['City'].astype(str).str.strip().str.upper()
    df = pd.merge(ship, coords.drop_duplicates('City_Match'), left_on='City_Clean', right_on='City_Match', how='left')
    
    counts = df.groupby('Plate_Clean')['Name'].nunique().reset_index(name='Dests')
    unique_plates = df[['Truck License Plate', 'Plate_Clean']].drop_duplicates()
    fleet_summary = pd.merge(unique_plates, counts, on='Plate_Clean', how='left').fillna(0)
    fleet_summary['Label'] = fleet_summary.apply(lambda r: f"{r['Truck License Plate']} ({int(r['Dests'])} Στάσεις)", axis=1)
    return fleet_summary.sort_values('Dests', ascending=False), df

fleet_info, all_data = load_full_data()

# --- SIDEBAR ---
st.sidebar.title("Alumil Hub")
st.sidebar.write(f"👤 **{st.session_state.username}**")
app_mode = st.sidebar.radio("Μενού", ["🚛 Driver Terminal", "📊 Admin Dashboard"])

if st.sidebar.button("Logout"):
    st.session_state.password_correct = False
    st.session_state.user_plate = None
    st.session_state.draft_sequence = None
    st.session_state.route_data = []
    st.session_state.route_geom = None
    st.session_state.start_time = None
    st.rerun()

# --- 1. DRIVER TERMINAL ---
if app_mode == "🚛 Driver Terminal":
    if st.session_state.user_plate is None:
        st.title("Επιλογή Οχήματος")
        sel = st.selectbox("Φορτηγό", fleet_info['Label'])
        if st.button("Έναρξη", type="primary", use_container_width=True):
            row = fleet_info[fleet_info['Label'] == sel].iloc[0]
            st.session_state.user_plate = row['Plate_Clean']
            st.session_state.display_plate = row['Truck License Plate']
            # Πλήρης καθαρισμός μνήμης για το νέο όχημα
            st.session_state.draft_sequence = None
            st.session_state.route_data = []
            st.session_state.route_geom = None
            st.session_state.start_time = None
            st.rerun()
    else:
        st.subheader(f"🚚 {st.session_state.display_plate}")
        user_data = all_data[all_data['Plate_Clean'] == st.session_state.user_plate]
        gps = get_geolocation()
        curr_loc = (gps['coords']['latitude'], gps['coords']['longitude']) if gps and 'coords' in gps else (41.0, 22.8)

        tab1, tab2, tab3, tab4, tab5 = st.tabs(["🌎 Γενικός Χάρτης", "🛣️ Δρομολόγηση", "📦 POD Protocol", "📊 Analytics", "📩 Ειδοποίηση"])

        with tab1:
            st.write("Όλα τα σημεία εκφόρτωσης (Αταξινόμητα):")
            m1 = folium.Map(location=curr_loc, zoom_start=7)
            folium.Marker(curr_loc, popup="Η θέση μου", tooltip="Η θέση μου", icon=folium.Icon(color='green', icon='truck', prefix='fa')).add_to(m1)
            for _, r in user_data.drop_duplicates(subset=['Name']).iterrows():
                if pd.notna(r['Latitude']):
                    folium.Marker([r['Latitude'], r['Longitude']], popup=f"{r['Name']}", tooltip=f"{r['Name']} ({r['City_x']})").add_to(m1)
            st_folium(m1, width="100%", height=500, key="all_points_map")

        with tab2:
            st.subheader("Σχεδιασμός & Βελτιστοποίηση Διαδρομής")
            
            if st.button("1. Αυτόματη Πρόταση Σειράς (OSRM)", use_container_width=True):
                stops = user_data.drop_duplicates('Name').dropna(subset=['Latitude'])
                pts, seq_list = [curr_loc], []
                unvisited = stops.copy()
                
                while not unvisited.empty:
                    unvisited['d'] = unvisited.apply(lambda r: geodesic(pts[-1], (r['Latitude'], r['Longitude'])).km, axis=1)
                    idx = unvisited['d'].idxmin()
                    row = unvisited.loc[idx]
                    pts.append((row['Latitude'], row['Longitude']))
                    un_time = (row['Total KG'] / 1000) * 10
                    seq_list.append({'name': row['Name'], 'city': row['City_x'], 'kg': row['Total KG'], 'unload': un_time, 'coords': (row['Latitude'], row['Longitude'])})
                    unvisited = unvisited.drop(index=idx)
                
                # Δημιουργία Draft Sequence για Ad-Hoc Edit
                draft_df = pd.DataFrame([{'Name': s['name'], 'City': s['city'], 'KG': s['kg'], 'Latitude': s['coords'][0], 'Longitude': s['coords'][1]} for s in seq_list])
                draft_df.insert(0, 'Σειρά', range(1, len(draft_df) + 1))
                st.session_state.draft_sequence = draft_df

                # Υπολογισμός χαρτών & χρόνων για την αυτόματη πρόταση
                for i in range(len(seq_list)):
                    _, _, d_min = get_osrm_data([pts[i], pts[i+1]])
                    seq_list[i]['drive_to'] = d_min
                st.session_state.route_data = seq_list
                geom, _, _ = get_osrm_data(pts)
                st.session_state.route_geom = geom
                st.rerun()

            if st.session_state.draft_sequence is not None:
                st.info("💡 **Ad-Hoc Rerouting:** Κάντε διπλό κλικ στη στήλη 'Σειρά' για να αλλάξετε τη σειρά εκφόρτωσης, αν θέλετε.")
                edited_seq = st.data_editor(st.session_state.draft_sequence, hide_index=True, use_container_width=True, disabled=['Name', 'City', 'KG', 'Latitude', 'Longitude'])
                
                if st.button("2. Εφαρμογή Νέας Σειράς & Ενημέρωση Χάρτη", type="primary", use_container_width=True):
                    edited_seq = edited_seq.sort_values(by='Σειρά')
                    pts, final_seq = [curr_loc], []
                    for _, row in edited_seq.iterrows():
                        pts.append((row['Latitude'], row['Longitude']))
                        un_time = (row['KG'] / 1000) * 10
                        final_seq.append({'name': row['Name'], 'city': row['City'], 'kg': row['KG'], 'unload': un_time, 'coords': (row['Latitude'], row['Longitude'])})
                    
                    for i in range(len(final_seq)):
                        _, _, d_min = get_osrm_data([pts[i], pts[i+1]])
                        final_seq[i]['drive_to'] = d_min
                    
                    st.session_state.route_data = final_seq
                    geom, _, _ = get_osrm_data(pts)
                    st.session_state.route_geom = geom
                    st.rerun()

            if st.session_state.route_data:
                st.divider()
                st.write("**Τελικό Δρομολόγιο & Εκτιμώμενοι Χρόνοι:**")
                for i, s in enumerate(st.session_state.route_data):
                    st.write(f"**{i+1}. {s['name']}** ({s['city']}): 🚛 ~{int(s['drive_to'])}' | 🏗️ ~{int(s['unload'])}' (Φορτίο: {gr_num(s['kg'], 0)} KG)")
                
                m2 = folium.Map(location=curr_loc, zoom_start=7)
                
                # Αφετηρία
                folium.Marker(curr_loc, popup="Αφετηρία", tooltip="Αφετηρία", icon=folium.Icon(color='green', icon='play')).add_to(m2)

                if st.session_state.route_geom:
                    folium.PolyLine([[l, lon] for lon, l in st.session_state.route_geom], color="#007bff", weight=5, opacity=0.8).add_to(m2)
                    
                    for i, s in enumerate(st.session_state.route_data):
                        seq_num = i + 1
                        # Custom Pin με Αριθμό και Χρώμα
                        pin_html = f'''
                            <div style="background-color:#E3000F; color:white; border-radius:50%; width:28px; height:28px; 
                                        display:flex; justify-content:center; align-items:center; font-weight:bold; 
                                        border:2px solid white; box-shadow: 0px 2px 4px rgba(0,0,0,0.4); font-size:13px;">
                                {seq_num}
                            </div>
                        '''
                        folium.Marker(
                            [s['coords'][0], s['coords'][1]],
                            popup=f"Στάση {seq_num}: {s['name']}",
                            tooltip=f"{seq_num}. {s['name']}",
                            icon=folium.DivIcon(html=pin_html, icon_size=(28, 28), icon_anchor=(14, 14))
                        ).add_to(m2)
                
                # Το hash key εγγυάται ότι αν αλλάξεις σειρά, ο παλιός χάρτης διαγράφεται εντελώς και σχεδιάζεται από την αρχή
                map_key = f"routing_map_{hash(str(st.session_state.route_data))}"
                st_folium(m2, width="100%", height=450, key=map_key)

        with tab3:
            st.subheader("POD Protocol")
            custList = [s['name'] for s in st.session_state.route_data] if st.session_state.route_data else sorted(user_data['Name'].unique())
            active_cust = st.selectbox("Πελάτης", custList)
            cust_rows = user_data[user_data['Name'] == active_cust]
            use_cam = st.checkbox("Ενεργοποίηση Κάμερας")
            photo = st.camera_input("📸 Φωτογραφία") if use_cam else None
            
            c1, c2 = st.columns(2)
            if c1.button("▶️ Άφιξη", use_container_width=True):
                st.session_state.start_time = datetime.now(GR_TIME)
            if c2.button("⏹️ Sync POD", type="primary", use_container_width=True):
                if st.session_state.start_time:
                    dur = (datetime.now(GR_TIME) - st.session_state.start_time).total_seconds() / 60
                    p_kg = cust_rows[['Unpainted', 'White', 'Colored']].sum().sum()
                    a_kg = cust_rows['Accessories'].sum()
                    new_log = pd.DataFrame([{
                        "Timestamp": datetime.now(GR_TIME).strftime('%Y-%m-%d %H:%M:%S'), 
                        "Driver": st.session_state.username, "Plate": st.session_state.display_plate, 
                        "Customer": active_cust, "Profiles_KG": p_kg, "Accessories_KG": a_kg,
                        "Unload_Mins": round(dur, 1), "Photo": "Yes" if photo else "No"
                    }])
                    conn.update(spreadsheet=LOG_URL, data=pd.concat([conn.read(spreadsheet=LOG_URL, ttl=0), new_log], ignore_index=True))
                    st.success("Συγχρονίστηκε!")
                    st.session_state.start_time = None
                else: st.error("Πατήστε 'Άφιξη'!")

        with tab4:
            tot = user_data['Total KG'].sum()
            prof = user_data[['Unpainted', 'White', 'Colored']].sum().sum()
            acc = user_data['Accessories'].sum()
            c1, c2, c3 = st.columns(3)
            c1.metric("Σύνολο", f"{gr_num(tot, 1)} KG")
            c2.metric("Προφίλ", f"{gr_num(prof, 1)} KG")
            c3.metric("Εξαρτήματα", f"{gr_num(acc, 1)} KG")
            st.progress(min(tot/24000, 1.0))
            st.caption(f"Load Factor: {gr_num((tot/24000)*100, 1)}%")

        with tab5:
            st.subheader("📩 Ειδοποίηση Επόμενου")
            if st.session_state.route_data:
                names = [s['name'] for s in st.session_state.route_data]
                if active_cust in names:
                    idx = names.index(active_cust)
                    if idx < len(st.session_state.route_data) - 1:
                        nxt = st.session_state.route_data[idx+1]
                        total_wait = int(next(s['unload'] for s in st.session_state.route_data if s['name'] == active_cust) + nxt['drive_to'])
                        body = f"Alumil Logistics: Εκφόρτωση σε εξέλιξη. Εκτιμώμενη άφιξη σε εσάς σε περίπου {total_wait} λεπτά."
                        st.info(body)
                        link = f"mailto:?subject=Alumil Delivery&body={urllib.parse.quote(body)}"
                        st.markdown(f'<a href="{link}" target="_blank" style="padding:15px; background-color:#007bff; color:white; border-radius:8px; text-decoration:none;">📧 Mail to Next Customer</a>', unsafe_allow_html=True)

# --- 2. ADMIN DASHBOARD ---
elif app_mode == "📊 Admin Dashboard":
    st.title("Admin Control Panel")
    logs = conn.read(spreadsheet=LOG_URL, ttl=0)
    st.dataframe(logs.tail(20), use_container_width=True)
