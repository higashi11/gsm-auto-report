import sqlite3
import requests
import os
from datetime import datetime, timedelta
from pathlib import Path
import json
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
import io
import base64

class GSMReporter:
    def __init__(self, db_path, webhook_url):
        self.db_path = db_path
        self.webhook_url = webhook_url
        self.state_file = "last_report_date.txt"
        self.timezone_offset = 9
    
    def get_last_report_date(self):
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                return f.read().strip()
        return None
    
    def save_report_date(self, date):
        with open(self.state_file, 'w') as f:
            f.write(date)
    
    def save_report_date_with_date(self, date_str):
        """Save report date for specific date"""
        report_file = f"last_report_{date_str}.txt"
        with open(report_file, 'w') as f:
            f.write(datetime.now().isoformat())
    
    def connect_db(self):
        return sqlite3.connect(self.db_path)
    
    def get_today_stats(self, days_ago=0):
        """Get statistics for specified date (0=today, 1=yesterday)"""
        conn = self.connect_db()
        cursor = conn.cursor()
        
        now = datetime.now()
        target_date = now - timedelta(days=days_ago)
        day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
        day_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59)
        
        start_timestamp = day_start.timestamp()
        end_timestamp = day_end.timestamp()
        
        stats = {}
        
        try:
            cursor.execute("""
                SELECT COUNT(*) FROM game_lines 
                WHERE timestamp >= ? AND timestamp <= ?
            """, (start_timestamp, end_timestamp))
            stats['lines_mined'] = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(*) FROM game_lines 
                WHERE timestamp >= ? AND timestamp <= ?
                AND (screenshot_in_anki != '' OR audio_in_anki != '')
            """, (start_timestamp, end_timestamp))
            stats['anki_cards_created'] = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(DISTINCT game_name) FROM game_lines 
                WHERE timestamp >= ? AND timestamp <= ?
            """, (start_timestamp, end_timestamp))
            stats['games_played'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM game_lines")
            stats['total_lines'] = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT game_name, 
                       SUM(LENGTH(line_text)) as total_chars,
                       COUNT(*) as line_count
                FROM game_lines 
                WHERE timestamp >= ? AND timestamp <= ?
                AND game_name IS NOT NULL AND game_name != ''
                GROUP BY game_name
                ORDER BY total_chars DESC
            """, (start_timestamp, end_timestamp))
            
            games_data = []
            total_chars = 0
            for row in cursor.fetchall():
                game_name, chars, lines = row
                games_data.append({
                    'name': game_name,
                    'chars': chars,
                    'lines': lines
                })
                total_chars += chars
            
            stats['games_list'] = games_data
            stats['total_chars'] = total_chars
            
            cursor.execute("""
                SELECT MIN(timestamp), MAX(timestamp) 
                FROM game_lines 
                WHERE timestamp >= ? AND timestamp <= ?
            """, (start_timestamp, end_timestamp))
            
            result = cursor.fetchone()
            if result[0] and result[1]:
                time_diff = result[1] - result[0]
                stats['play_time_hours'] = time_diff / 3600
            else:
                stats['play_time_hours'] = 0
            
        except sqlite3.OperationalError as e:
            print(f"Database query error: {e}")
            stats = {
                'lines_mined': 0,
                'anki_cards_created': 0,
                'games_played': 0,
                'total_lines': 0,
                'games_list': [],
                'total_chars': 0,
                'play_time_hours': 0
            }
        
        conn.close()
        return stats
    
    def get_activity_streak(self):
        conn = self.connect_db()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT DISTINCT DATE(timestamp, 'unixepoch', 'localtime') as activity_date
                FROM game_lines
                ORDER BY activity_date DESC
            """)
            
            dates = [row[0] for row in cursor.fetchall()]
            
            if not dates:
                return 0
            
            today = datetime.now().strftime('%Y-%m-%d')
            
            if dates[0] != today and dates[0] != (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'):
                return 0
            
            streak = 1
            for i in range(len(dates) - 1):
                current = datetime.strptime(dates[i], '%Y-%m-%d')
                next_date = datetime.strptime(dates[i + 1], '%Y-%m-%d')
                
                if (current - next_date).days == 1:
                    streak += 1
                else:
                    break
            
            return streak
            
        except sqlite3.OperationalError as e:
            print(f"Streak calculation error: {e}")
            return 0
        finally:
            conn.close()
    
    def create_activity_heatmap_image(self, days=30):
        conn = self.connect_db()
        cursor = conn.cursor()
        
        dates = []
        char_counts = []
        
        for days_ago in range(days - 1, -1, -1):
            date = datetime.now() - timedelta(days=days_ago)
            day_start = datetime(date.year, date.month, date.day, 0, 0, 0)
            day_end = datetime(date.year, date.month, date.day, 23, 59, 59)
            
            start_ts = day_start.timestamp()
            end_ts = day_end.timestamp()
            
            try:
                cursor.execute("""
                    SELECT SUM(LENGTH(line_text)) FROM game_lines 
                    WHERE timestamp >= ? AND timestamp <= ?
                """, (start_ts, end_ts))
                
                result = cursor.fetchone()[0]
                char_count = result if result else 0
                
                dates.append(date)
                char_counts.append(char_count)
                
            except sqlite3.OperationalError:
                dates.append(date)
                char_counts.append(0)
        
        conn.close()
        
        try:
            japanese_fonts = ['Yu Gothic', 'Hiragino Sans', 'Noto Sans CJK JP', 'MS Gothic', 'AppleGothic']
            available_font = None
            for font_name in japanese_fonts:
                try:
                    font_manager.findfont(font_name, fallback_to_default=False)
                    available_font = font_name
                    break
                except:
                    continue
            
            if available_font:
                plt.rcParams['font.family'] = available_font
        except:
            pass
        
        fig, ax = plt.subplots(figsize=(12, 4), facecolor='#2b2d31')
        ax.set_facecolor('#2b2d31')
        
        colors = ['#5865f2' if count > 0 else '#404249' for count in char_counts]
        bars = ax.bar(dates, char_counts, color=colors, width=0.8, edgecolor='none')
        
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
        
        ax.set_xlabel('Date', color='#b5bac1', fontsize=10)
        ax.set_ylabel('Characters', color='#b5bac1', fontsize=10)
        ax.set_title('Activity Heatmap - Past 30 Days', color='#ffffff', fontsize=14, pad=15)
        
        ax.grid(True, alpha=0.1, color='#ffffff', linestyle='-', linewidth=0.5)
        ax.set_axisbelow(True)
        
        ax.spines['bottom'].set_color('#404249')
        ax.spines['top'].set_color('#404249')
        ax.spines['left'].set_color('#404249')
        ax.spines['right'].set_color('#404249')
        ax.tick_params(colors='#b5bac1', labelsize=8)
        
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, facecolor='#2b2d31', edgecolor='none')
        buf.seek(0)
        plt.close()
        
        return buf
    
    def format_report(self, stats, streak, days_ago=0):
        target_date = datetime.now() - timedelta(days=days_ago)
        date_str = target_date.strftime('%B %d, %Y')
        weekday = target_date.strftime('%A')
        
        title = "🎮 GSM Daily Report"
        if days_ago == 1:
            title = "🎮 GSM Daily Report (Yesterday)"
        elif days_ago > 1:
            title = f"🎮 GSM Daily Report ({days_ago} days ago)"
        
        embed = {
            "title": title,
            "description": f"**{date_str} ({weekday})**",
            "color": 5814783,
            "fields": [
                {
                    "name": "⏱️ Play Time",
                    "value": f"**{stats['play_time_hours']:.1f}** hours",
                    "inline": True
                },
                {
                    "name": "📊 Characters",
                    "value": f"**{stats['total_chars']:,}** chars",
                    "inline": True
                },
                {
                    "name": "🔥 Streak",
                    "value": f"**{streak}** days",
                    "inline": True
                },
                {
                    "name": "✨ Anki Cards",
                    "value": f"**{stats['anki_cards_created']}** cards",
                    "inline": True
                },
                {
                    "name": "🎯 Games Played",
                    "value": f"**{stats['games_played']}** games",
                    "inline": True
                }
            ],
            "footer": {
                "text": "GameSentenceMiner Auto Report"
            },
            "timestamp": datetime.now().isoformat()
        }
        
        if stats['games_list']:
            games_text = ""
            for i, game in enumerate(stats['games_list'][:5], 1):
                games_text += f"{i}. **{game['name']}**\n"
                games_text += f"   └ {game['lines']} lines / {game['chars']:,} chars\n"
            
            if len(stats['games_list']) > 5:
                remaining = len(stats['games_list']) - 5
                games_text += f"\n...and {remaining} more"
            
            embed["fields"].append({
                "name": "🎮 Today's Games",
                "value": games_text,
                "inline": False
            })
        
        return embed
    
    def send_to_discord(self, embed, heatmap_image):
        files = {
            'file': ('heatmap.png', heatmap_image, 'image/png')
        }
        
        embed['image'] = {'url': 'attachment://heatmap.png'}
        
        payload = {
            'embeds': [embed]
        }
        
        data = {
            'payload_json': json.dumps(payload)
        }
        
        try:
            response = requests.post(
                self.webhook_url,
                data=data,
                files=files,
                timeout=10
            )
            
            if response.status_code == 204 or response.status_code == 200:
                print("✅ Report sent successfully")
                return True
            else:
                print(f"❌ Send failed: {response.status_code}")
                print(response.text)
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"❌ Send error: {e}")
            return False
    
    def check_and_send_missing_reports(self, max_days_back=7):
        """Check past N days for missing reports and send them"""
        print("🔍 Checking for missing reports...")
        
        reports_sent = []
        today = datetime.now()
        
        for days_ago in range(1, max_days_back + 1):
            target_date = today - timedelta(days=days_ago)
            date_str = target_date.strftime('%Y-%m-%d')
            report_file = f"last_report_{date_str}.txt"
            
            if os.path.exists(report_file):
                print(f"  ✅ {date_str}: Already sent")
                continue
            
            stats = self.get_today_stats(days_ago=days_ago)
            
            if stats['total_chars'] == 0:
                print(f"  ⚪ {date_str}: No data (skipped)")
                continue
            
            print(f"  📤 {date_str}: Sending missing report...")
            
            try:
                streak = self.get_activity_streak()
                heatmap_image = self.create_activity_heatmap_image()
                embed = self.format_report(stats, streak, days_ago=days_ago)
                
                if self.send_to_discord(embed, heatmap_image):
                    with open(report_file, 'w') as f:
                        f.write(datetime.now().isoformat())
                    reports_sent.append(date_str)
                    print(f"  ✅ {date_str}: Sent!")
                    
            except Exception as e:
                print(f"  ❌ {date_str}: Error - {e}")
        
        if reports_sent:
            print(f"\n✨ Sent {len(reports_sent)} missing report(s)")
        else:
            print("\n✅ All reports up to date!")
        
        return len(reports_sent)
    
    def generate_and_send_report(self, force=False, days_ago=0, check_missing=False):
        """Generate and send report"""
        if check_missing:
            self.check_and_send_missing_reports(max_days_back=7)
            return
        
        target_date = datetime.now() - timedelta(days=days_ago)
        date_str = target_date.strftime('%Y-%m-%d')
        report_file = f"last_report_{date_str}.txt"
        
        if not force and os.path.exists(report_file):
            print(f"ℹ️  Report for {date_str} already sent")
            return
        
        print(f"📊 Generating report for {date_str}...")
        stats = self.get_today_stats(days_ago=days_ago)
        
        if stats['total_chars'] == 0:
            print(f"ℹ️  No data for {date_str}, skipping...")
            return
        
        streak = self.get_activity_streak()
        
        print("📈 Creating heatmap image...")
        heatmap_image = self.create_activity_heatmap_image()
        
        embed = self.format_report(stats, streak, days_ago=days_ago)
        
        if self.send_to_discord(embed, heatmap_image):
            self.save_report_date_with_date(date_str)
            print("✅ Report sent successfully!")
            print(f"   - Date: {date_str}")
            print(f"   - Play time: {stats['play_time_hours']:.1f} hours")
            print(f"   - Characters: {stats['total_chars']:,}")
            print(f"   - Streak: {streak} days")
    
    def list_tables(self):
        conn = self.connect_db()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        conn.close()
        
        print("\n📋 Database tables:")
        for table in tables:
            print(f"  - {table[0]}")
        
        return [t[0] for t in tables]
    
    def show_sample_data(self):
        conn = self.connect_db()
        cursor = conn.cursor()
        
        print("\n📝 Latest entries (5 samples):")
        try:
            cursor.execute("""
                SELECT id, game_name, line_text, timestamp 
                FROM game_lines 
                ORDER BY timestamp DESC 
                LIMIT 5
            """)
            rows = cursor.fetchall()
            for row in rows:
                dt = datetime.fromtimestamp(row[3])
                print(f"  {dt.strftime('%Y-%m-%d %H:%M')} | {row[1]} | {row[2][:30]}...")
        except sqlite3.OperationalError as e:
            print(f"  Error: {e}")
        
        conn.close()


def find_gsm_db():
    if os.name == 'nt':
        appdata = os.getenv('APPDATA')
        db_path = os.path.join(appdata, 'GameSentenceMiner', 'gsm.db')
    else:
        config_dir = os.path.expanduser('~/.config')
        db_path = os.path.join(config_dir, 'GameSentenceMiner', 'gsm.db')
    
    if os.path.exists(db_path):
        print(f"✅ GSM database found: {db_path}")
        return db_path
    else:
        print(f"⚠️  GSM database not found: {db_path}")
        return None


def create_config_file():
    config = {
        "db_path": "",
        "webhook_url": ""
    }
    
    print("\n=== GSM Reporter Initial Setup ===\n")
    
    auto_db = find_gsm_db()
    if auto_db:
        use_auto = input(f"Use detected path? (Y/n): ").strip().lower()
        if use_auto != 'n':
            config["db_path"] = auto_db
    
    if not config["db_path"]:
        config["db_path"] = input("Enter GSM database path: ").strip()
    
    print("\nHow to create Discord Webhook:")
    print("1. Open channel settings in Discord")
    print("2. Integrations → Webhooks")
    print("3. New Webhook → Set name → Copy URL\n")
    
    config["webhook_url"] = input("Enter Discord Webhook URL: ").strip()
    
    with open("gsm_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    print("\n✅ Configuration saved: gsm_config.json")
    return config


def load_config():
    if os.path.exists("gsm_config.json"):
        with open("gsm_config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return None


if __name__ == "__main__":
    import sys
    
    force = "--force" in sys.argv
    debug = "--debug" in sys.argv
    setup = "--setup" in sys.argv
    yesterday = "--yesterday" in sys.argv
    check_missing = "--check-missing" in sys.argv
    
    is_github_actions = os.getenv('GITHUB_ACTIONS') == 'true'
    
    if is_github_actions:
        print("🔧 Running in GitHub Actions mode")
        db_path = os.path.join(os.getcwd(), 'gsm.db')
        webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
        
        if not os.path.exists(db_path):
            print(f"❌ Database file not found: {db_path}")
            sys.exit(1)
        if not webhook_url:
            print("❌ DISCORD_WEBHOOK_URL not set")
            sys.exit(1)
        
        reporter = GSMReporter(db_path, webhook_url)
        reporter.generate_and_send_report(force=True, days_ago=1)
        
    else:
        if setup or not os.path.exists("gsm_config.json"):
            config = create_config_file()
        else:
            config = load_config()
        
        if not config or not config.get("db_path") or not config.get("webhook_url"):
            print("❌ Configuration incomplete. Run with --setup")
            sys.exit(1)
        
        reporter = GSMReporter(config["db_path"], config["webhook_url"])
        
        if debug:
            reporter.list_tables()
            reporter.show_sample_data()
        
        if check_missing:
            reporter.generate_and_send_report(check_missing=True)
        elif yesterday:
            reporter.generate_and_send_report(force=force, days_ago=1)
        else:
            reporter.generate_and_send_report(force=force)
