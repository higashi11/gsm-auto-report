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
        """
        初期化
        
        Args:
            db_path: GSMのデータベースファイルパス (.db)
            webhook_url: Discord Webhook URL
        """
        self.db_path = db_path
        self.webhook_url = webhook_url
        self.state_file = "last_report_date.txt"
        self.timezone_offset = 9  # JSTはUTC+9
    
    def get_last_report_date(self):
        """最後の報告日を取得"""
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                return f.read().strip()
        return None
    
    def save_report_date(self, date):
        """報告日を保存"""
        with open(self.state_file, 'w') as f:
            f.write(date)
    
    def connect_db(self):
        """データベースに接続"""
        return sqlite3.connect(self.db_path)
    
    def get_today_stats(self):
        """本日の統計を取得（タイムスタンプから直接計算）"""
        conn = self.connect_db()
        cursor = conn.cursor()
        
        # 今日の開始と終了のUNIXタイムスタンプを取得（JST基準）
        now = datetime.now()
        today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
        today_end = datetime(now.year, now.month, now.day, 23, 59, 59)
        
        start_timestamp = today_start.timestamp()
        end_timestamp = today_end.timestamp()
        
        stats = {}
        
        try:
            # 本日追加された文章数を取得（timestamp列を使用）
            cursor.execute("""
                SELECT COUNT(*) FROM game_lines 
                WHERE timestamp >= ? AND timestamp <= ?
            """, (start_timestamp, end_timestamp))
            stats['lines_mined'] = cursor.fetchone()[0]
            
            # 本日Ankiカードに追加された文章数
            cursor.execute("""
                SELECT COUNT(*) FROM game_lines 
                WHERE timestamp >= ? AND timestamp <= ?
                AND (screenshot_in_anki != '' OR audio_in_anki != '')
            """, (start_timestamp, end_timestamp))
            stats['anki_cards_created'] = cursor.fetchone()[0]
            
            # 本日プレイしたゲーム数（ユニークなgame_name）
            cursor.execute("""
                SELECT COUNT(DISTINCT game_name) FROM game_lines 
                WHERE timestamp >= ? AND timestamp <= ?
            """, (start_timestamp, end_timestamp))
            stats['games_played'] = cursor.fetchone()[0]
            
            # 累計文章数
            cursor.execute("SELECT COUNT(*) FROM game_lines")
            stats['total_lines'] = cursor.fetchone()[0]
            
            # 本日プレイしたゲームのリストと文字数
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
            
            # 本日のプレイ時間を計算（最初と最後のタイムスタンプから）
            cursor.execute("""
                SELECT MIN(timestamp), MAX(timestamp) 
                FROM game_lines 
                WHERE timestamp >= ? AND timestamp <= ?
            """, (start_timestamp, end_timestamp))
            
            result = cursor.fetchone()
            if result[0] and result[1]:
                time_diff = result[1] - result[0]
                stats['play_time_hours'] = time_diff / 3600  # 秒を時間に変換
            else:
                stats['play_time_hours'] = 0
            
        except sqlite3.OperationalError as e:
            print(f"データベースクエリエラー: {e}")
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
    
    def get_weekly_trend(self):
        """過去7日間のトレンドを取得"""
        conn = self.connect_db()
        cursor = conn.cursor()
        
        # 過去7日間の各日の開始タイムスタンプを計算
        weekly_data = []
        
        for days_ago in range(6, -1, -1):  # 6日前から今日まで
            date = datetime.now() - timedelta(days=days_ago)
            day_start = datetime(date.year, date.month, date.day, 0, 0, 0)
            day_end = datetime(date.year, date.month, date.day, 23, 59, 59)
            
            start_ts = day_start.timestamp()
            end_ts = day_end.timestamp()
            
            try:
                cursor.execute("""
                    SELECT COUNT(*) FROM game_lines 
                    WHERE timestamp >= ? AND timestamp <= ?
                """, (start_ts, end_ts))
                
                count = cursor.fetchone()[0]
                date_str = date.strftime('%m/%d')
                weekly_data.append((date_str, count))
                
            except sqlite3.OperationalError:
                weekly_data.append((date.strftime('%m/%d'), 0))
        
        conn.close()
        return weekly_data
    
    def get_activity_streak(self):
        """現在の継続日数を計算"""
        conn = self.connect_db()
        cursor = conn.cursor()
        
        try:
            # 全ての日付を取得してソート
            cursor.execute("""
                SELECT DISTINCT DATE(timestamp, 'unixepoch', 'localtime') as activity_date
                FROM game_lines
                ORDER BY activity_date DESC
            """)
            
            dates = [row[0] for row in cursor.fetchall()]
            
            if not dates:
                return 0
            
            # 今日の日付
            today = datetime.now().strftime('%Y-%m-%d')
            
            # 最新の活動日が今日または昨日でない場合、ストリークは0
            if dates[0] != today and dates[0] != (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'):
                return 0
            
            # 連続日数をカウント
            streak = 1
            for i in range(len(dates) - 1):
                current = datetime.strptime(dates[i], '%Y-%m-%d')
                next_date = datetime.strptime(dates[i + 1], '%Y-%m-%d')
                
                # 1日の差があるかチェック
                if (current - next_date).days == 1:
                    streak += 1
                else:
                    break
            
            return streak
            
        except sqlite3.OperationalError as e:
            print(f"ストリーク計算エラー: {e}")
            return 0
        finally:
            conn.close()
    
    def create_activity_heatmap_image(self, days=30):
        """過去N日間のアクティビティヒートマップ画像を生成"""
        conn = self.connect_db()
        cursor = conn.cursor()
        
        # 過去N日間の各日の文字数を取得
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
        
        # 日本語フォントの設定（システムにインストールされているフォントを使用）
        try:
            # Windows/Mac/Linuxの日本語フォントを自動検出
            japanese_fonts = [
                'Yu Gothic',  # Windows
                'Hiragino Sans',  # Mac
                'Noto Sans CJK JP',  # Linux
                'MS Gothic',  # Windows
                'AppleGothic',  # Mac
            ]
            
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
            pass  # フォントが見つからなくても続行
        
        # グラフ作成
        fig, ax = plt.subplots(figsize=(12, 4), facecolor='#2b2d31')
        ax.set_facecolor('#2b2d31')
        
        # 棒グラフとして表示
        colors = ['#5865f2' if count > 0 else '#404249' for count in char_counts]
        bars = ax.bar(dates, char_counts, color=colors, width=0.8, edgecolor='none')
        
        # 軸の設定
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
        
        # ラベルとタイトル
        ax.set_xlabel('Date', color='#b5bac1', fontsize=10)
        ax.set_ylabel('Characters', color='#b5bac1', fontsize=10)
        ax.set_title('Activity Heatmap - Past 30 Days', color='#ffffff', fontsize=14, pad=15)
        
        # グリッド
        ax.grid(True, alpha=0.1, color='#ffffff', linestyle='-', linewidth=0.5)
        ax.set_axisbelow(True)
        
        # 軸の色を変更
        ax.spines['bottom'].set_color('#404249')
        ax.spines['top'].set_color('#404249')
        ax.spines['left'].set_color('#404249')
        ax.spines['right'].set_color('#404249')
        ax.tick_params(colors='#b5bac1', labelsize=8)
        
        # X軸のラベルを回転
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
        
        # レイアウト調整
        plt.tight_layout()
        
        # 画像をバイトストリームに保存
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, facecolor='#2b2d31', edgecolor='none')
        buf.seek(0)
        plt.close()
        
        return buf
    
    def create_simple_chart(self, weekly_data):
        """シンプルなテキストグラフを作成"""
        if not weekly_data:
            return "```\n過去7日間のデータがありません\n```"
        
        max_count = max([count for _, count in weekly_data]) if weekly_data else 0
        chart = "```\n📈 過去7日間の採掘数\n\n"
        
        for date, count in weekly_data:
            if max_count > 0:
                bar_length = int((count / max_count * 20))
            else:
                bar_length = 0
            bar = "█" * bar_length
            chart += f"{date}: {bar} {count}\n"
        
        chart += "```"
        return chart
    
    def format_report(self, stats, streak):
        """レポートをフォーマット"""
        today = datetime.now().strftime('%B %d, %Y')
        weekday = datetime.now().strftime('%A')
        
        # 基本統計のEmbed
        embed = {
            "title": f"🎮 GSM Daily Report",
            "description": f"**{today} ({weekday})**",
            "color": 5814783,  # 青色
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
        
        # 今日プレイしたゲームの詳細を追加
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
        """Discordに送信"""
        # マルチパートリクエストで画像とEmbedを送信
        files = {
            'file': ('heatmap.png', heatmap_image, 'image/png')
        }
        
        # Embedに画像を添付
        embed['image'] = {'url': 'attachment://heatmap.png'}
        
        payload = {
            'embeds': [embed]
        }
        
        # payload_jsonとして送信
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
                print("✅ レポートを送信しました")
                return True
            else:
                print(f"❌ 送信失敗: {response.status_code}")
                print(response.text)
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"❌ 送信エラー: {e}")
            return False
    
    def generate_and_send_report(self, force=False):
        """レポートを生成して送信"""
        today = datetime.now().strftime('%Y-%m-%d')
        last_report = self.get_last_report_date()
        
        # 同じ日に複数回実行されないようにチェック（forceオプションで上書き可能）
        if not force and last_report == today:
            print("ℹ️  本日は既に報告済みです（--force で強制実行可能）")
            return
        
        print("📊 レポートを生成中...")
        stats = self.get_today_stats()
        streak = self.get_activity_streak()
        
        print("📈 ヒートマップ画像を生成中...")
        heatmap_image = self.create_activity_heatmap_image()
        
        embed = self.format_report(stats, streak)
        
        if self.send_to_discord(embed, heatmap_image):
            self.save_report_date(today)
            print("✅ 報告完了！")
            print(f"   - プレイ時間: {stats['play_time_hours']:.1f}時間")
            print(f"   - 文字数: {stats['total_chars']:,}字")
            print(f"   - 継続日数: {streak}日")
    
    def list_tables(self):
        """データベース内のテーブル一覧を表示（デバッグ用）"""
        conn = self.connect_db()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        conn.close()
        
        print("\n📋 データベース内のテーブル:")
        for table in tables:
            print(f"  - {table[0]}")
        
        return [t[0] for t in tables]
    
    def show_sample_data(self):
        """サンプルデータを表示（デバッグ用）"""
        conn = self.connect_db()
        cursor = conn.cursor()
        
        print("\n📝 最新の文章データ（5件）:")
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
            print(f"  エラー: {e}")
        
        conn.close()


def find_gsm_db():
    """GSMデータベースを自動検出"""
    if os.name == 'nt':  # Windows
        appdata = os.getenv('APPDATA')
        db_path = os.path.join(appdata, 'GameSentenceMiner', 'gsm.db')
    else:  # macOS/Linux
        config_dir = os.path.expanduser('~/.config')
        db_path = os.path.join(config_dir, 'GameSentenceMiner', 'gsm.db')
    
    if os.path.exists(db_path):
        print(f"✅ GSMデータベースを検出: {db_path}")
        return db_path
    else:
        print(f"⚠️  GSMデータベースが見つかりません: {db_path}")
        return None


def create_config_file():
    """設定ファイルを作成"""
    config = {
        "db_path": "",
        "webhook_url": ""
    }
    
    print("\n=== GSM Reporter 初期設定 ===\n")
    
    # データベースパスの検出
    auto_db = find_gsm_db()
    if auto_db:
        use_auto = input(f"検出されたパスを使用しますか？ (Y/n): ").strip().lower()
        if use_auto != 'n':
            config["db_path"] = auto_db
    
    if not config["db_path"]:
        config["db_path"] = input("GSMデータベースのパスを入力: ").strip()
    
    # Webhook URL
    print("\nDiscord Webhookの作成方法:")
    print("1. Discordチャンネルの設定を開く")
    print("2. 「連携サービス」→「ウェブフック」")
    print("3. 「新しいウェブフック」→名前を設定→URLをコピー\n")
    
    config["webhook_url"] = input("Discord Webhook URLを入力: ").strip()
    
    # 設定ファイルに保存
    with open("gsm_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    print("\n✅ 設定ファイルを作成しました: gsm_config.json")
    return config


def load_config():
    """設定ファイルを読み込む"""
    if os.path.exists("gsm_config.json"):
        with open("gsm_config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# メイン実行部分
if __name__ == "__main__":
    import sys
    
    # コマンドライン引数の処理
    force = "--force" in sys.argv
    debug = "--debug" in sys.argv
    setup = "--setup" in sys.argv
    yesterday = "--yesterday" in sys.argv
    check_missing = "--check-missing" in sys.argv
    
    # GitHub Actions環境の検出
    is_github_actions = os.getenv('GITHUB_ACTIONS') == 'true'
    
    if is_github_actions:
        # GitHub Actions環境での実行
        print("🔧 Running in GitHub Actions mode")
        
        db_path = os.path.join(os.getcwd(), 'gsm.db')
        webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
        
        if not os.path.exists(db_path):
            print(f"❌ Database file not found: {db_path}")
            sys.exit(1)
        
        if not webhook_url:
            print("❌ DISCORD_WEBHOOK_URL environment variable not set")
            sys.exit(1)
        
        reporter = GSMReporter(db_path, webhook_url)
        
        # 前日（1日前）のレポートを生成
        reporter.generate_and_send_report(force=True, days_ago=1)
        
    else:
        # ローカル環境での実行
        if setup or not os.path.exists("gsm_config.json"):
            config = create_config_file()
        else:
            config = load_config()
        
        if not config or not config.get("db_path") or not config.get("webhook_url"):
            print("❌ Configuration incomplete. Run with --setup to reconfigure.")
            sys.exit(1)
        
        reporter = GSMReporter(config["db_path"], config["webhook_url"])
        
        if debug:
            reporter.list_tables()
            reporter.show_sample_data()
            print("\n" + "="*50)
        
        # 未送信レポートのチェック
        if check_missing:
            reporter.generate_and_send_report(check_missing=True)
        # 昨日のレポート
        elif yesterday:
            reporter.generate_and_send_report(force=force, days_ago=1)
        # 通常実行（今日のレポート）
        else:
            reporter.generate_and_send_report(force=force)
```

---

## 🎯 完成後の動作

### シナリオ1: PCが1amに起動している
```
1:00 AM → タスクスケジューラ実行
         → 前日のレポート送信 ✅
         → last_report_2024-01-15.txt 作成
```

### シナリオ2: PCが1amにオフ、朝10amに起動
```
10:00 AM → PC起動
10:05 AM → スタートアップスクリプト実行
         → 昨日(1/15)のレポート未送信を検出
         → 前日のレポート送信 ✅
         → last_report_2024-01-15.txt 作成
```

### シナリオ3: 3日間PCを起動していない
```
4日目に起動
起動5分後 → スタートアップスクリプト実行
          → 過去7日分をチェック
          → 1/15, 1/16, 1/17 が未送信
          → 3つのレポートを順次送信 ✅✅✅
def check_and_send_missing_reports(self, max_days_back=7):
    """
    過去N日分のレポートをチェックし、未送信があれば送信
    
    Args:
        max_days_back: 何日前まで遡ってチェックするか
    """
    print("🔍 Checking for missing reports...")
    
    reports_sent = []
    today = datetime.now()
    
    for days_ago in range(1, max_days_back + 1):
        target_date = today - timedelta(days=days_ago)
        date_str = target_date.strftime('%Y-%m-%d')
        
        # そのレポートが送信済みかチェック
        report_file = f"last_report_{date_str}.txt"
        
        if os.path.exists(report_file):
            print(f"  ✅ {date_str}: Already sent")
            continue
        
        # 未送信の場合、そのデータがあるかチェック
        stats = self.get_today_stats(days_ago=days_ago)
        
        if stats['total_chars'] == 0 and stats['lines_mined'] == 0:
            print(f"  ⚪ {date_str}: No data (skipped)")
            continue
        
        # データがあるのに未送信 → 送信
        print(f"  📤 {date_str}: Sending missing report...")
        
        try:
            streak = self.get_activity_streak()
            heatmap_image = self.create_activity_heatmap_image()
            embed = self.format_report(stats, streak, days_ago=days_ago)
            
            if self.send_to_discord(embed, heatmap_image):
                # 送信記録を保存
                with open(report_file, 'w') as f:
                    f.write(datetime.now().isoformat())
                reports_sent.append(date_str)
                print(f"  ✅ {date_str}: Sent successfully!")
            else:
                print(f"  ❌ {date_str}: Failed to send")
                
        except Exception as e:
            print(f"  ❌ {date_str}: Error - {e}")
    
    if reports_sent:
        print(f"\n✨ Sent {len(reports_sent)} missing report(s): {', '.join(reports_sent)}")
    else:
        print("\n✅ All reports are up to date!")
    
    return len(reports_sent)


def save_report_date_with_date(self, date_str):
    """特定の日付のレポート送信を記録"""
    report_file = f"last_report_{date_str}.txt"
    with open(report_file, 'w') as f:
        f.write(datetime.now().isoformat())


def generate_and_send_report(self, force=False, days_ago=0, check_missing=False):
    """レポートを生成して送信
    
    Args:
        force: 強制実行
        days_ago: 何日前のデータか（0=今日、1=昨日）
        check_missing: 過去の未送信レポートもチェックするか
    """
    # 過去の未送信レポートをチェック
    if check_missing:
        self.check_and_send_missing_reports(max_days_back=7)
        return
    
    target_date = datetime.now() - timedelta(days=days_ago)
    date_str = target_date.strftime('%Y-%m-%d')
    report_file = f"last_report_{date_str}.txt"
    
    # その日のレポートが送信済みかチェック
    if not force and os.path.exists(report_file):
        print(f"ℹ️  Report for {date_str} already sent")
        return
    
    print(f"📊 Generating report for {date_str}...")
    stats = self.get_today_stats(days_ago=days_ago)
    
    # データがない場合はスキップ
    if stats['total_chars'] == 0 and stats['lines_mined'] == 0:
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
