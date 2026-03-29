import streamlit as st
import pandas as pd
import datetime
import re
from ortools.sat.python import cp_model

st.set_page_config(page_title="ABM Skittles Scheduler", layout="wide")

# --- Constants & Mappings ---
DAY_OFFSETS = {0: 0, 1: 0, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3, 7: 3}
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1x7NdJCc9_Wh_fRkuR_9kQ6bwEsYLqii_zKGLdvf-Dt0/export?format=csv"

class ABMSchedulerEngine:
    def __init__(self, div1_data, div2_data, play_weeks, matches_per_pair, venue_blocks, team_blocks):
        d1 = div1_data.copy()
        d2 = div2_data.copy()
        d1['Division'] = 'Division 1'
        d2['Division'] = 'Division 2'
        self.team_data = pd.concat([d1, d2], ignore_index=True)
        
        self.num_teams = len(self.team_data)
        self.play_weeks = play_weeks 
        self.num_weeks = len(play_weeks)
        self.matches_per_pair = matches_per_pair
        self.venue_blocks = venue_blocks
        self.team_blocks = team_blocks
        self.num_slots = 8 
        self.num_alleys = 2
        
        self.model = cp_model.CpModel()
        self.play = {}
        self.create_variables()

    def create_variables(self):
        for t1 in range(self.num_teams):
            for t2 in range(self.num_teams):
                if t1 != t2 and self.team_data.iloc[t1]['Division'] == self.team_data.iloc[t2]['Division']:
                    for w in range(self.num_weeks):
                        for s in range(self.num_slots):
                            for a in range(self.num_alleys):
                                self.play[(t1, t2, w, s, a)] = self.model.NewBoolVar(
                                    f'match_t{t1}_t{t2}_w{w}_s{s}_a{a}'
                                )

    def add_constraints(self):
        # 1. Total Matches & Exact Home/Away Balance per Pair
        for t1 in range(self.num_teams):
            for t2 in range(t1 + 1, self.num_teams):
                if self.team_data.iloc[t1]['Division'] == self.team_data.iloc[t2]['Division']:
                    matches_t1_home = sum(self.play[(t1, t2, w, s, a)] for w in range(self.num_weeks) for s in range(self.num_slots) for a in range(self.num_alleys))
                    matches_t2_home = sum(self.play[(t2, t1, w, s, a)] for w in range(self.num_weeks) for s in range(self.num_slots) for a in range(self.num_alleys))
                    
                    self.model.Add(matches_t1_home + matches_t2_home == self.matches_per_pair)
                    
                    if self.matches_per_pair % 2 == 0:
                        self.model.Add(matches_t1_home == self.matches_per_pair // 2)
                        self.model.Add(matches_t2_home == self.matches_per_pair // 2)
                    else:
                        self.model.Add(matches_t1_home - matches_t2_home <= 1)
                        self.model.Add(matches_t2_home - matches_t1_home <= 1)
        
        # 2. Match Frequency
        for t in range(self.num_teams):
            for w in range(self.num_weeks):
                weekly_matches = []
                for t2 in range(self.num_teams):
                    if t != t2 and self.team_data.iloc[t]['Division'] == self.team_data.iloc[t2]['Division']:
                        for s in range(self.num_slots):
                            for a in range(self.num_alleys):
                                weekly_matches.append(self.play[(t, t2, w, s, a)])
                                weekly_matches.append(self.play[(t2, t, w, s, a)])
                self.model.Add(sum(weekly_matches) <= 1)

        # 3. Double Booking
        for w in range(self.num_weeks):
            for s in range(self.num_slots):
                for a in range(self.num_alleys):
                    slot_matches = []
                    for t1 in range(self.num_teams):
                        for t2 in range(self.num_teams):
                            if t1 != t2 and (t1, t2, w, s, a) in self.play:
                                slot_matches.append(self.play[(t1, t2, w, s, a)])
                    self.model.Add(sum(slot_matches) <= 1)

        # 4. Global Home/Away & Alley Balancing
        for t in range(self.num_teams):
            home_alley_0 = []
            home_alley_1 = []
            away_alley_0 = []
            away_alley_1 = []
            
            for t2 in range(self.num_teams):
                if t != t2 and self.team_data.iloc[t]['Division'] == self.team_data.iloc[t2]['Division']:
                    for w in range(self.num_weeks):
                        for s in range(self.num_slots):
                            home_alley_0.append(self.play[(t, t2, w, s, 0)])
                            home_alley_1.append(self.play[(t, t2, w, s, 1)])
                            away_alley_0.append(self.play[(t2, t, w, s, 0)])
                            away_alley_1.append(self.play[(t2, t, w, s, 1)])
            
            total_home = sum(home_alley_0) + sum(home_alley_1)
            total_away = sum(away_alley_0) + sum(away_alley_1)
            self.model.Add(total_home - total_away <= 1)
            self.model.Add(total_away - total_home <= 1)
            
            total_alley_0 = sum(home_alley_0) + sum(away_alley_0)
            total_alley_1 = sum(home_alley_1) + sum(away_alley_1)
            self.model.Add(total_alley_0 - total_alley_1 <= 1)
            self.model.Add(total_alley_1 - total_alley_0 <= 1)

            self.model.Add(sum(home_alley_0) - sum(home_alley_1) <= 2)
            self.model.Add(sum(home_alley_1) - sum(home_alley_0) <= 2)
            self.model.Add(sum(away_alley_0) - sum(away_alley_1) <= 2)
            self.model.Add(sum(away_alley_1) - sum(away_alley_0) <= 2)

        # 5. Process Day/Time Preferences
        for t, row in self.team_data.iterrows():
            days_config = {
                'Monday': (0, 1, row['Monday']),
                'Tuesday': (2, 3, row['Tuesday']),
                'Wednesday': (4, 5, row['Wednesday']),
                'Thursday': (6, 7, row['Thursday'])
            }
            for day, (slot_8, slot_9, config) in days_config.items():
                slots_to_block = []
                if config == "Unavailable":
                    slots_to_block.extend([slot_8, slot_9])
                elif config == "8:00 pm only":
                    slots_to_block.append(slot_9)
                elif config == "9:00 pm only":
                    slots_to_block.append(slot_8)
                
                if slots_to_block:
                    for t2 in range(self.num_teams):
                        if t != t2 and (t, t2, 0, 0, 0) in self.play:
                            for w in range(self.num_weeks):
                                for s in slots_to_block:
                                    for a in range(self.num_alleys):
                                        self.model.Add(self.play[(t, t2, w, s, a)] == 0)
                                        self.model.Add(self.play[(t2, t, w, s, a)] == 0)

        # 6. Process Specific Date Blocks
        for w in range(self.num_weeks):
            week_start = self.play_weeks[w]
            for s in range(self.num_slots):
                current_date = week_start + datetime.timedelta(days=DAY_OFFSETS[s])
                
                for block in self.venue_blocks:
                    if block['Date'] == current_date:
                        alleys_to_block = [0, 1] if block['Scope'] == "Whole Club" else ([0] if block['Scope'] == "Alley 1" else [1])
                        for a in alleys_to_block:
                            for t1 in range(self.num_teams):
                                for t2 in range(self.num_teams):
                                    if (t1, t2, w, s, a) in self.play:
                                        self.model.Add(self.play[(t1, t2, w, s, a)] == 0)

                for block in self.team_blocks:
                    if block['Date'] == current_date:
                        target_team = block['Team']
                        for t in range(self.num_teams):
                            if self.team_data.iloc[t]['Team Name'] == target_team:
                                for t2 in range(self.num_teams):
                                    for a in range(self.num_alleys):
                                        if (t, t2, w, s, a) in self.play:
                                            self.model.Add(self.play[(t, t2, w, s, a)] == 0)
                                            self.model.Add(self.play[(t2, t, w, s, a)] == 0)

        # 7. Soft Preferences
        penalties = []
        for t, row in self.team_data.iterrows():
            pref = row['Prefers Time']
            if pref == "8:00 pm":
                for t2 in range(self.num_teams):
                    if t != t2 and (t, t2, 0, 0, 0) in self.play:
                        for w in range(self.num_weeks):
                            for s in [1, 3, 5, 7]: 
                                for a in range(self.num_alleys):
                                    penalties.extend([self.play[(t, t2, w, s, a)], self.play[(t2, t, w, s, a)]])
            elif pref == "9:00 pm":
                for t2 in range(self.num_teams):
                    if t != t2 and (t, t2, 0, 0, 0) in self.play:
                        for w in range(self.num_weeks):
                            for s in [0, 2, 4, 6]: 
                                for a in range(self.num_alleys):
                                    penalties.extend([self.play[(t, t2, w, s, a)], self.play[(t2, t, w, s, a)]])
        
        self.model.Minimize(sum(penalties))

    def solve(self):
        self.add_constraints()
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 180.0 
        status = solver.Solve(self.model)
        
        results = []
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            for w in range(self.num_weeks):
                for s in range(self.num_slots):
                    match_date = self.play_weeks[w] + datetime.timedelta(days=DAY_OFFSETS[s])
                    day_name = match_date.strftime("%a")
                    time_str = "8:00 pm" if s % 2 == 0 else "9:00 pm"
                    
                    for a in range(self.num_alleys):
                        for t1 in range(self.num_teams):
                            for t2 in range(self.num_teams):
                                if (t1, t2, w, s, a) in self.play and solver.Value(self.play[(t1, t2, w, s, a)]) == 1:
                                    results.append({
                                        "SortDate": match_date,
                                        "Date": match_date.strftime("%d %b %Y"),
                                        "Day": day_name,
                                        "Time": time_str,
                                        "Home Team Name": self.team_data.iloc[t1]['Team Name'],
                                        "Away Team Name": self.team_data.iloc[t2]['Team Name'],
                                        "Alley": f"Alley {a + 1}",
                                        "Division": self.team_data.iloc[t1]['Division']
                                    })
            return results
        else:
            return None

def calculate_playing_weeks(start_date, end_date, xmas_start, xmas_end, easter_start, easter_end):
    weeks = []
    current_date = start_date - datetime.timedelta(days=start_date.weekday())
    while current_date <= end_date:
        in_xmas = xmas_start <= current_date <= xmas_end
        in_easter = easter_start <= current_date <= easter_end
        if not in_xmas and not in_easter:
            weeks.append(current_date)
        current_date += datetime.timedelta(days=7)
    return weeks

def create_default_df(prefix, count):
    return pd.DataFrame({
        "Team Name": [f"{prefix} Team {i+1}" for i in range(count)],
        "Monday": ["Any"] * count,
        "Tuesday": ["Any"] * count,
        "Wednesday": ["Any"] * count,
        "Thursday": ["Any"] * count,
        "Prefers Time": ["No Preference"] * count
    })

# --- State Management ---
if 'venue_blocks' not in st.session_state:
    st.session_state.venue_blocks = []
if 'team_blocks' not in st.session_state:
    st.session_state.team_blocks = []
if 'div1_data' not in st.session_state:
    st.session_state.div1_data = create_default_df("D1", 10)
if 'div2_data' not in st.session_state:
    st.session_state.div2_data = create_default_df("D2", 10)

# --- User Interface ---
st.title("ABM Skittles Scheduler")

tab1, tab2, tab3, tab4 = st.tabs(["1. Calendar & Rules", "2. Venue Exceptions", "3. Teams & Preferences", "4. Generate"])

with tab1:
    st.header("Season Parameters")
    col1, col2 = st.columns(2)
    with col1:
        matches_per_pair = st.number_input("Matches per pair (e.g. 2 for Home/Away)", min_value=1, max_value=4, value=2)
        season_start = st.date_input("Season Start (W/C)", datetime.date(2026, 9, 14))
        xmas_start = st.date_input("Xmas Break Start", datetime.date(2026, 12, 21))
        easter_start = st.date_input("Easter Break Start", datetime.date(2027, 3, 22))
    with col2:
        st.write("")
        season_end = st.date_input("Season Target End", datetime.date(2027, 5, 14))
        xmas_end = st.date_input("Xmas Break End", datetime.date(2027, 1, 3))
        easter_end = st.date_input("Easter Break End", datetime.date(2027, 4, 4))
    
    available_weeks = calculate_playing_weeks(season_start, season_end, xmas_start, xmas_end, easter_start, easter_end)
    st.info(f"Playable weeks generated: **{len(available_weeks)}**")

with tab2:
    st.header("Venue & Specific Date Blockers")
    st.write("Add new exceptions via the inputs, or directly edit/delete existing rows in the tables below.")
    col_a, col_b = st.columns(2)
    
    with col_a:
        st.subheader("Add Venue/Alley Block")
        v_date = st.date_input("Date Closed", key="v_date")
        v_scope = st.selectbox("What is closed?", ["Whole Club", "Alley 1", "Alley 2"])
        if st.button("Add Venue Block"):
            st.session_state.venue_blocks.append({"Date": v_date, "Scope": v_scope})
            st.rerun()
            
        if st.session_state.venue_blocks:
            v_df = pd.DataFrame(st.session_state.venue_blocks)
            edited_v_df = st.data_editor(
                v_df, 
                num_rows="dynamic", 
                column_config={"Date": st.column_config.DateColumn("Date")},
                key="v_editor",
                use_container_width=True
            )
            # Safely clean up date formats and save back
            if not edited_v_df.empty:
                edited_v_df['Date'] = pd.to_datetime(edited_v_df['Date']).dt.date
            st.session_state.venue_blocks = edited_v_df.to_dict('records')
            
            if st.button("Clear All Venue Blocks"):
                st.session_state.venue_blocks = []
                st.rerun()

    with col_b:
        st.subheader("Add Specific Team Block")
        t_date = st.date_input("Date Unavailable", key="t_date")
        t_team = st.text_input("Exact Team Name")
        if st.button("Add Team Block"):
            if t_team:
                st.session_state.team_blocks.append({"Date": t_date, "Team": t_team})
                st.rerun()
                
        if st.session_state.team_blocks:
            t_df = pd.DataFrame(st.session_state.team_blocks)
            edited_t_df = st.data_editor(
                t_df, 
                num_rows="dynamic", 
                column_config={"Date": st.column_config.DateColumn("Date")},
                key="t_editor",
                use_container_width=True
            )
            if not edited_t_df.empty:
                edited_t_df['Date'] = pd.to_datetime(edited_t_df['Date']).dt.date
            st.session_state.team_blocks = edited_t_df.to_dict('records')
            
            if st.button("Clear All Team Blocks"):
                st.session_state.team_blocks = []
                st.rerun()

with tab3:
    st.header("Division Setups & Import")
    
    if st.button("🔄 Sync with Google Sheets Form", type="primary"):
        try:
            df_import = pd.read_csv(SHEET_CSV_URL)
            
            def extract_division(df, div_name):
                div_df = df[df['Division'] == div_name].copy()
                if div_df.empty:
                    return pd.DataFrame()
                res = pd.DataFrame()
                res['Team Name'] = div_df['Team Name']
                res['Monday'] = div_df['Monday']
                res['Tuesday'] = div_df['Tuesday']
                res['Wednesday'] = div_df['Wednesday']
                res['Thursday'] = div_df['Thursday']
                res['Prefers Time'] = div_df['Prefers Time']
                return res.reset_index(drop=True)

            st.session_state.div1_data = extract_division(df_import, 'Division 1')
            st.session_state.div2_data = extract_division(df_import, 'Division 2')
            
            parsed_blocks = []
            for _, row in df_import.iterrows():
                dates_str = row.get('Specific Unavailable Dates', '')
                t_name = row['Team Name']
                
                if pd.notna(dates_str) and str(dates_str).strip():
                    raw_dates = re.split(r'[,\n]+', str(dates_str))
                    for rd in raw_dates:
                        rd = rd.strip()
                        if not rd: continue
                        
                        try:
                            p_date = datetime.datetime.strptime(rd, "%d/%m/%y").date()
                            parsed_blocks.append({"Date": p_date, "Team": t_name})
                        except ValueError:
                            try:
                                p_date = datetime.datetime.strptime(rd, "%d/%m/%Y").date()
                                parsed_blocks.append({"Date": p_date, "Team": t_name})
                            except ValueError:
                                st.warning(f"Could not automatically read date '{rd}' for team '{t_name}'. Please add it manually in Tab 2.")
            
            st.session_state.team_blocks.extend(parsed_blocks)
            st.success("Data successfully synced! Captains' preferences and specific dates have been loaded.")
            
        except Exception as e:
            st.error(f"Failed to fetch data. Ensure the Google Sheet is shared publicly. Error: {e}")

    st.write("Review or manually edit the team details below.")
    day_options = ["Any", "8:00 pm only", "9:00 pm only", "Unavailable"]
    col_config = {
        "Monday": st.column_config.SelectboxColumn("Monday", options=day_options),
        "Tuesday": st.column_config.SelectboxColumn("Tuesday", options=day_options),
        "Wednesday": st.column_config.SelectboxColumn("Wednesday", options=day_options),
        "Thursday": st.column_config.SelectboxColumn("Thursday", options=day_options),
        "Prefers Time": st.column_config.SelectboxColumn("Prefers Time", options=["No Preference", "8:00 pm", "9:00 pm"])
    }

    st.subheader("Division 1")
    div1_edited = st.data_editor(st.session_state.div1_data, column_config=col_config, num_rows="dynamic", key="div1_ui")
    
    st.subheader("Division 2")
    div2_edited = st.data_editor(st.session_state.div2_data, column_config=col_config, num_rows="dynamic", key="div2_ui")

with tab4:
    st.header("Generate Schedule")
    
    if st.button("Run Optimisation Engine", type="primary"):
        with st.spinner("Calculating... Note: Equalising home/away across alleys makes this significantly harder for the engine. It may take up to 3 minutes."):
            scheduler = ABMSchedulerEngine(
                div1_edited, div2_edited, available_weeks, 
                matches_per_pair=matches_per_pair,
                venue_blocks=st.session_state.venue_blocks,
                team_blocks=st.session_state.team_blocks
            )
            schedule_data = scheduler.solve()
            
            if schedule_data:
                st.success("Success! Here is the finalised schedule.")
                df = pd.DataFrame(schedule_data)
                
                df = df.sort_values(by=["SortDate", "Time", "Alley"])
                df = df[["Date", "Day", "Time", "Home Team Name", "Away Team Name", "Alley", "Division"]]
                
                st.dataframe(df, use_container_width=True)
                
                csv = df.to_csv(index=False).encode('utf-8')
                
                st.download_button(
                    label="Download Schedule as CSV",
                    data=csv,
                    file_name="abm_skittles_schedule.csv",
                    mime="text/csv"
                )
            else:
                st.error("The engine couldn't find a solution. There are too many restrictions locking the math up. Try relaxing some team availability.")
