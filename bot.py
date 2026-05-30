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
        self.c.execute("CREATE TABLE IF NOT EXISTS users(user_id INT, chat_id INT, warns INT DEFAULT 0, reputation INT DEFAULT 0, messages INT DEFAULT 0, last_active TEXT, PRIMARY KEY(user_id, chat_id))")
        self.c.execute("CREATE TABLE IF NOT EXISTS warns_log(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INT, chat_id INT, warned_by INT, reason TEXT, timestamp TEXT)")
        self.c.execute("CREATE TABLE IF NOT EXISTS mutes(user_id INT, chat_id INT, until TEXT, muted_by INT, PRIMARY KEY(user_id, chat_id))")
        self.c.execute("CREATE TABLE IF NOT EXISTS bans(user_id INT, chat_id INT, banned_by INT, reason TEXT, timestamp TEXT, PRIMARY KEY(user_id, chat_id))")
        self.c.execute("CREATE TABLE IF NOT EXISTS faq(keyword TEXT UNIQUE, response TEXT, created_by INT, created_at TEXT)")
        self.c.execute("CREATE TABLE IF NOT EXISTS reports(date TEXT PRIMARY KEY, warns INT DEFAULT 0, mutes INT DEFAULT 0, kicks INT DEFAULT 0, bans INT DEFAULT 0, spam_deleted INT DEFAULT 0, joins INT DEFAULT 0, leaves INT DEFAULT 0, messages INT DEFAULT 0)")
        self.c.execute("CREATE TABLE IF NOT EXISTS settings(chat_id INT, key TEXT, value TEXT, PRIMARY KEY(chat_id, key))")
        self.c.execute("CREATE TABLE IF NOT EXISTS action_log(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INT, admin_id INT, action TEXT, target_id INT, details TEXT, timestamp TEXT)")
        self.conn.commit()
    
    def get_setting(self, chat_id, key, default='on'):
        r = self.c.execute('SELECT value FROM settings WHERE chat_id=? AND key=?', (chat_id, key)).fetchone()
        return r[0] if r else default
    
    def set_setting(self, chat_id, key, value):
        self.c.execute('INSERT OR REPLACE INTO settings VALUES(?,?,?)', (chat_id, key, value))
        self.conn.commit()
    
    def add_warn(self, user_id, chat_id, warned_by, reason=''):
        self.c.execute('INSERT INTO users(user_id,chat_id,warns) VALUES(?,?,1) ON CONFLICT(user_id,chat_id) DO UPDATE SET warns=warns+1', (user_id, chat_id))
        self.c.execute('INSERT INTO warns_log(user_id,chat_id,warned_by,reason,timestamp) VALUES(?,?,?,?,datetime("now"))', (user_id, chat_id, warned_by, reason))
        self.conn.commit()
        return self.get_warns(user_id, chat_id)
    
    def get_warns(self, user_id, chat_id):
        r = self.c.execute('SELECT warns FROM users WHERE user_id=? AND chat_id=?', (user_id, chat_id)).fetchone()
        return r[0] if r else 0
    
    def reset_warns(self, user_id, chat_id):
        self.c.execute('UPDATE users SET warns=0 WHERE user_id=? AND chat_id=?', (user_id, chat_id))
        self.conn.commit()
    
    def mute_user(self, user_id, chat_id, until, muted_by):
        self.c.execute('INSERT OR REPLACE INTO mutes VALUES(?,?,?,?)', (user_id, chat_id, until, muted_by))
        self.conn.commit()
    
    def unmute_user(self, user_id, chat_id):
        self.c.execute('DELETE FROM mutes WHERE user_id=? AND chat_id=?', (user_id, chat_id))
        self.conn.commit()
    
    def is_muted(self, user_id, chat_id):
        r = self.c.execute('SELECT until FROM mutes WHERE user_id=? AND chat_id=?', (user_id, chat_id)).fetchone()
        if r:
            until = datetime.fromisoformat(r[0])
            if until > datetime.now():
                return until
        return None
    
    def add_report(self, field, count=1):
        try:
            self.c.execute(f'INSERT INTO reports(date,{field}) VALUES(date("now"),?) ON CONFLICT(date) DO UPDATE SET {field}={field}+?', (count, count))
            self.conn.commit()
        except:
            pass
    
    def get_report(self, date=None):
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
        r = self.c.execute('SELECT * FROM reports WHERE date=?', (date,)).fetchone()
        return r
    
    def get_faq(self, keyword):
        r = self.c.execute('SELECT response FROM faq WHERE keyword=?', (keyword.lower(),)).fetchone()
        return r[0] if r else None
    
    def add_faq(self, keyword, response, user_id):
        self.c.execute('INSERT OR REPLACE INTO faq VALUES(?,?,?,datetime("now"))', (keyword.lower(), response, user_id))
        self.conn.commit()
    
    def delete_faq(self, keyword):
        self.c.execute('DELETE FROM faq WHERE keyword=?', (keyword.lower(),))
        self.conn.commit()
    
    def get_all_faqs(self):
        return self.c.execute('SELECT * FROM faq ORDER BY keyword').fetchall()
    
    def log_action(self, chat_id, admin_id, action, target_id, details=''):
        self.c.execute('INSERT INTO action_log(chat_id,admin_id,action,target_id,details,timestamp) VALUES(?,?,?,?,?,datetime("now"))', (chat_id, admin_id, action, target_id, details))
        self.conn.commit()
    
    def get_top_users(self, chat_id, limit=10):
        return self.c.execute('SELECT user_id, reputation, messages FROM users WHERE chat_id=? AND reputation>0 ORDER BY reputation DESC LIMIT ?', (chat_id, limit)).fetchall()
    
    def get_warn_history(self, user_id, chat_id):
        return self.c.execute('SELECT * FROM warns_log WHERE user_id=? AND chat_id=? ORDER BY timestamp DESC LIMIT 5', (user_id, chat_id)).fetchall()

db = Database()

# ==================== TELEGRAM API ====================
class TelegramAPI:
    @staticmethod
    def call(method, params=None):
        if params is None:
            params = {}
        try:
            r = requests.post(f'https://api.telegram.org/bot{TOKEN}/{method}', json=params, timeout=10)
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
        params = {'chat_id': chat_id, 'user_id': user_id, 'permissions': {'can_send_messages': False}}
        if until:
            params['until_date'] = until
        return TelegramAPI.call('restrictChatMember', params)
    
    @staticmethod
    def unrestrict(chat_id, user_id):
        perms = {'can_send_messages': True, 'can_send_media': True, 'can_send_polls': True, 'can_send_other': True, 'can_add_web_page_previews': True, 'can_invite_users': True}
        return TelegramAPI.call('restrictChatMember', {'chat_id': chat_id, 'user_id': user_id, 'permissions': perms})
    
    @staticmethod
    def kick(chat_id, user_id):
        TelegramAPI.call('banChatMember', {'chat_id': chat_id, 'user_id': user_id})
        TelegramAPI.call('unbanChatMember', {'chat_id': chat_id, 'user_id': user_id})
    
    @staticmethod
    def ban(chat_id, user_id):
        return TelegramAPI.call('banChatMember', {'chat_id': chat_id, 'user_id': user_id})
    
    @staticmethod
    def is_admin(chat_id, user_id):
        r = TelegramAPI.call('getChatMember', {'chat_id': chat_id, 'user_id': user_id})
        s = r.get('result', {}).get('status', '')
        return s in ['creator', 'administrator']
    
    @staticmethod
    def answer_callback(query_id, text='', show_alert=False):
        return TelegramAPI.call('answerCallbackQuery', {'callback_query_id': query_id, 'text': text, 'show_alert': show_alert})

api = TelegramAPI

# ==================== FLOOD CONTROL ====================
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
        if p in t: return True
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
    return {'inline_keyboard': [[{'text': '📊 Reports', 'callback_data': 'admin_reports'}, {'text': '📋 FAQs', 'callback_data': 'admin_faqs'}], [{'text': '📜 Rules', 'callback_data': 'admin_rules'}, {'text': '🔄 Refresh', 'callback_data': 'admin_refresh'}]]}

def settings_keyboard(chat_id):
    s1 = db.get_setting(chat_id, 'anti_spam')
    s2 = db.get_setting(chat_id, 'anti_flood')
    s3 = db.get_setting(chat_id, 'link_filter')
    s4 = db.get_setting(chat_id, 'welcome')
    return {'inline_keyboard': [[{'text': f'🛡️ Anti-Spam: {s1.upper()}', 'callback_data': f'toggle_anti_spam_{chat_id}'}], [{'text': f'🌊 Anti-Flood: {s2.upper()}', 'callback_data': f'toggle_anti_flood_{chat_id}'}], [{'text': f'🔗 Link Filter: {s3.upper()}', 'callback_data': f'toggle_link_filter_{chat_id}'}], [{'text': f'👋 Welcome: {s4.upper()}', 'callback_data': f'toggle_welcome_{chat_id}'}], [{'text': '🔙 Back', 'callback_data': 'admin_refresh'}]]}

# ==================== PRIVATE CHAT ====================
def handle_private(msg):
    user_id = msg['from']['id']
    chat_id = msg['chat']['id']
    if user_id != ADMIN_ID:
        api.send(chat_id, '⛔ Admin only.')
        return
    api.send(chat_id, '🎌 <b>MIKASA ACKERMAN</b> - Admin Panel\n\nUse buttons below:', reply_markup=admin_keyboard())

# ==================== GROUP COMMANDS ====================
def handle_group_command(msg):
    chat_id = msg['chat']['id']
    user_id = msg['from']['id']
    text = msg.get('text', '')
    args = text.split()
    cmd = args[0].lower().split('@')[0]
    reply = msg.get('reply_to_message')
    is_adm = api.is_admin(chat_id, user_id)
    
    if cmd == '/start':
        api.send(chat_id, '🎌 Mikasa Ackerman active! /help')
    elif cmd == '/help':
        api.send(chat_id, '<b>Commands:</b>\n/help /rules\n/report\n\n<b>Admins:</b>\n/warn /resetwarn /warnings\n/mute /unmute /kick /ban\n/purge /settings /top')
    elif cmd == '/rules':
        api.send(chat_id, RULES_TEXT)
    elif cmd == '/settings' and is_adm:
        api.send(chat_id, '⚙️ Settings:', reply_markup=settings_keyboard(chat_id))
    elif cmd == '/warn' and is_adm and reply:
        t = reply['from']
        c = db.add_warn(t['id'], chat_id, user_id, 'Manual')
        db.add_report('warns')
        msg_txt = f'⚠️ {t["first_name"]} warned! ({c}/{WARN_LIMIT})'
        if c >= WARN_LIMIT:
            until = int(time.time()) + MUTE_MINUTES * 60
            api.restrict(chat_id, t['id'], until)
            db.mute_user(t['id'], chat_id, (datetime.now()+timedelta(minutes=MUTE_MINUTES)).isoformat(), user_id)
            db.add_report('mutes')
            msg_txt += f'\n🔇 Muted {MUTE_MINUTES}min!'
        api.send(chat_id, msg_txt)
    elif cmd == '/resetwarn' and is_adm and reply:
        db.reset_warns(reply['from']['id'], chat_id)
        api.send(chat_id, '✅ Reset!')
    elif cmd == '/mute' and is_adm and reply:
        mins = int(args[1]) if len(args)>1 and args[1].isdigit() else MUTE_MINUTES
        until = int(time.time()) + mins*60
        api.restrict(chat_id, reply['from']['id'], until)
        db.mute_user(reply['from']['id'], chat_id, (datetime.now()+timedelta(minutes=mins)).isoformat(), user_id)
        api.send(chat_id, f'🔇 Muted {mins}min')
    elif cmd == '/unmute' and is_adm and reply:
        api.unrestrict(chat_id, reply['from']['id'])
        db.unmute_user(reply['from']['id'], chat_id)
        api.send(chat_id, '✅ Unmuted')
    elif cmd == '/kick' and is_adm and reply:
        api.kick(chat_id, reply['from']['id'])
        api.send(chat_id, '👢 Kicked')
    elif cmd == '/ban' and is_adm and reply:
        api.ban(chat_id, reply['from']['id'])
        api.send(chat_id, '🚫 Banned')
    elif cmd == '/purge' and is_adm and reply:
        n = min(int(args[1]) if len(args)>1 and args[1].isdigit() else 10, 100)
        api.delete(chat_id, msg['message_id'])
        for i in range(n): api.delete(chat_id, reply['message_id']+i)
    elif cmd == '/report' and reply:
        api.send(ADMIN_ID, f'📩 Report from {user_id} in {chat_id}')

# ==================== GROUP MESSAGES ====================
def handle_group_message(msg):
    chat_id = msg['chat']['id']
    user_id = msg['from']['id']
    text = msg.get('text', msg.get('caption', ''))
    if api.is_admin(chat_id, user_id): return
    if db.is_muted(user_id, chat_id):
        api.delete(chat_id, msg['message_id']); return
    if db.get_setting(chat_id, 'anti_flood') != 'off' and fc.is_flooding(user_id):
        api.delete(chat_id, msg['message_id'])
        until = int(time.time()) + 300
        api.restrict(chat_id, user_id, until)
        db.mute_user(user_id, chat_id, (datetime.now()+timedelta(minutes=5)).isoformat(), 0)
        api.send(chat_id, '🌊 Flood! Muted 5min.'); return
    if db.get_setting(chat_id, 'anti_spam') != 'off' and is_scam(text):
        api.delete(chat_id, msg['message_id'])
        c = db.add_warn(user_id, chat_id, 0, 'Scam')
        db.add_report('warns')
        alert = f'⚠️ Scam! Warns: {c}/{WARN_LIMIT}'
        if c >= WARN_LIMIT:
            until = int(time.time()) + MUTE_MINUTES*60
            api.restrict(chat_id, user_id, until)
            db.mute_user(user_id, chat_id, (datetime.now()+timedelta(minutes=MUTE_MINUTES)).isoformat(), 0)
            alert += f'\n🔇 Muted {MUTE_MINUTES}min!'
        sent = api.send(chat_id, alert)
        if sent.get('ok'): time.sleep(5); api.delete(chat_id, sent['result']['message_id'])
        return
    if db.get_setting(chat_id, 'link_filter') != 'off':
        for l in extract_links(text):
            if not is_allowed_link(l):
                api.delete(chat_id, msg['message_id'])
                c = db.add_warn(user_id, chat_id, 0, 'Link')
                db.add_report('warns')
                alert = f'🔗 Only animethic.in/.xyz! Warns: {c}/{WARN_LIMIT}'
                if c >= WARN_LIMIT:
                    until = int(time.time()) + MUTE_MINUTES*60
                    api.restrict(chat_id, user_id, until)
                    db.mute_user(user_id, chat_id, (datetime.now()+timedelta(minutes=MUTE_MINUTES)).isoformat(), 0)
                    alert += f'\n🔇 Muted {MUTE_MINUTES}min!'
                sent = api.send(chat_id, alert)
                if sent.get('ok'): time.sleep(5); api.delete(chat_id, sent['result']['message_id'])
                return
    faq_r = db.get_faq(text.strip())
    if faq_r: api.send(chat_id, faq_r)

# ==================== JOIN ====================
def handle_join(msg):
    chat_id = msg['chat']['id']
    user = msg['new_chat_member']['user']
    db.add_report('joins')
    if fc.is_raid(chat_id): api.send(ADMIN_ID, f'🚨 Raid! {chat_id}')
    if db.get_setting(chat_id, 'welcome') != 'off':
        api.send(chat_id, WELCOME_TEXT.format(name=user['first_name']))

# ==================== CALLBACK ====================
def handle_callback(query):
    qid = query['id']
    data = query['data']
    msg = query.get('message', {})
    cid = msg.get('chat', {}).get('id', 0)
    uid = query['from']['id']
    if uid != ADMIN_ID: api.answer_callback(qid, '⛔ Admin only!', True); return
    
    if data == 'admin_refresh':
        api.edit(cid, msg['message_id'], '🎌 Admin Panel', reply_markup=admin_keyboard())
    elif data == 'admin_reports':
        r = db.get_report()
        t = f'📊 Today\n⚠️ {r[1] if r else 0} | 🔇 {r[2] if r else 0} | 👥 {r[6] if r else 0}'
        api.edit(cid, msg['message_id'], t, reply_markup={'inline_keyboard': [[{'text':'🔙 Back','callback_data':'admin_refresh'}]]})
    elif data == 'admin_rules':
        api.edit(cid, msg['message_id'], RULES_TEXT, reply_markup={'inline_keyboard': [[{'text':'🔙 Back','callback_data':'admin_refresh'}]]})
    elif data.startswith('toggle_'):
        parts = data.split('_')
        feat = '_'.join(parts[1:-1])
        gid = int(parts[-1])
        cur = db.get_setting(gid, feat)
        new = 'off' if cur == 'on' else 'on'
        db.set_setting(gid, feat, new)
        api.answer_callback(qid, f'✅ {feat} = {new}')
        api.edit(cid, msg['message_id'], '⚙️ Settings:', reply_markup=settings_keyboard(gid))
    api.answer_callback(qid)

# ==================== MAIN ====================
def process(update):
    if 'message' in update:
        m = update['message']
        cid = m['chat']['id']
        uid = m['from']['id']
        txt = m.get('text', m.get('caption', ''))
        if cid == uid: handle_private(m)
        elif txt.startswith('/'): handle_group_command(m)
        else: handle_group_message(m)
    elif 'chat_member' in update:
        if update['chat_member'].get('new_chat_member', {}).get('status') == 'member':
            handle_join(update['chat_member'])
    elif 'callback_query' in update:
        handle_callback(update['callback_query'])

def main():
    print("🎌 Mikasa v3.0 Started!")
    offset = 0
    while True:
        try:
            r = requests.get(f'https://api.telegram.org/bot{TOKEN}/getUpdates', params={'offset': offset, 'timeout': 30}, timeout=35)
            data = r.json()
            if data.get('ok'):
                for u in data['result']:
                    offset = u['update_id'] + 1
                    try: process(u)
                    except Exception as e: print(f'Error: {e}')
        except Exception as e:
            print(f'Conn Error: {e}')
            time.sleep(2)

if __name__ == '__main__':
    main()
