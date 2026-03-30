import streamlit as st
import pandas as pd
import datetime
import re
from ortools.sat.python import cp_model

st.set_page_config(page_title="ABM Skittles Scheduler", layout="wide")

# --- Security ---
def check_password():
    def password_entered():
        app_pw = st.secrets.get("app_password")
        if app_pw and st.session_state.get("password") == app_pw:
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
            
            self.pairs = [
                (t1, t2) for t1 in range(self.num_teams) 
                for t2 in range(t1 + 1, self.num_teams) 
                if self.team_data.iloc[t1]['Division'] == self.team_data.iloc[t2]['Division']
            ]
            
            # For diagnostics
            self.team_names = list(self.team_data["Team Name"])
            self.valid_slots = {
                t: get_available_slots(self.team_data.iloc[t])
                for t in range(self.num_teams)
            }
            
        def _build(self):
            self.model = cp_model.CpModel()
            self.match = {}
            self.home = {}
            self._create_variables()

        def _create_variables(self):
            for (t1, t2) in self.pairs:
                for w in range(self.num_weeks):
                    for s in range(self.num_slots):
                        for a in range(self.num_alleys):
                            key = (t1, t2, w, s, a)
                            self.match[key] = self.model.NewBoolVar(f'm_{t1}_{t2}_{w}_{s}_{a}')
                            self.home[key] = self.model.NewBoolVar(f'h_{t1}_{t2}_{w}_{s}_{a}')
                            
                            self.model.Add(self.home[key] <= self.match[key])

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

        def add_constraints(self, relax_time=False, relax_clumping=False):
            # 1. Matches per pair & Exact Home/Away split
            for (t1, t2) in self.pairs:
                pair_matches = [self.match[(t1, t2, w, s, a)] for w in range(self.num_weeks) for s in range(self.num_slots) for a in range(self.num_alleys)]
                pair_home = [self.home[(t1, t2, w, s, a)] for w in range(self.num_weeks) for s in range(self.num_slots) for a in range(self.num_alleys)]
                
                self.model.Add(cp_model.LinearExpr.Sum(pair_matches) == self.matches_per_pair)
                
                home_sum = cp_model.LinearExpr.Sum(pair_home)
                min_home = self.matches_per_pair // 2
                max_home = (self.matches_per_pair + 1) // 2
                self.model.Add(home_sum >= min_home)
                self.model.Add(home_sum <= max_home)
            
            # 2. One match per week per team
            for t in range(self.num_teams):
                for w in range(self.num_weeks):
                    week_vars = []
                    for (t1, t2) in self.pairs:
                        if t == t1 or t == t2:
                            for s in range(self.num_slots):
                                for a in range(self.num_alleys):
                                    week_vars.append(self.match[(t1, t2, w, s, a)])
                    if week_vars:
                        self.model.Add(cp_model.LinearExpr.Sum(week_vars) <= 1)

            # 3. Slot capacity (no double booking alleys)
            for w in range(self.num_weeks):
                for s in range(self.num_slots):
                    for a in range(self.num_alleys):
                        slot_vars = [self.match[(t1, t2, w, s, a)] for (t1, t2) in self.pairs]
                        if slot_vars:
                            self.model.Add(cp_model.LinearExpr.Sum(slot_vars) <= 1)

            # 4. Handle Specific Unavailable Days & Exceptions
            for t in range(self.num_teams):
                t_name = self.team_data.iloc[t]['Team Name']
                row = self.team_data.iloc[t]
                days_config = {
                    'Monday': (0, 1, row.get('Monday', 'Any')),
                    'Tuesday': (2, 3, row.get('Tuesday', 'Any')),
                    'Wednesday': (4, 5, row.get('Wednesday', 'Any')),
                    'Thursday': (6, 7, row.get('Thursday', 'Any'))
                }
                slots_to_block = []
                for day, (s8, s9, config) in days_config.items():
                    if config == "Unavailable": slots_to_block.extend([s8, s9])
                    elif config == "8:00 pm only": slots_to_block.append(s9)
                    elif config == "9:00 pm only": slots_to_block.append(s8)
                
                if slots_to_block:
                    for (t1, t2) in self.pairs:
                        if t == t1 or t == t2:
                            t2_name = self.team_data.iloc[t2 if t == t1 else t1]['Team Name']
                            overridden = self.get_overridden_slots(t_name, t2_name)
                            
                            for w in range(self.num_weeks):
                                for s in slots_to_block:
                                    if s not in overridden:
                                        for a in range(self.num_alleys):
                                            var = self.match.get((t1, t2, w, s, a))
                                            if var is not None:
                                                self.model.Add(var == 0)

            # 5. Handle Venue & Team Specific Dates
            for w in range(self.num_weeks):
                week_start = self.play_weeks[w]
                for s in range(self.num_slots):
                    current_date = week_start + datetime.timedelta(days=DAY_OFFSETS[s])
                    
                    for block in self.venue_blocks:
                        if block['Date'] == current_date:
                            alleys_to_block = [0, 1] if block['Scope'] == "Whole Club" else ([0] if block['Scope'] == "Alley 1" else [1])
                            for a in alleys_to_block:
                                if a < self.num_alleys:
                                    for (t1, t2) in self.pairs:
                                        var = self.match.get((t1, t2, w, s, a))
                                        if var is not None:
                                            self.model.Add(var == 0)

                    for block in self.team_blocks:
                        if block['Date'] == current_date:
                            target_team = block['Team']
                            for (t1, t2) in self.pairs:
                                name1 = self.team_data.iloc[t1]['Team Name']
                                name2 = self.team_data.iloc[t2]['Team Name']
                                if target_team in (name1, name2):
                                    for a in range(self.num_alleys):
                                        var = self.match.get((t1, t2, w, s, a))
                                        if var is not None:
                                            self.model.Add(var == 0)

            # 6. Global Balancing & Soft Penalties
            penalties = []
            max_matches = self.num_weeks
            
            for t in range(self.num_teams):
                home_vars, away_vars = [], []
                alley0_vars, alley1_vars = [], []
                early_vars, late_vars = [], []
                team_day_vars = {(w, d): [] for w in range(self.num_weeks) for d in range(4)}
                
                for (t1, t2) in self.pairs:
                    if t in (t1, t2):
                        for w in range(self.num_weeks):
                            for s in range(self.num_slots):
                                for a in range(self.num_alleys):
                                    m = self.match[(t1, t2, w, s, a)]
                                    h = self.home[(t1, t2, w, s, a)]
                                    
                                    if t == t1:
                                        home_vars.append(h)
                                        away_vars.append(m - h)
                                    else:
                                        home_vars.append(m - h)
                                        away_vars.append(h)
                                        
                                    if a == 0: alley0_vars.append(m)
                                    elif a == 1: alley1_vars.append(m)
                                    
                                    if s % 2 == 0: early_vars.append(m)
                                    else: late_vars.append(m)
                                    
                                    d = s // 2
                                    team_day_vars[(w, d)].append(m)

                # Overall Home/Away Balance
                ha_diff = self.model.NewIntVar(-max_matches, max_matches, f"ha_diff_{t}")
                ha_abs = self.model.NewIntVar(0, max_matches, f"ha_abs_{t}")
                self.model.Add(ha_diff == cp_model.LinearExpr.Sum(home_vars) - cp_model.LinearExpr.Sum(away_vars))
                self.model.AddAbsEquality(ha_abs, ha_diff)
                penalties.append(ha_abs * 2)

                # Alley Parity
                if self.num_alleys == 2:
                    al_diff = self.model.NewIntVar(-max_matches, max_matches, f"al_diff_{t}")
                    al_abs = self.model.NewIntVar(0, max_matches, f"al_abs_{t}")
                    self.model.Add(al_diff == cp_model.LinearExpr.Sum(alley0_vars) - cp_model.LinearExpr.Sum(alley1_vars))
                    self.model.AddAbsEquality(al_abs, al_diff)
                    penalties.append(al_abs)

                # Time Parity
                pref = self.team_data.iloc[t].get('Prefers Time', 'No Preference')
                early_sum = cp_model.LinearExpr.Sum(early_vars)
                late_sum = cp_model.LinearExpr.Sum(late_vars)
                
                if pref == "8:00 pm":
                    if not relax_time:
                        penalties.append(late_sum * 10)
                elif pref == "9:00 pm":
                    if not relax_time:
                        penalties.append(early_sum * 10)
                else:
                    td_diff = self.model.NewIntVar(-max_matches, max_matches, f"td_diff_{t}")
                    td_abs = self.model.NewIntVar(0, max_matches, f"td_abs_{t}")
                    self.model.Add(td_diff == early_sum - late_sum)
                    self.model.AddAbsEquality(td_abs, td_diff)
                    penalties.append(td_abs)

                # Anti-Clumping
                if not relax_clumping:
                    for w in range(self.num_weeks - 2):
                        for d in range(4):
                            window_vars = team_day_vars[(w, d)] + team_day_vars[(w+1, d)] + team_day_vars[(w+2, d)]
                            window_sum = cp_model.LinearExpr.Sum(window_vars)
                            
                            is_two_clump = self.model.NewBoolVar(f"two_clump_{t}_{w}_{d}")
                            is_three_clump = self.model.NewBoolVar(f"three_clump_{t}_{w}_{d}")
                            
                            self.model.Add(window_sum >= 2).OnlyEnforceIf(is_two_clump)
                            self.model.Add(window_sum < 2).OnlyEnforceIf(is_two_clump.Not())
                            self.model.Add(window_sum >= 3).OnlyEnforceIf(is_three_clump)
                            self.model.Add(window_sum < 3).OnlyEnforceIf(is_three_clump.Not())
                            
                            penalties.append(is_two_clump * 1)
                            penalties.append(is_three_clump * 100) 

            if penalties:
                self.model.Minimize(cp_model.LinearExpr.Sum(penalties))

        # --- DIAGNOSTIC X-RAY ---
        def diagnose_failure(self):
            issues = []
            
            # Math check 1: Are there enough slots in the year?
            total_required = len(self.pairs) * self.matches_per_pair
            total_slots = self.num_weeks * self.num_slots * self.num_alleys
            
            if total_slots < total_required:
                issues.append(f"Not enough total slots in the calendar to complete the season. Requires {total_required} slots, but only {total_slots} are available before closures.")

            # Math check 2: Do the teams actually overlap?
            def has_valid_exception(t1_name, t2_name):
                for exc in self.match_exceptions:
                    if (exc['Team 1'] == t1_name and exc['Team 2'] == t2_name) or \
                       (exc['Team 1'] == t2_name and exc['Team 2'] == t1_name):
                        return True
                return False

            for (t1, t2) in self.pairs:
                valid = any(
                    s in self.valid_slots[t1] and s in self.valid_slots[t2]
                    for s in range(self.num_slots)
                )
                if not valid:
                    name1 = self.team_names[t1]
                    name2 = self.team_names[t2]
                    if not has_valid_exception(name1, name2):
                        issues.append(f"No overlapping days available for {name1} vs {name2}.")

            return issues

        def solve(self, time_limit):
            def run_attempt(msg, r_time, r_clump, t_limit):
                self._build()
                self.add_constraints(relax_time=r_time, relax_clumping=r_clump)
                solver = cp_model.CpSolver()
                solver.parameters.max_time_in_seconds = t_limit
                solver.parameters.num_search_workers = 8 
                status = solver.Solve(self.model)
                if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                    return self._extract(solver), status, msg
                return None, status, msg

            # Attempt 1: Strict Mode
            res, status, msg = run_attempt("Optimal Solution Found!", False, False, time_limit)
            if res: return {"error": False, "message": msg, "data": res}
            
            if status == cp_model.INFEASIBLE:
                # Attempt 2: Relax Time Preferences slightly
                res, status, msg = run_attempt("Solution found by relaxing strict Time Preferences", True, False, time_limit * 0.5)
                if res: return {"error": False, "message": msg, "data": res}
                
                # Attempt 3: Relax Time & Clumping Rules
                res, status, msg = run_attempt("Solution found by relaxing Time & Clumping Rules (Schedule was very tight)", True, True, time_limit * 0.5)
                if res: return {"error": False, "message": msg, "data": res}
            
            return {"error": True, "diagnostics": self.diagnose_failure()}

        def _extract(self, solver):
            results = []
            for (t1, t2, w, s, a), var in self.match.items():
                if solver.Value(var):
                    date = self.play_weeks[w] + datetime.timedelta(days=DAY_OFFSETS[s])
                    is_home = solver.Value(self.home[(t1, t2, w, s, a)]) == 1
                    
                    home_name = self.team_data.iloc[t1]['Team Name'] if is_home else self.team_data.iloc[t2]['Team Name']
                    away_name = self.team_data.iloc[t2]['Team Name'] if is_home else self.team_data.iloc[t1]['Team Name']
                    
                    results.append({
                        "SortDate": date,
                        "Date": date.strftime("%d %b %Y"),
                        "Day": date.strftime("%a"),
                        "Time": "8:00 pm" if s % 2 == 0 else "9:00 pm",
                        "Home Team Name": home_name,
                        "Away Team Name": away_name,
                        "Alley": f"Alley {a+1}",
                        "Division": self.team_data.iloc[t1]['Division']
                    })
            return results

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
    if 'sync_key' not in st.session_state:
        st.session_state.sync_key = 0
    if 'schedule_result' not in st.session_state:
        st.session_state.schedule_result = None

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
        
        st.markdown("---")
        ui_timeout = st.slider("Solver Time Limit (Seconds)", min_value=30, max_value=300, value=120, help="Higher numbers give the solver more time to find optimal schedules for complex configurations.")

    with tab2:
        st.header("Venue & Specific Date Blockers")
        col_a, col_b = st.columns(2)
        
        with col_a:
            st.subheader("Add Venue/Alley Block")
            v_date = st.date_input("Date(s) Closed (Select one date, or a start and end date)", value=[], key="v_date")
            v_scope = st.selectbox("What is closed?", ["Whole Club", "Alley 1", "Alley 2"])
            if st.button("Add Venue Block", key="add_venue"):
                dates = []
                if isinstance(v_date, (list, tuple)): dates = list(v_date)
                elif v_date: dates = [v_date]

                if len(dates) > 0:
                    start_d = dates[0]
                    end_d = dates[1] if len(dates) > 1 else dates[0]
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
                edited_v_df = st.data_editor(v_df, num_rows="dynamic", column_config={"Date": st.column_config.DateColumn("Date")}, key="v_editor")
                if not edited_v_df.empty and 'Date' in edited_v_df.columns:
                    edited_v_df['Date'] = pd.to_datetime(edited_v_df['Date']).dt.date
                st.session_state.venue_blocks = edited_v_df.to_dict('records')

        with col_b:
            st.subheader("Add Specific Team Block")
            t_date = st.date_input("Date(s) Unavailable (Select one date, or a start and end date)", value=[], key="t_date")
            t_team = st.text_input("Exact Team Name")
            if st.button("Add Team Block", key="add_team"):
                dates = []
                if isinstance(t_date, (list, tuple)): dates = list(t_date)
                elif t_date: dates = [t_date]

                if t_team and len(dates) > 0:
                    start_d = dates[0]
                    end_d = dates[1] if len(dates) > 1 else dates[0]
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
                edited_t_df = st.data_editor(t_df, num_rows="dynamic", column_config={"Date": st.column_config.DateColumn("Date")}, key="t_editor")
                if not edited_t_df.empty and 'Date' in edited_t_df.columns:
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
                    if div_df.empty: return pd.DataFrame()
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
                
                parsed_blocks = []
                for _, row in df_import.iterrows():
                    dates_str = row.get('Specific Unavailable Dates', '')
                    t_name = row['Team Name']
                    if pd.notna(dates_str) and str(dates_str).strip():
                        raw_dates = re.split(r'[,\n]+', str(dates_str))
                        for rd in raw_dates:
                            rd = rd.strip()
                            if not rd: continue
                            try: p_date = datetime.datetime.strptime(rd, "%d/%m/%y").date()
                            except ValueError:
                                try: p_date = datetime.datetime.strptime(rd, "%d/%m/%Y").date()
                                except ValueError:
                                    st.warning(f"Could not read date '{rd}' for '{t_name}'. Add manually in Tab 2.")
                                    continue
                            parsed_blocks.append({"Date": p_date, "Team": t_name})
                            
                existing = {(b['Date'], b['Team']) for b in st.session_state.team_blocks}
                for pb in parsed_blocks:
                    if (pb['Date'], pb['Team']) not in existing:
                        st.session_state.team_blocks.append(pb)
                        existing.add((pb['Date'], pb['Team']))
                
                st.session_state.sync_key += 1
                st.success("Data successfully synced! (Please refresh the page to update the tables).")
            except Exception as e:
                st.error(f"Failed to fetch data. Error: {e}")

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
        if 'Playing?' not in st.session_state.div1_data.columns:
            st.session_state.div1_data.insert(0, 'Playing?', True)
            
        div1_edited = st.data_editor(st.session_state.div1_data, column_config=col_config, num_rows="dynamic", key=f"div1_ui_{st.session_state.sync_key}")
        if 'Playing?' not in div1_edited.columns: div1_edited.insert(0, 'Playing?', True)
            
        if ui_num_divisions == 2:
            st.subheader("Division 2")
            if 'Playing?' not in st.session_state.div2_data.columns:
                st.session_state.div2_data.insert(0, 'Playing?', True)
                
            div2_edited = st.data_editor(st.session_state.div2_data, column_config=col_config, num_rows="dynamic", key=f"div2_ui_{st.session_state.sync_key}")
            if 'Playing?' not in div2_edited.columns: div2_edited.insert(0, 'Playing?', True)
        else:
            div2_edited = st.session_state.div2_data
            if 'Playing?' not in div2_edited.columns:
                temp_df = div2_edited.copy()
                temp_df.insert(0, 'Playing?', True)
                div2_edited = temp_df
                st.session_state.div2_data = temp_df

    with tab4:
        st.header("Clash Checker & Match Exceptions")
        
        d1 = div1_edited.copy()
        if 'Playing?' in d1.columns: d1 = d1[d1['Playing?'] == True]
        d1['Division'] = 'Division 1'
        
        if ui_num_divisions == 2:
            d2 = div2_edited.copy()
            if 'Playing?' in d2.columns: d2 = d2[d2['Playing?'] == True]
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
                
            if st.button("Add Exception", key="add_exc"):
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
                edited_exc_df = st.data_editor(exc_df, num_rows="dynamic", key="exc_editor")
                st.session_state.match_exceptions = edited_exc_df.to_dict('records')

    with tab5:
        st.header("Generate Schedule")
        
        # 1. THE BUTTON - Triggers calculation and saves to memory
        if st.button("Run Optimisation Engine", type="primary"):
            with st.spinner("Calculating... Note: The engine will now automatically relax rules if it hits a mathematical brick wall."):
                scheduler = ABMSchedulerEngine(
                    div1_edited, div2_edited, available_weeks, 
                    matches_per_pair=matches_per_pair,
                    venue_blocks=st.session_state.venue_blocks,
                    team_blocks=st.session_state.team_blocks,
                    match_exceptions=st.session_state.match_exceptions,
                    num_divisions=ui_num_divisions,
                    num_alleys=ui_num_alleys
                )
                
                # Save the raw dictionary to Streamlit's permanent session state
                st.session_state.schedule_result = scheduler.solve(time_limit=ui_timeout)

        # 2. THE DISPLAY - Reads from memory so it survives UI updates
        if st.session_state.schedule_result is not None:
            result = st.session_state.schedule_result
            
            if isinstance(result, dict):
                if result.get("error"):
                    st.error("❌ No valid schedule found")
                    for issue in result.get("diagnostics", []):
                        st.warning(issue)
                else:
                    st.success(result["message"])
                    
                    df = pd.DataFrame(result["data"])
                    df = df.sort_values(by=["SortDate", "Time", "Alley"])
                    
                    df_display = df[[
                        "Date", "Day", "Time", 
                        "Home Team Name", "Away Team Name", 
                        "Alley"
                    ]]
                    
                    st.dataframe(df_display)
                    
                    st.subheader("👥 Quick Team View")
                    team = st.selectbox("Select a Team to filter:", sorted(pd.concat([df["Home Team Name"], df["Away Team Name"]]).unique()))
                    tdf = df[(df["Home Team Name"] == team) | (df["Away Team Name"] == team)]
                    st.dataframe(tdf)
                    
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button(label="Download Schedule as CSV", data=csv, file_name="abm_skittles_schedule.csv", mime="text/csv")
