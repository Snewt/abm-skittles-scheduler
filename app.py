import streamlit as st
import pandas as pd
import datetime
import re
from ortools.sat.python import cp_model

st.set_page_config(page_title="ABM Skittles Scheduler", layout="wide")

# --- Security ---
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["app_password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.title("🔒 ABM Skittles Scheduler")
        st.text_input("Please enter the admin password:", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.title("🔒 ABM Skittles Scheduler")
        st.text_input("Please enter the admin password:", type="password", on_change=password_entered, key="password")
        st.error("Incorrect password.")
        return False
    else:
        return True

if check_password():
    # --- Constants & Mappings ---
    DAY_OFFSETS = {0: 0, 1: 0, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3, 7: 3}

    # --- Helper Functions for Clash Detection ---
    def get_available_slots(row):
        slots = set([0,1,2,3,4,5,6,7])
        days_config = {
            'Monday': (0, 1, row.get('Monday', 'Any')),
            'Tuesday': (2, 3, row.get('Tuesday', 'Any')),
            'Wednesday': (4, 5, row.get('Wednesday', 'Any')),
            'Thursday': (6, 7, row.get('Thursday', 'Any'))
        }
        for day, (slot_8, slot_9, config) in days_config.items():
            if config == "Unavailable":
                slots.discard(slot_8)
                slots.discard(slot_9)
            elif config == "8:00 pm only":
                slots.discard(slot_9)
            elif config == "9:00 pm only":
                slots.discard(slot_8)
        return slots

    def find_impossible_matchups(team_data, match_exceptions, num_divisions):
        clashes = []
        def has_valid_exception(t1_name, t2_name):
            for exc in match_exceptions:
                if (exc['Team 1'] == t1_name and exc['Team 2'] == t2_name) or \
                   (exc['Team 1'] == t2_name and exc['Team 2'] == t1_name):
                    return True
            return False

        divs_to_check = ['Division 1', 'Division 2'] if num_divisions == 2 else ['Division 1']
        
        for div in divs_to_check:
            div_teams = team_data[team_data['Division'] == div].reset_index(drop=True)
            for i in range(len(div_teams)):
                for j in range(i+1, len(div_teams)):
                    team1 = div_teams.iloc[i]
                    team2 = div_teams.iloc[j]
                    
                    t1_avail = get_available_slots(team1)
                    t2_avail = get_available_slots(team2)
                    
                    if not t1_avail.intersection(t2_avail):
                        if not has_valid_exception(team1['Team Name'], team2['Team Name']):
                            clashes.append((team1['Team Name'], team2['Team Name']))
        return clashes

    class ABMSchedulerEngine:
        def __init__(self, div1_data, div2_data, play_weeks, matches_per_pair, venue_blocks, team_blocks, match_exceptions, num_divisions, num_alleys):
            d1 = div1_data[div1_data['Playing?'] == True].copy()
            d1['Division'] = 'Division 1'
            
            if num_divisions == 2:
                d2 = div2_data[div2_data['Playing?'] == True].copy()
                d2['Division'] = 'Division 2'
                self.team_data = pd.concat([d1, d2], ignore_index=True)
            else:
                self.team_data = d1.reset_index(drop=True)
            
            self.num_teams = len(self.team_data)
            self.play_weeks = play_weeks 
            self.num_weeks = len(play_weeks)
            self.matches_per_pair = matches_per_pair
            self.venue_blocks = venue_blocks
            self.team_blocks = team_blocks
            self.match_exceptions = match_exceptions
            self.num_slots = 8 
            self.num_alleys = num_alleys
            
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

        def get_overridden_slots(self, t1_name, t2_name):
            allowed = []
            for exc in self.match_exceptions:
                if (exc['Team 1'] == t1_name and exc['Team 2'] == t2_name) or \
                   (exc['Team 1'] == t2_name and exc['Team 2'] == t1_name):
                    day = exc['Override Day']
                    time = exc['Override Time']
                    
                    day_slots = {'Monday': [0,1], 'Tuesday': [2,3], 'Wednesday': [4,5], 'Thursday': [6,7]}
                    if day in day_slots:
                        s8, s9 = day_slots[day]
                        if time == "Both":
                            allowed.extend([s8, s9])
                        elif time == "8:00 pm":
                            allowed.append(s8)
                        elif time == "9:00 pm":
                            allowed.append(s9)
            return allowed

        def add_constraints(self):
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

            for w in range(self.num_weeks):
                for s in range(self.num_slots):
                    for a in range(self.num_alleys):
                        slot_matches = []
                        for t1 in range(self.num_teams):
                            for t2 in range(self.num_teams):
                                if t1 != t2 and (t1, t2, w, s, a) in self.play:
                                    slot_matches.append(self.play[(t1, t2, w, s, a)])
                        self.model.Add(sum(slot_matches) <= 1)

            for t in range(self.num_teams):
                home_alley_0 = []
                home_alley_1 = []
                away_alley_0 = []
                away_alley_1 = []
                
                for t2 in range(self.num_teams):
                    if t != t2 and self.team_data.iloc[t]['Division'] == self.team_data.iloc[t2]['Division']:
                        for w in range(self.num_weeks):
                            for s in range(self.num_slots):
                                if self.num_alleys > 0:
                                    home_alley_0.append(self.play[(t, t2, w, s, 0)])
                                    away_alley_0.append(self.play[(t2, t, w, s, 0)])
                                if self.num_alleys == 2:
                                    home_alley_1.append(self.play[(t, t2, w, s, 1)])
                                    away_alley_1.append(self.play[(t2, t, w, s, 1)])
                
                total_home = sum(home_alley_0) + sum(home_alley_1)
                total_away = sum(away_alley_0) + sum(away_alley_1)
                self.model.Add(total_home - total_away <= 1)
                self.model.Add(total_away - total_home <= 1)
                
                if self.num_alleys == 2:
                    total_alley_0 = sum(home_alley_0) + sum(away_alley_0)
                    total_alley_1 = sum(home_alley_1) + sum(away_alley_1)
                    self.model.Add(total_alley_0 - total_alley_1 <= 1)
                    self.model.Add(total_alley_1 - total_alley_0 <= 1)

                    self.model.Add(sum(home_alley_0) - sum(home_alley_1) <= 2)
                    self.model.Add(sum(home_alley_1) - sum(home_alley_0) <= 2)
                    self.model.Add(sum(away_alley_0) - sum(away_alley_1) <= 2)
                    self.model.Add(sum(away_alley_1) - sum(away_alley_0) <= 2)

            for t in range(self.num_teams):
                t_name = self.team_data.iloc[t]['Team Name']
                row = self.team_data.iloc[t]
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
                                t2_name = self.team_data.iloc[t2]['Team Name']
                                overridden = self.get_overridden_slots(t_name, t2_name)
                                
                                for w in range(self.num_weeks):
                                    for s in slots_to_block:
                                        if s not in overridden:
                                            for a in range(self.num_alleys):
                                                self.model.Add(self.play[(t, t2, w, s, a)] == 0)
                                                self.model.Add(self.play[(t2, t, w, s, a)] == 0)

            for w in range(self.num_weeks):
                week_start = self.play_weeks[w]
                for s in range(self.num_slots):
                    current_date = week_start + datetime.timedelta(days=DAY_OFFSETS[s])
                    
                    for block in self.venue_blocks:
                        if block['Date'] == current_date:
                            alleys_to_block = [0, 1] if block['Scope'] == "Whole Club" else ([0] if block['Scope'] == "Alley 1" else [1])
                            for a in alleys_to_block:
                                if a < self.num_alleys:
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

            penalties = []
            
            # --- Anti-Clumping (Days) ---
            team_day_vars = {}
            for t in range(self.num_teams):
                for w in range(self.num_weeks):
                    for d in range(4):
                        matches_this_day = []
                        for s in [d*2, d*2+1]: 
                            for t2 in range(self.num_teams):
                                if t != t2 and (t, t2, w, s, 0) in self.play:
                                    for a in range(self.num_alleys):
                                        matches_this_day.append(self.play[(t, t2, w, s, a)])
                                        matches_this_day.append(self.play[(t2, t, w, s, a)])
                        team_day_vars[(t, w, d)] = sum(matches_this_day)

            for t in range(self.num_teams):
                for w in range(self.num_weeks - 2):
                    for d in range(4):
                        window_sum = team_day_vars[(t, w, d)] + team_day_vars[(t, w+1, d)] + team_day_vars[(t, w+2, d)]
                        penalty_var = self.model.NewIntVar(0, 3, f'pen_clump_t{t}_w{w}_d{d}')
                        self.model.Add(penalty_var >= window_sum - 1)
                        penalties.extend([penalty_var, penalty_var])

            # --- Time Parity (8pm vs 9pm) ---
            for t, row in self.team_data.iterrows():
                pref = row['Prefers Time']
                
                matches_8pm = []
                matches_9pm = []
                for t2 in range(self.num_teams):
                    if t != t2 and self.team_data.iloc[t]['Division'] == self.team_data.iloc[t2]['Division']:
                        for w in range(self.num_weeks):
                            for s in range(self.num_slots):
                                for a in range(self.num_alleys):
                                    if (t, t2, w, s, a) in self.play:
                                        if s % 2 == 0:
                                            matches_8pm.extend([self.play[(t, t2, w, s, a)], self.play[(t2, t, w, s, a)]])
                                        else:
                                            matches_9pm.extend([self.play[(t, t2, w, s, a)], self.play[(t2, t, w, s, a)]])

                if pref == "8:00 pm":
                    for var in matches_9pm:
                        penalties.extend([var, var])
                elif pref == "9:00 pm":
                    for var in matches_8pm:
                        penalties.extend([var, var])
                else:
                    max_matches = self.num_teams * self.matches_per_pair
                    time_diff = self.model.NewIntVar(-max_matches, max_matches, f'time_diff_t{t}')
                    abs_time_diff = self.model.NewIntVar(0, max_matches, f'abs_time_diff_t{t}')
                    
                    self.model.Add(time_diff == sum(matches_8pm) - sum(matches_9pm))
                    self.model.AddAbsEquality(abs_time_diff, time_diff)
                    
                    penalties.append(abs_time_diff)
            
            self.model.Minimize(sum(penalties))

        def solve(self):
            self.add_constraints()
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 240.0 
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
            "Playing?": [True] * count,
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
    if 'match_exceptions' not in st.session_state:
        st.session_state.match_exceptions = []
    if 'div1_data' not in st.session_state:
        st.session_state.div1_data = create_default_df("D1", 10)
    if 'div2_data' not in st.session_state:
        st.session_state.div2_data = create_default_df("D2", 10)

    # --- User Interface ---
    st.title("ABM Skittles Scheduler")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["1. Calendar & Format", "2. Venue Blocks", "3. Teams", "4. Clash Checker", "5. Generate"])

    with tab1:
        st.header("League Format")
        f_col1, f_col2 = st.columns(2)
        with f_col1:
            ui_num_divisions = st.radio("Number of Divisions", [1, 2], index=1, horizontal=True)
        with f_col2:
            ui_num_alleys = st.radio("Number of Alleys", [1, 2], index=1, horizontal=True)
            
        st.markdown("---")
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
        col_a, col_b = st.columns(2)
        
        with col_a:
            st.subheader("Add Venue/Alley Block")
            v_date = st.date_input("Date(s) Closed (Click twice for range)", value=[], key="v_date", format="DD/MM/YYYY")
            v_scope = st.selectbox("What is closed?", ["Whole Club", "Alley 1", "Alley 2"])
            if st.button("Add Venue Block"):
                if len(v_date) > 0:
                    start_d = v_date[0]
                    end_d = v_date[1] if len(v_date) > 1 else v_date[0]
                    
                    current = start_d
                    while current <= end_d:
                        if not any(b['Date'] == current and b['Scope'] == v_scope for b in st.session_state.venue_blocks):
                            st.session_state.venue_blocks.append({"Date": current, "Scope": v_scope})
                        current += datetime.timedelta(days=1)
                    st.rerun()
                else:
                    st.warning("Please select at least one date.")
                
            if st.session_state.venue_blocks:
                v_df = pd.DataFrame(st.session_state.venue_blocks)
                edited_v_df = st.data_editor(v_df, num_rows="dynamic", column_config={"Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY")}, key="v_editor", width="stretch")
                if not edited_v_df.empty:
                    edited_v_df['Date'] = pd.to_datetime(edited_v_df['Date']).dt.date
                st.session_state.venue_blocks = edited_v_df.to_dict('records')

        with col_b:
            st.subheader("Add Specific Team Block")
            t_date = st.date_input("Date(s) Unavailable (Click twice for range)", value=[], key="t_date", format="DD/MM/YYYY")
            t_team = st.text_input("Exact Team Name")
            if st.button("Add Team Block"):
                if t_team and len(t_date) > 0:
                    start_d = t_date[0]
                    end_d = t_date[1] if len(t_date) > 1 else t_date[0]
                    
                    current = start_d
                    while current <= end_d:
                        if not any(b['Date'] == current and b['Team'] == t_team for b in st.session_state.team_blocks):
                            st.session_state.team_blocks.append({"Date": current, "Team": t_team})
                        current += datetime.timedelta(days=1)
                    st.rerun()
                elif not t_team:
                    st.warning("Please enter a team name.")
                else:
                    st.warning("Please select at least one date.")
                    
            if st.session_state.team_blocks:
                t_df = pd.DataFrame(st.session_state.team_blocks)
                edited_t_df = st.data_editor(t_df, num_rows="dynamic", column_config={"Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY")}, key="t_editor", width="stretch")
                if not edited_t_df.empty:
                    edited_t_df['Date'] = pd.to_datetime(edited_t_df['Date']).dt.date
                st.session_state.team_blocks = edited_t_df.to_dict('records')

    with tab3:
        st.header("Division Setups & Import")
        
        default_url = "https://docs.google.com/spreadsheets/d/1x7NdJCc9_Wh_fRkuR_9kQ6bwEsYLqii_zKGLdvf-Dt0/export?format=csv"
        user_sheet_url = st.text_input("Google Sheet CSV Export Link:", value=default_url)
        
        if st.button("🔄 Sync with Google Sheets Form", type="primary"):
            try:
                df_import = pd.read_csv(user_sheet_url)
                
                def extract_division(df, div_name):
                    div_df = df[df['Division'] == div_name].copy()
                    if div_df.empty:
                        return pd.DataFrame()
                    
                    div_df = div_df.reset_index(drop=True)
                    
                    res = pd.DataFrame()
                    res['Playing?'] = [True] * len(div_df) 
                    res['Team Name'] = div_df['Team Name']
                    res['Monday'] = div_df['Monday']
                    res['Tuesday'] = div_df['Tuesday']
                    res['Wednesday'] = div_df['Wednesday']
                    res['Thursday'] = div_df['Thursday']
                    res['Prefers Time'] = div_df['Prefers Time']
                    return res

                st.session_state.div1_data = extract_division(df_import, 'Division 1')
                st.session_state.div2_data = extract_division(df_import, 'Division 2')
                
                if "div1_ui" in st.session_state:
                    del st.session_state["div1_ui"]
                if "div2_ui" in st.session_state:
                    del st.session_state["div2_ui"]
                
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
                st.success("Data successfully synced!")
            except Exception as e:
                st.error(f"Failed to fetch data. Please check the link is correct and publicly shared. Error: {e}")

        day_options = ["Any", "8:00 pm only", "9:00 pm only", "Unavailable"]
        col_config = {
            "Playing?": st.column_config.CheckboxColumn("Playing?", default=True),
            "Monday": st.column_config.SelectboxColumn("Monday", options=day_options),
            "Tuesday": st.column_config.SelectboxColumn("Tuesday", options=day_options),
            "Wednesday": st.column_config.SelectboxColumn("Wednesday", options=day_options),
            "Thursday": st.column_config.SelectboxColumn("Thursday", options=day_options),
            "Prefers Time": st.column_config.SelectboxColumn("Prefers Time", options=["No Preference", "8:00 pm", "9:00 pm"])
        }

        st.subheader("Division 1")
        div1_edited = st.data_editor(st.session_state.div1_data, column_config=col_config, num_rows="dynamic", key="div1_ui", width="stretch")
        
        # --- THE FAILSAFE: Check and patch missing columns caused by cache ---
        if 'Playing?' not in div1_edited.columns:
            div1_edited['Playing?'] = True
            
        if ui_num_divisions == 2:
            st.subheader("Division 2")
            div2_edited = st.data_editor(st.session_state.div2_data, column_config=col_config, num_rows="dynamic", key="div2_ui", width="stretch")
            if 'Playing?' not in div2_edited.columns:
                div2_edited['Playing?'] = True
        else:
            div2_edited = st.session_state.div2_data
            if 'Playing?' not in div2_edited.columns:
                div2_edited['Playing?'] = True

    with tab4:
        st.header("Clash Checker & Match Exceptions")
        
        d1 = div1_edited[div1_edited['Playing?'] == True].copy()
        d1['Division'] = 'Division 1'
        if ui_num_divisions == 2:
            d2 = div2_edited[div2_edited['Playing?'] == True].copy()
            d2['Division'] = 'Division 2'
            full_teams = pd.concat([d1, d2], ignore_index=True)
        else:
            full_teams = d1.reset_index(drop=True)
        
        if st.button("Check for Impossible Clashes"):
            clashes = find_impossible_matchups(full_teams, st.session_state.match_exceptions, ui_num_divisions)
            if clashes:
                for c in clashes:
                    st.error(f"🚨 **Mathematical Impossibility:** **{c[0]}** and **{c[1]}** have no common available days. The schedule will fail unless you add an exception below.")
            else:
                st.success("✅ No impossible clashes detected! All teams have at least one valid slot to play each other.")
                
        st.markdown("---")
        st.subheader("Add a Match Exception")
        if not full_teams.empty:
            col_e1, col_e2 = st.columns(2)
            with col_e1:
                exc_t1 = st.selectbox("Team 1", full_teams['Team Name'].tolist())
                exc_day = st.selectbox("Override Day", ["Monday", "Tuesday", "Wednesday", "Thursday"])
            with col_e2:
                exc_t2 = st.selectbox("Team 2", full_teams['Team Name'].tolist())
                exc_time = st.selectbox("Override Time", ["Both", "8:00 pm", "9:00 pm"])
                
            if st.button("Add Exception"):
                if exc_t1 != exc_t2:
                    st.session_state.match_exceptions.append({
                        "Team 1": exc_t1, "Team 2": exc_t2,
                        "Override Day": exc_day, "Override Time": exc_time
                    })
                    st.rerun()
                else:
                    st.error("Please select two different teams.")
                    
            if st.session_state.match_exceptions:
                exc_df = pd.DataFrame(st.session_state.match_exceptions)
                edited_exc_df = st.data_editor(exc_df, num_rows="dynamic", key="exc_editor", width="stretch")
                st.session_state.match_exceptions = edited_exc_df.to_dict('records')

    with tab5:
        st.header("Generate Schedule")
        
        if st.button("Run Optimisation Engine", type="primary"):
            with st.spinner("Calculating... Note: This requires complex maths and may take up to 4 minutes."):
                scheduler = ABMSchedulerEngine(
                    div1_edited, div2_edited, available_weeks, 
                    matches_per_pair=matches_per_pair,
                    venue_blocks=st.session_state.venue_blocks,
                    team_blocks=st.session_state.team_blocks,
                    match_exceptions=st.session_state.match_exceptions,
                    num_divisions=ui_num_divisions,
                    num_alleys=ui_num_alleys
                )
                schedule_data = scheduler.solve()
                
                if schedule_data:
                    st.success("Success! Here is the finalised schedule.")
                    df = pd.DataFrame(schedule_data)
                    
                    df = df.sort_values(by=["SortDate", "Time", "Alley"])
                    df = df[["Date", "Day", "Time", "Home Team Name", "Away Team Name", "Alley", "Division"]]
                    
                    st.dataframe(df, width="stretch")
                    
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button(label="Download Schedule as CSV", data=csv, file_name="abm_skittles_schedule.csv", mime="text/csv")
                else:
                    st.error("The engine couldn't find a solution. There are too many restrictions locking the maths up.")
