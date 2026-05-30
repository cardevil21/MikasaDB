import os
import re
import json
import sqlite3
import time
import requests
from datetime import datetime, timedelta
from collections import defaultdict

# ==================== CONFIG ====================
TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
BOT_USERNAME = os.environ.get("BOT_USERNAME", "MikasaAckermanBot")

# ==================== CONSTANTS ====================
ALLOWED_DOMAINS = ['animethic.in', 'animethic.xyz']
WARN_LIMIT = 3
MUTE_MINUTES = 10
FLOOD_LIMIT = 5
FLOOD_WINDOW = 10
RAID_LIMIT = 10
RAID_WINDOW = 60

SCAM_PATTERNS = [
    'free nitro', 'giveaway', 'airdrop', 'click here', 'claim reward',
    'verify account', 'earn money', 'free gift', 'crypto free',
    'double your', 'lottery win', 'won prize', 'limited offer',
    'exclusive deal', 'act now', 'passive income', 'get rich'
]

RULES_TEXT = """
📜 <b>ANIMETHIC GROUP RULES</b>
━━━━━━━━━━━━━━━━━━━━

1️⃣ <b>Anime Related Discussion Only</b>
   Keep conversations focused on anime/manga

2️⃣ <b>No Promotion or Scam</b>
   Self-promotion, MLM, scams = instant action

3️⃣ <b>No Other Channel/Website Promotion</b>
   Don't mention or promote other channels

4️⃣ <b>Only animethic.in & animethic.xyz Links</b>
   All other links = auto-delete + warning
   ⚠️ 3 warnings = 10 minutes mute

5️⃣ <b>No Hate Speech or Harassment</b>
   Zero tolerance for toxicity

6️⃣ <b>Be Friendly & Respectful</b>
   Help newcomers, maintain positive vibes

━━━━━━━━━━━━━━━━━━━━
🔗 <b>Official:</b> animethic.in | animethic.xyz
"""

WELCOME_TEXT = """
🎌 <b>Welcome to Animethic Community, {name}!</b>

📜 Please read the /rules
🔗 Only animethic.in/.xyz links allowed
💬 Enjoy anime discussions!

<b>Useful Commands:</b>
/help - Bot commands
/report - Report a message
"""

# ==================== DATABASE ====================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('mikasa.db', check_same_thread=False)
        self.c = self.conn.cursor()
        self._init_tables()
    
    def _init_tables(self):
        self.c.executescript('''
            CREATE TABLE IF NOT EXISTS users(
                user_id INT, chat_id INT, 
                warns INT DEFAULT 0, 
                reputation INT DEFAULT 0,
                messages INT DEFAULT 0,
                last_active TEXT,
                PRIMARY KEY(user_id, chat_id)
            );
            
            CREATE TABLE IF NOT EXISTS warns_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INT, chat_id INT, 
                warned_by INT, reason TEXT, 
                timestamp TEXT
            );
            
            CREATE TABLE IF NOT EXISTS mutes(
                user_id INT, chat_id INT, 
                until TEXT, muted_by INT,
                PRIMARY KEY(user_id, chat_id)
            );
            
            CREATE TABLE IF NOT EXISTS bans(
                user_id INT, chat_id INT,
                banned_by INT, reason TEXT,
                timestamp TEXT,
                PRIMARY KEY(user_id, chat_id)
            );
            
            CREATE TABLE IF NOT EXISTS faq(
                keyword TEXT UNIQUE, 
                response TEXT, 
                created_by INT,
                created_at TEXT
            );
            
            CREATE TABLE IF NOT EXISTS reports(
                date TEXT PRIMARY KEY,
                warns INT DEFAULT 0,
                mutes INT DEFAULT 0,
                kicks INT DEFAULT 0,
                bans INT DEFAULT 0,
                spam_deleted INT DEFAULT 0,
                joins INT DEFAULT 0,
                leaves INT DEFAULT 0,
                messages INT DEFAULT 0
            );
            
            CREATE TABLE IF NOT EXISTS settings(
                chat_id INT, key TEXT, 
                value TEXT,
                PRIMARY KEY(chat_id, key)
            );
            
            CREATE TABLE IF NOT EXISTS action_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INT, admin_id INT, 
                action TEXT, target_id INT,
                details TEXT, timestamp TEXT
            );
        ''')
        self.conn.commit()
    
    def get_setting(self, chat_id, key, default='on'):
        r = self.c.execute('SELECT value FROM settings WHERE chat_id=? AND key=?', 
                          (chat_id, key)).fetchone()
        return r[0] if r else default
    
    def set_setting(self, chat_id, key, value):
        self.c.execute('INSERT OR REPLACE INTO settings VALUES(?,?,?)', 
                      (chat_id, key, value))
        self.conn.commit()
    
    def get_user(self, user_id, chat_id):
        r = self.c.execute('SELECT * FROM users WHERE user_id=? AND chat_id=?', 
                          (user_id, chat_id)).fetchone()
        return r
    
    def add_warn(self, user_id, chat_id, warned_by, reason=''):
        self.c.execute('''INSERT INTO users(user_id,chat_id,warns) 
                         VALUES(?,?,1) ON CONFLICT(user_id,chat_id) 
                         DO UPDATE SET warns=warns+1''', (user_id, chat_id))
        self.c.execute('INSERT INTO warns_log(user_id,chat_id,warned_by,reason,timestamp) 
                       VALUES(?,?,?,?,datetime("now"))', 
                       (user_id, chat_id, warned_by, reason))
        self.conn.commit()
        return self.get_warns(user_id, chat_id)
    
    def get_warns(self, user_id, chat_id):
        r = self.c.execute('SELECT warns FROM users WHERE user_id=? AND chat_id=?', 
                          (user_id, chat_id)).fetchone()
        return r[0] if r else 0
    
    def reset_warns(self, user_id, chat_id):
        self.c.execute('UPDATE users SET warns=0 WHERE user_id=? AND chat_id=?', 
                      (user_id, chat_id))
        self.c.execute('DELETE FROM warns_log WHERE user_id=? AND chat_id=?', 
                      (user_id, chat_id))
        self.conn.commit()
    
    def mute_user(self, user_id, chat_id, until, muted_by):
        self.c.execute('INSERT OR REPLACE INTO mutes VALUES(?,?,?,?)', 
                      (user_id, chat_id, until, muted_by))
        self.conn.commit()
    
    def unmute_user(self, user_id, chat_id):
        self.c.execute('DELETE FROM mutes WHERE user_id=? AND chat_id=?', 
                      (user_id, chat_id))
        self.conn.commit()
    
    def is_muted(self, user_id, chat_id):
        r = self.c.execute('SELECT until FROM mutes WHERE user_id=? AND chat_id=?', 
                          (user_id, chat_id)).fetchone()
        if r:
            until = datetime.fromisoformat(r[0])
            if until > datetime.now():
                return until
        return None
    
    def add_report(self, field, count=1):
        self.c.execute(f'''INSERT INTO reports(date,{field}) 
                         VALUES(date("now"),?) ON CONFLICT(date) 
                         DO UPDATE SET {field}={field}+?''', (count, count))
        self.conn.commit()
    
    def get_report(self, date=None):
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
        r = self.c.execute('SELECT * FROM reports WHERE date=?', (date,)).fetchone()
        return r
    
    def get_faq(self, keyword):
        r = self.c.execute('SELECT response FROM faq WHERE keyword=?', 
                          (keyword.lower(),)).fetchone()
        return r[0] if r else None
    
    def add_faq(self, keyword, response, user_id):
        self.c.execute('INSERT OR REPLACE INTO faq VALUES(?,?,?,datetime("now"))', 
                      (keyword.lower(), response, user_id))
        self.conn.commit()
    
    def delete_faq(self, keyword):
        self.c.execute('DELETE FROM faq WHERE keyword=?', (keyword.lower(),))
        self.conn.commit()
    
    def get_all_faqs(self):
        return self.c.execute('SELECT * FROM faq ORDER BY keyword').fetchall()
    
    def log_action(self, chat_id, admin_id, action, target_id, details=''):
        self.c.execute('''INSERT INTO action_log(chat_id,admin_id,action,target_id,details,timestamp) 
                         VALUES(?,?,?,?,?,datetime("now"))''', 
                      (chat_id, admin_id, action, target_id, details))
        self.conn.commit()
    
    def get_top_users(self, chat_id, limit=10):
        return self.c.execute('''SELECT user_id, reputation, messages 
                                FROM users WHERE chat_id=? AND reputation>0 
                                ORDER BY reputation DESC LIMIT ?''', 
                             (chat_id, limit)).fetchall()
    
    def get_warn_history(self, user_id, chat_id):
        return self.c.execute('''SELECT * FROM warns_log 
                                WHERE user_id=? AND chat_id=? 
                                ORDER BY timestamp DESC LIMIT 5''', 
                             (user_id, chat_id)).fetchall()

db = Database()

# ==================== TELEGRAM API ====================
class TelegramAPI:
    @staticmethod
    def call(method, params=None):
        if params is None:
            params = {}
        try:
            r = requests.post(f'https://api.telegram.org/bot{TOKEN}/{method}', 
                            json=params, timeout=10)
            return r.json()
        except:
            return {'ok': False}
    
    @staticmethod
    def send(chat_id, text, reply_markup=None, parse_mode='HTML'):
        params = {'chat_id': chat_id, 'text': text, 'parse_mode': parse_mode}
        if reply_markup:
            params['reply_markup'] = reply_markup
        return TelegramAPI.call('sendMessage', params)
    
    @staticmethod
    def edit(chat_id, msg_id, text, reply_markup=None):
        params = {'chat_id': chat_id, 'message_id': msg_id, 'text': text, 'parse_mode': 'HTML'}
        if reply_markup:
            params['reply_markup'] = reply_markup
        return TelegramAPI.call('editMessageText', params)
    
    @staticmethod
    def delete(chat_id, msg_id):
        try:
            TelegramAPI.call('deleteMessage', {'chat_id': chat_id, 'message_id': msg_id})
        except:
            pass
    
    @staticmethod
    def restrict(chat_id, user_id, until=None):
        params = {
            'chat_id': chat_id,
            'user_id': user_id,
            'permissions': {'can_send_messages': False}
        }
        if until:
            params['until_date'] = until
        return TelegramAPI.call('restrictChatMember', params)
    
    @staticmethod
    def unrestrict(chat_id, user_id):
        perms = {
            'can_send_messages': True, 'can_send_media': True,
            'can_send_polls': True, 'can_send_other': True,
            'can_add_web_page_previews': True, 'can_invite_users': True
        }
        return TelegramAPI.call('restrictChatMember', 
                              {'chat_id': chat_id, 'user_id': user_id, 'permissions': perms})
    
    @staticmethod
    def kick(chat_id, user_id):
        TelegramAPI.call('banChatMember', {'chat_id': chat_id, 'user_id': user_id})
        TelegramAPI.call('unbanChatMember', {'chat_id': chat_id, 'user_id': user_id})
    
    @staticmethod
    def ban(chat_id, user_id):
        return TelegramAPI.call('banChatMember', {'chat_id': chat_id, 'user_id': user_id})
    
    @staticmethod
    def unban(chat_id, user_id):
        return TelegramAPI.call('unbanChatMember', {'chat_id': chat_id, 'user_id': user_id})
    
    @staticmethod
    def is_admin(chat_id, user_id):
        r = TelegramAPI.call('getChatMember', {'chat_id': chat_id, 'user_id': user_id})
        s = r.get('result', {}).get('status', '')
        return s in ['creator', 'administrator']
    
    @staticmethod
    def answer_callback(query_id, text='', show_alert=False):
        return TelegramAPI.call('answerCallbackQuery', {
            'callback_query_id': query_id, 'text': text, 'show_alert': show_alert
        })

api = TelegramAPI

# ==================== FLOOD & RAID CONTROL ====================
class FloodControl:
    def __init__(self):
        self.data = defaultdict(list)
        self.joins = defaultdict(list)
    
    def is_flooding(self, user_id):
        now = time.time()
        self.data[user_id].append(now)
        self.data[user_id] = [t for t in self.data[user_id] if now - t < FLOOD_WINDOW]
        return len(self.data[user_id]) > FLOOD_LIMIT
    
    def is_raid(self, chat_id):
        now = time.time()
        self.joins[chat_id].append(now)
        self.joins[chat_id] = [t for t in self.joins[chat_id] if now - t < RAID_WINDOW]
        return len(self.joins[chat_id]) >= RAID_LIMIT

fc = FloodControl()

# ==================== DETECTION ====================
def is_scam(text):
    if not text: return False
    t = text.lower()
    for p in SCAM_PATTERNS:
        if p in t:
            return True
    return False

def extract_links(text):
    if not text: return []
    return re.findall(r'https?://[^\s]+', text)

def is_allowed_link(url):
    try:
        domain = url.replace('https://', '').replace('http://', '').split('/')[0].lower()
        return domain in ALLOWED_DOMAINS
    except:
        return False

# ==================== KEYBOARDS ====================
def admin_keyboard():
    return {
        'inline_keyboard': [
            [{'text': '📊 Reports', 'callback_data': 'admin_reports'},
             {'text': '📋 FAQs', 'callback_data': 'admin_faqs'}],
            [{'text': '⚙️ Settings', 'callback_data': 'admin_settings'},
             {'text': '📜 Rules', 'callback_data': 'admin_rules'}],
            [{'text': '📈 Top Members', 'callback_data': 'admin_top'},
             {'text': '🔄 Refresh', 'callback_data': 'admin_refresh'}]
        ]
    }

def settings_keyboard(chat_id):
    anti_spam = db.get_setting(chat_id, 'anti_spam')
    anti_flood = db.get_setting(chat_id, 'anti_flood')
    link_filter = db.get_setting(chat_id, 'link_filter')
    welcome = db.get_setting(chat_id, 'welcome')
    
    return {
        'inline_keyboard': [
            [{'text': f'🛡️ Anti-Spam: {anti_spam.upper()}', 
              'callback_data': f'toggle_anti_spam_{chat_id}'}],
            [{'text': f'🌊 Anti-Flood: {anti_flood.upper()}', 
              'callback_data': f'toggle_anti_flood_{chat_id}'}],
            [{'text': f'🔗 Link Filter: {link_filter.upper()}', 
              'callback_data': f'toggle_link_filter_{chat_id}'}],
            [{'text': f'👋 Welcome: {welcome.upper()}', 
              'callback_data': f'toggle_welcome_{chat_id}'}],
            [{'text': '🔙 Back', 'callback_data': 'admin_refresh'}]
        ]
    }

def faq_keyboard(page=0):
    faqs = db.get_all_faqs()
    total = len(faqs)
    per_page = 5
    start = page * per_page
    end = start + per_page
    current_faqs = faqs[start:end]
    
    keyboard = []
    for f in current_faqs:
        keyboard.append([{'text': f'🔹 {f[0]}', 'callback_data': f'faq_view_{f[0]}'}])
    
    nav = []
    if page > 0:
        nav.append({'text': '◀️ Prev', 'callback_data': f'faq_page_{page-1}'})
    nav.append({'text': f'{page+1}/{(total//per_page)+1}', 'callback_data': 'noop'})
    if end < total:
        nav.append({'text': '▶️ Next', 'callback_data': f'faq_page_{page+1}'})
    if nav:
        keyboard.append(nav)
    
    keyboard.append([{'text': '🔙 Back', 'callback_data': 'admin_refresh'}])
    return {'inline_keyboard': keyboard}

# ==================== HANDLERS ====================
def handle_private(msg):
    user_id = msg['from']['id']
    chat_id = msg['chat']['id']
    
    if user_id != ADMIN_ID:
        api.send(chat_id, '⛔ This bot is managed by Animethic Admin only.')
        return
    
    text = msg.get('text', '')
    args = text.split()
    cmd = args[0].lower()
    
    if cmd in ['/start', '/admin', '/panel']:
        api.send(chat_id, 
                '🎌 <b>MIKASA ACKERMAN</b> - Admin Control Panel\n\n'
                'Welcome, Commander!\n\n'
                'Use the buttons below to manage your bot:',
                reply_markup=admin_keyboard())
    
    elif cmd == '/faq_add' and len(args) >= 3:
        keyword = args[1]
        response = ' '.join(args[2:])
        db.add_faq(keyword, response, user_id)
        api.send(chat_id, f'✅ FAQ "{keyword}" added successfully!')
    
    elif cmd == '/faq_remove' and len(args) >= 2:
        db.delete_faq(args[1])
        api.send(chat_id, f'✅ FAQ "{args[1]}" removed!')
    
    elif cmd == '/report':
        date = args[1] if len(args) > 1 else None
        report = db.get_report(date)
        if report:
            api.send(chat_id, 
                    f'📊 <b>Report - {report[0]}</b>\n'
                    f'━━━━━━━━━━━━━━━━\n'
                    f'⚠️ Warnings: {report[1]}\n'
                    f'🔇 Mutes: {report[2]}\n'
                    f'👢 Kicks: {report[3]}\n'
                    f'🚫 Bans: {report[4]}\n'
                    f'🗑️ Spam Deleted: {report[5]}\n'
                    f'👥 Joins: {report[6]}\n'
                    f'🚶 Leaves: {report[7]}\n'
                    f'💬 Messages: {report[8]}')
        else:
            api.send(chat_id, '📊 No data for this date.')

def handle_group_command(msg):
    chat_id = msg['chat']['id']
    user_id = msg['from']['id']
    text = msg.get('text', '')
    args = text.split()
    cmd = args[0].lower().split('@')[0]
    reply = msg.get('reply_to_message')
    
    is_admin = api.is_admin(chat_id, user_id)
    
    if cmd == '/start':
        api.send(chat_id, '🎌 <b>Mikasa Ackerman</b> is active!\nUse /help for commands.')
    
    elif cmd == '/help':
        help_text = """
🎌 <b>MIKASA ACKERMAN - HELP</b>

<b>📋 Everyone:</b>
/help - This menu
/rules - Group rules
/report - Report a message

<b>👑 Admins:</b>
/warn - Warn user (reply)
/resetwarn - Reset warnings
/warnings - Check warnings
/mute [mins] - Mute user
/unmute - Unmute user
/kick - Kick user
/ban - Ban user
/purge [n] - Delete messages
/settings - Bot settings
/top - Top members
"""
        api.send(chat_id, help_text)
    
    elif cmd == '/rules':
        api.send(chat_id, RULES_TEXT)
    
    elif cmd == '/settings' and is_admin:
        api.send(chat_id, '⚙️ <b>Group Settings</b>\n\nTap to toggle:',
                reply_markup=settings_keyboard(chat_id))
    
    elif cmd == '/top':
        top = db.get_top_users(chat_id)
        if top:
            text = '🏆 <b>TOP MEMBERS</b>\n\n'
            for i, (uid, rep, msgs) in enumerate(top, 1):
                text += f'{i}. User {uid}: ⭐{rep} | 💬{msgs} msgs\n'
            api.send(chat_id, text)
        else:
            api.send(chat_id, 'No data yet!')
    
    # Moderation Commands
    elif cmd == '/warn' and is_admin and reply:
        target = reply['from']
        count = db.add_warn(target['id'], chat_id, user_id, 'Manual warn')
        db.log_action(chat_id, user_id, 'warn', target['id'], f'Manual warn')
        db.add_report('warns')
        
        msg_text = f'⚠️ {target["first_name"]} warned!\nWarnings: {count}/{WARN_LIMIT}'
        
        if count >= WARN_LIMIT:
            until = int(time.time()) + MUTE_MINUTES * 60
            api.restrict(chat_id, target['id'], until)
            db.mute_user(target['id'], chat_id, 
                        (datetime.now() + timedelta(minutes=MUTE_MINUTES)).isoformat(), 
                        user_id)
            db.add_report('mutes')
            msg_text += f'\n\n🔇 Auto-muted for {MUTE_MINUTES} minutes!'
            db.log_action(chat_id, user_id, 'auto_mute', target['id'], 
                         f'Auto mute after {count} warns')
        
        api.send(chat_id, msg_text)
    
    elif cmd == '/resetwarn' and is_admin and reply:
        db.reset_warns(reply['from']['id'], chat_id)
        api.send(chat_id, f'✅ Warnings reset for {reply["from"]["first_name"]}')
        db.log_action(chat_id, user_id, 'reset_warns', reply['from']['id'])
    
    elif cmd == '/warnings' and is_admin and reply:
        count = db.get_warns(reply['from']['id'], chat_id)
        history = db.get_warn_history(reply['from']['id'], chat_id)
        text = f'⚠️ {reply["from"]["first_name"]}: {count}/{WARN_LIMIT} warnings\n\n'
        if history:
            text += '<b>Recent:</b>\n'
            for h in history[:3]:
                text += f'• {h[3]} - {h[5][:16]}\n'
        api.send(chat_id, text)
    
    elif cmd == '/mute' and is_admin and reply:
        mins = int(args[1]) if len(args) > 1 and args[1].isdigit() else MUTE_MINUTES
        until = int(time.time()) + mins * 60
        api.restrict(chat_id, reply['from']['id'], until)
        db.mute_user(reply['from']['id'], chat_id,
                    (datetime.now() + timedelta(minutes=mins)).isoformat(),
                    user_id)
        db.add_report('mutes')
        db.log_action(chat_id, user_id, 'mute', reply['from']['id'], f'{mins} min')
        api.send(chat_id, f'🔇 {reply["from"]["first_name"]} muted for {mins} minutes.')
    
    elif cmd == '/unmute' and is_admin and reply:
        api.unrestrict(chat_id, reply['from']['id'])
        db.unmute_user(reply['from']['id'], chat_id)
        db.log_action(chat_id, user_id, 'unmute', reply['from']['id'])
        api.send(chat_id, f'✅ {reply["from"]["first_name"]} unmuted.')
    
    elif cmd == '/kick' and is_admin and reply:
        api.kick(chat_id, reply['from']['id'])
        db.add_report('kicks')
        db.log_action(chat_id, user_id, 'kick', reply['from']['id'])
        api.send(chat_id, f'👢 {reply["from"]["first_name"]} kicked.')
    
    elif cmd == '/ban' and is_admin and reply:
        api.ban(chat_id, reply['from']['id'])
        db.add_report('bans')
        db.log_action(chat_id, user_id, 'ban', reply['from']['id'])
        api.send(chat_id, f'🚫 {reply["from"]["first_name"]} banned.')
    
    elif cmd == '/purge' and is_admin and reply:
        count = min(int(args[1]) if len(args) > 1 and args[1].isdigit() else 10, 100)
        api.delete(chat_id, msg['message_id'])
        for i in range(count):
            api.delete(chat_id, reply['message_id'] + i)
        db.log_action(chat_id, user_id, 'purge', 0, f'{count} msgs')
    
    elif cmd == '/report' and reply:
        api.send(ADMIN_ID, 
                f'📩 <b>Report</b>\n'
                f'From: {user_id}\n'
                f'Chat: {chat_id}\n'
                f'Message: {reply.get("text","Media")[:200]}')
        api.send(chat_id, '✅ Reported to admin.')

def handle_group_message(msg):
    chat_id = msg['chat']['id']
    user_id = msg['from']['id']
    text = msg.get('text', msg.get('caption', ''))
    
    # Skip admins
    if api.is_admin(chat_id, user_id):
        return
    
    # Check mute
    muted_until = db.is_muted(user_id, chat_id)
    if muted_until:
        api.delete(chat_id, msg['message_id'])
        return
    
    # Anti-Flood
    if db.get_setting(chat_id, 'anti_flood') != 'off':
        if fc.is_flooding(user_id):
            api.delete(chat_id, msg['message_id'])
            until = int(time.time()) + 300
            api.restrict(chat_id, user_id, until)
            db.mute_user(user_id, chat_id, 
                        (datetime.now() + timedelta(minutes=5)).isoformat(), 
                        0)
            db.add_report('spam_deleted')
            api.send(chat_id, f'🌊 {msg["from"]["first_name"]} auto-muted (flood).')
            return
    
    # Anti-Scam
    if db.get_setting(chat_id, 'anti_spam') != 'off':
        if is_scam(text):
            api.delete(chat_id, msg['message_id'])
            count = db.add_warn(user_id, chat_id, 0, 'Scam detected')
            db.add_report('warns')
            db.add_report('spam_deleted')
            
            alert = f'⚠️ {msg["from"]["first_name"]}\nScam detected!\nWarnings: {count}/{WARN_LIMIT}'
            
            if count >= WARN_LIMIT:
                until = int(time.time()) + MUTE_MINUTES * 60
                api.restrict(chat_id, user_id, until)
                db.mute_user(user_id, chat_id,
                            (datetime.now() + timedelta(minutes=MUTE_MINUTES)).isoformat(),
                            0)
                db.add_report('mutes')
                alert += f'\n\n🔇 Muted {MUTE_MINUTES} minutes!'
                api.send(ADMIN_ID, f'⚠️ Auto-mute: {user_id} - Scam\nChat: {chat_id}')
            
            sent = api.send(chat_id, alert)
            if sent.get('ok'):
                time.sleep(8)
                api.delete(chat_id, sent['result']['message_id'])
            return
    
    # Link Filter
    if db.get_setting(chat_id, 'link_filter') != 'off':
        links = extract_links(text)
        if links:
            for link in links:
                if not is_allowed_link(link):
                    api.delete(chat_id, msg['message_id'])
                    count = db.add_warn(user_id, chat_id, 0, 'Unauthorized link')
                    db.add_report('warns')
                    db.add_report('spam_deleted')
                    
                    alert = f'🔗 {msg["from"]["first_name"]}\nOnly animethic.in/.xyz allowed!\nWarnings: {count}/{WARN_LIMIT}'
                    
                    if count >= WARN_LIMIT:
                        until = int(time.time()) + MUTE_MINUTES * 60
                        api.restrict(chat_id, user_id, until)
                        db.mute_user(user_id, chat_id,
                                    (datetime.now() + timedelta(minutes=MUTE_MINUTES)).isoformat(),
                                    0)
                        db.add_report('mutes')
                        alert += f'\n\n🔇 Muted {MUTE_MINUTES} minutes!'
                        api.send(ADMIN_ID, f'⚠️ Auto-mute: {user_id} - Link\nChat: {chat_id}')
                    
                    sent = api.send(chat_id, alert)
                    if sent.get('ok'):
                        time.sleep(8)
                        api.delete(chat_id, sent['result']['message_id'])
                    return
    
    # FAQ Auto-Reply
    faq_response = db.get_faq(text.strip())
    if faq_response:
        api.send(chat_id, faq_response)
    
    # Track messages
    db.add_report('messages')
    db.c.execute('''UPDATE users SET messages=messages+1, last_active=datetime("now") 
                   WHERE user_id=? AND chat_id=?''', (user_id, chat_id))
    db.conn.commit()

def handle_join(msg):
    chat_id = msg['chat']['id']
    user = msg['new_chat_member']['user']
    db.add_report('joins')
    
    if fc.is_raid(chat_id):
        api.send(ADMIN_ID, f'🚨 <b>RAID ALERT!</b>\nChat: {chat_id}')
    
    if db.get_setting(chat_id, 'welcome') != 'off':
        api.send(chat_id, WELCOME_TEXT.format(name=user['first_name']))

def handle_callback(query):
    query_id = query['id']
    data = query['data']
    msg = query.get('message', {})
    chat_id = msg.get('chat', {}).get('id', 0)
    user_id = query['from']['id']
    
    if user_id != ADMIN_ID:
        api.answer_callback(query_id, '⛔ Admin only!', True)
        return
    
    if data == 'admin_refresh':
        api.edit(chat_id, msg['message_id'],
                '🎌 <b>MIKASA ACKERMAN</b> - Admin Control Panel\n\nSelect an option:',
                reply_markup=admin_keyboard())
    
    elif data == 'admin_reports':
        report = db.get_report()
        text = f'📊 <b>Today\'s Report</b>\n━━━━━━━━\n'
        if report:
            text += f'⚠️ Warns: {report[1]}\n🔇 Mutes: {report[2]}\n'
            text += f'👢 Kicks: {report[3]}\n🚫 Bans: {report[4]}\n'
            text += f'🗑️ Spam: {report[5]}\n👥 Joins: {report[6]}\n💬 Msgs: {report[8]}'
        else:
            text += 'No data yet'
        api.edit(chat_id, msg['message_id'], text, 
                reply_markup={'inline_keyboard': [[{'text': '🔙 Back', 'callback_data': 'admin_refresh'}]]})
    
    elif data == 'admin_faqs':
        faqs = db.get_all_faqs()
        text = '📋 <b>FAQs</b>\n\n'
        if faqs:
            for f in faqs[:10]:
                text += f'🔹 <b>{f[0]}</b>: {f[1][:50]}...\n'
        else:
            text += 'No FAQs yet.\n\nAdd via: /faq_add keyword response'
        api.edit(chat_id, msg['message_id'], text,
                reply_markup={'inline_keyboard': [[{'text': '🔙 Back', 'callback_data': 'admin_refresh'}]]})
    
    elif data == 'admin_settings':
        api.edit(chat_id, msg['message_id'], 'Select a group first. Use /settings in group.')
    
    elif data == 'admin_rules':
        api.edit(chat_id, msg['message_id'], RULES_TEXT,
                reply_markup={'inline_keyboard': [[{'text': '🔙 Back', 'callback_data': 'admin_refresh'}]]})
    
    elif data == 'admin_top':
        top = db.get_top_users(chat_id)
        text = '🏆 <b>Top Members</b>\n\n'
        if top:
            for i, (uid, rep, msgs) in enumerate(top, 1):
                text += f'{i}. User {uid}: ⭐{rep} | 💬{msgs}\n'
        else:
            text += 'No data yet'
        api.edit(chat_id, msg['message_id'], text,
                reply_markup={'inline_keyboard': [[{'text': '🔙 Back', 'callback_data': 'admin_refresh'}]]})
    
    elif data.startswith('toggle_'):
        parts = data.split('_')
        feature = '_'.join(parts[1:-1])
        gid = int(parts[-1])
        current = db.get_setting(gid, feature)
        new_val = 'off' if current == 'on' else 'on'
        db.set_setting(gid, feature, new_val)
        api.answer_callback(query_id, f'✅ {feature} = {new_val}')
        api.edit(chat_id, msg['message_id'], '⚙️ <b>Group Settings</b>\n\nTap to toggle:',
                reply_markup=settings_keyboard(gid))
    
    api.answer_callback(query_id)

# ==================== MAIN ====================
def process_update(update):
    if 'message' in update:
        msg = update['message']
        chat_id = msg['chat']['id']
        user_id = msg['from']['id']
        text = msg.get('text', msg.get('caption', ''))
        
        # Private chat
        if chat_id == user_id:
            handle_private(msg)
        # Group - commands
        elif text.startswith('/'):
            handle_group_command(msg)
        # Group - messages
        else:
            handle_group_message(msg)
    
    elif 'chat_member' in update:
        cm = update['chat_member']
        if cm.get('new_chat_member', {}).get('status') == 'member':
            handle_join(cm)
        elif cm.get('old_chat_member', {}).get('status') == 'member':
            db.add_report('leaves')
    
    elif 'callback_query' in update:
        handle_callback(update['callback_query'])

def main():
    print("🎌 Mikasa Ackerman v3.0 Started!")
    print(f"Bot: @{BOT_USERNAME}")
    print(f"Admin: {ADMIN_ID}")
    print("Features: Anti-Spam | Anti-Flood | Link Filter | FAQ | Reports | User Reputation")
    print("=" * 50)
    
    offset = 0
    while True:
        try:
            r = requests.get(
                f'https://api.telegram.org/bot{TOKEN}/getUpdates',
                params={'offset': offset, 'timeout': 30},
                timeout=35
            )
            data = r.json()
            if data.get('ok'):
                for update in data['result']:
                    offset = update['update_id'] + 1
                    try:
                        process_update(update)
                    except Exception as e:
                        print(f'Update Error: {e}')
        except Exception as e:
            print(f'Connection Error: {e}')
            time.sleep(2)

if __name__ == '__main__':
    main()
