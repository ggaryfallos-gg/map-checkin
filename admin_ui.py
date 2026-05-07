import streamlit as st
import pandas as pd
import time
from datetime import datetime
from utils import gr_num, geocode_address,get_supplier_pickups

def render_admin_dashboard(all_data, conn, LOG_URL):
    st.title("Admin Logistics Control Tower")
    admin_tab1, admin_tab2, admin_tab3 = st.tabs(["📈 Analytics & Planning", "📦 Διαχείριση Παραλαβών", "📡 GPS Logs"])
    
    # --- TAB 1: ANALYTICS & PLANNING ---
    with admin_tab1:
        st.header("Capacity Planning & Distribution")
        
        if not all_data.empty:
            # FIX ΓΙΑ ΤΟ TYPEERROR: Καθαρίζουμε τα NaN πριν το sorted()
            raw_plates = all_data['Truck License Plate'].dropna().unique().tolist()
            all_plates = sorted([str(p) for p in raw_plates]) # Διασφάλιση ότι όλα είναι strings
            
            # Φίλτρα Planning
            col_f1, col_f2 = st.columns([2, 1])
            selected_trucks = col_f1.multiselect(
                "🚚 Επιλογή Στόλου για Ανάλυση", 
                all_plates, 
                default=all_plates[:min(5, len(all_plates))]
            )
            
            # Φιλτράρισμα δεδομένων
            planning_df = all_data[all_data['Truck License Plate'].isin(selected_trucks)].copy()
            
            if not planning_df.empty:
                # 1. Δημιουργία Pivot: Rows=Trucks, Columns=Dates, Values=Total KG
                pivot_planning = planning_df.pivot_table(
                    index='Truck License Plate', 
                    columns='Loading_Date', 
                    values='Total KG', 
                    aggfunc='sum'
                ).fillna(0)
               
                
                # Εμφάνιση Heatmap Table
                if not planning_df.empty:
                    # 1. Δημιουργία Pivot με καθαρούς αριθμούς (float/int)
                    pivot_planning = planning_df.pivot_table(
                        index='Truck License Plate', 
                        columns='Loading_Date', 
                        values='Total KG', 
                        aggfunc='sum'
                    ).fillna(0)
        
                    st.subheader("📅 Χρονοδιάγραμμα Φόρτωσης (Heatmap)")
                    st.write("Οπτικοποίηση βάρους ανά ημέρα. Το σκούρο χρώμα υποδεικνύει πλήρες φορτίο.")
                    
                    # 2. Υπολογισμός δυναμικού ορίου για τα χρώματα
                    # Αν όλες οι τιμές είναι μικρές, το 'high' θα προσαρμοστεί για να βλέπεις χρώματα
                    max_val = pivot_planning.max().max()
                    if max_val < 1: max_val = 24000 # Safety check
        
                    # 3. Εφαρμογή Heatmap Styling
                    styled_df = pivot_planning.style.background_gradient(
                        cmap='YlOrRd',    # Κίτρινο -> Πορτοκαλί -> Κόκκινο
                        axis=None,        # Σύγκριση σε όλο τον πίνακα
                        low=0, 
                        high=max_val      # Το μέγιστο βάρος θα είναι το πιο σκούρο κόκκινο
                    ).format("{:,.0f} kg") # Προσθήκη μονάδας μέτρησης μόνο στην απεικόνιση
        
                    # 4. Εμφάνιση
                    st.dataframe(styled_df, use_container_width=True)

                st.divider()
                
                # 2. Γεωγραφική Κατανομή & Metrics
                col_g1, col_g2 = st.columns([2, 1])
                with col_g1:
                    st.subheader("🏙️ Προορισμοί ανά Πόλη")
                    city_counts = planning_df.groupby('City_Clean')['Name'].nunique().sort_values(ascending=False).reset_index()
                    city_counts.columns = ['Πόλη', 'Πλήθος Πελατών']
                    st.bar_chart(city_counts, x='Πόλη', y='Πλήθος Πελατών', color="#007bff")

                with col_g2:
                    st.subheader("📊 Key Metrics")
                    total_kg_view = planning_df['Total KG'].sum()
                    # Υπολογισμός μέσης φόρτωσης ανά δρομολόγιο
                    route_sums = planning_df.groupby(['Truck License Plate', 'Loading_Date'])['Total KG'].sum()
                    avg_load = route_sums.mean() if not route_sums.empty else 0
                    
                    st.metric("Συνολικό Βάρος", f"{gr_num(total_kg_view, 0)} kg")
                    st.metric("Μέση Φόρτωση", f"{gr_num(avg_load, 0)} kg")
                    
                    load_factor = min(avg_load / 24000, 1.0) if avg_load > 0 else 0
                    st.write(f"Efficiency: **{gr_num(load_factor*100, 1)}%**")
                    st.progress(load_factor)
            else:
                st.warning("Παρακαλώ επιλέξτε τουλάχιστον ένα φορτηγό.")
        else:
            st.info("Αναμονή για δεδομένα από το SHIPMENTS_URL...")

    # --- TAB 2: ΔΙΑΧΕΙΡΙΣΗ ΠΑΡΑΛΑΒΩΝ ---
    with admin_tab2:
        st.header("Διαχείριση Παραλαβών Προμηθευτών")
        # 1. Φόρμα Νέας Παραλαβής
        with st.expander("➕ Καταχώρηση Νέας Παραλαβής", expanded=False):
            with st.form("new_pickup_admin", clear_on_submit=True):
                c1, c2 = st.columns(2)
                s_name = c1.text_input("Όνομα Προμηθευτή")
                s_addr = c2.text_input("Διεύθυνση")
                
                c3, c4 = st.columns(2)
                s_area = c3.selectbox("Περιοχή", ["Σίνδος", "Καλοχώρι", "Οινόφυτα", "Ασπρόπυργος", "Θεσσαλονίκη", "Αθήνα"])
                p_date = c4.date_input("Ημερομηνία", value=datetime.now())
                
                if st.form_submit_button("Οριστική Υποβολή"):
                    if s_name and s_addr:
                        lat, lon = geocode_address(s_addr, "")
                        new_entry = pd.DataFrame([{
                            "ID": int(time.time()), "Date": p_date.strftime("%d/%m/%Y"),
                            "Supplier_Name": s_name, "Address": s_addr, "Area": s_area,
                            "Status": "Pending", "Assigned_Plate": "", "Lat": lat or 0.0, "Lon": lon or 0.0
                        }])
                        conn.update(spreadsheet=LOG_URL, worksheet="Supplier_Pickups", 
                                    data=updated_data.fillna(""))
                        st.success(f"Καταχωρήθηκε: {s_name}")
                        time.sleep(0.5); st.rerun()

        st.divider()

        # 2. Manager/Editor Παραλαβών
        pickups_df = get_supplier_pickups(conn, LOG_URL)
        if not pickups_df.empty:
            raw_plates = all_data['Truck License Plate'].dropna().unique().tolist()
            available_plates = sorted([str(p) for p in raw_plates])
            
            edited_pickups = st.data_editor(
                pickups_df,
                column_config={
                    "Status": st.column_config.SelectboxColumn("Κατάσταση", options=["Pending", "Assigned", "Collected"], required=True),
                    "Assigned_Plate": st.column_config.SelectboxColumn("Ανάθεση σε Πινακίδα", options=available_plates),
                    "ID": None, "Lat": None, "Lon": None, "Address": None, "Area": None
                },
                hide_index=True, use_container_width=True, key="admin_pickup_editor"
            )
            
            if st.button("💾 Αποθήκευση Αναθέσεων", type="primary"):
                conn.update(spreadsheet=LOG_URL, worksheet="Supplier_Pickups", data=edited_pickups.fillna(""))
                st.success("Οι αναθέσεις ενημερώθηκαν!")
                time.sleep(0.5); st.rerun()
        else:
            st.info("Δεν υπάρχουν εκκρεμείς παραλαβές.")
                    
    # --- TAB 3: GPS LOGS ---
    with admin_tab3:
        st.header("Live Activity Logs")
        try:
            logs_view = conn.read(spreadsheet=LOG_URL, worksheet="Transit_Log", ttl=20)
            st.dataframe(logs_view.tail(30), use_container_width=True)
        except:
            st.warning("Το Transit_Log δεν είναι διαθέσιμο.")
