import asyncio
import logging
import re
import json
import time
import random
from datetime import datetime
from aiogram import Bot, Dispatcher
from aiogram.types import (
    Message, LinkPreviewOptions, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
)
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import aiohttp
import aiosqlite

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8858053496:AAEDHlFV4HBVa9bXCdEdiYTGYkFFBxPWgcU"
# Твои 3 ключа для распределения нагрузки!
STEAM_API_KEYS = [
    "58078C086C8EB81A26316C824EBBF452",
    "AA7E37631CE33F6D2E802B87B9438C5F",
    "354B4A89A071C82E0213772519B80AAA"
]

ADMIN_ID = 6739835571  
DB_NAME = "steam_users.db"
WEB_APP_URL = "https://newkindoflove.github.io/steam-panel-ui/" 

GROUP_ID = -1003937921596
TOPIC_SYSTEM = 3 
TOPIC_LOGS = 12

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

TG_TOKEN = None
TG_DB = None
TG_CMD = None

async def send_alert(text, is_system=False, **kwargs):
    chat = GROUP_ID if GROUP_ID else ADMIN_ID
    topic = TOPIC_SYSTEM if is_system else TOPIC_LOGS
    try:
        if GROUP_ID and topic:
            await bot.send_message(chat, text, message_thread_id=topic, **kwargs)
        else:
            await bot.send_message(ADMIN_ID, text, **kwargs)
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление: {e}")

async def telegraph_request(method, **kwargs):
    async with aiohttp.ClientSession() as session:
        async with session.post(f"https://api.telegra.ph/{method}", data=kwargs) as r:
            return await r.json()

async def init_telegraph():
    global TG_TOKEN, TG_DB, TG_CMD
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                steam_id TEXT PRIMARY KEY, name TEXT, avatar TEXT, profile_url TEXT,
                notifications INTEGER DEFAULT 0, last_status INTEGER DEFAULT 0, last_game TEXT DEFAULT '',
                note TEXT DEFAULT '', cs_hours TEXT DEFAULT '0', inv_value TEXT DEFAULT 'Скрыто 🔒',
                tb_status TEXT DEFAULT 'none', tb_time INTEGER DEFAULT 0, added_date TEXT DEFAULT '',
                is_checker INTEGER DEFAULT 0, log_number TEXT DEFAULT ''
            )
        """)
        try: await db.execute("ALTER TABLE users ADD COLUMN tb_status TEXT DEFAULT 'none'")
        except: pass
        try: await db.execute("ALTER TABLE users ADD COLUMN tb_time INTEGER DEFAULT 0")
        except: pass
        try: await db.execute("ALTER TABLE users ADD COLUMN added_date TEXT DEFAULT ''")
        except: pass
        try: await db.execute("ALTER TABLE users ADD COLUMN is_checker INTEGER DEFAULT 0")
        except: pass
        try: await db.execute("ALTER TABLE users ADD COLUMN log_number TEXT DEFAULT ''")
        except: pass
        
        await db.execute("DELETE FROM users WHERE is_checker = 1")
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('all_notifs', '1')")
        await db.commit()

        async with db.execute("SELECT value FROM settings WHERE key='tg_token'") as cursor:
            row = await cursor.fetchone()
            if row:
                TG_TOKEN = row[0]
                TG_DB = (await (await db.execute("SELECT value FROM settings WHERE key='tg_db'")).fetchone())[0]
                TG_CMD = (await (await db.execute("SELECT value FROM settings WHERE key='tg_cmd'")).fetchone())[0]
                return

        res = await telegraph_request("createAccount", short_name="SteamBot", author_name="Bot")
        TG_TOKEN = res["result"]["access_token"]
        empty_content = json.dumps([{"tag": "p", "children": ["[]"]}])
        res_db = await telegraph_request("createPage", access_token=TG_TOKEN, title="DB", content=empty_content)
        TG_DB = res_db["result"]["path"]
        res_cmd = await telegraph_request("createPage", access_token=TG_TOKEN, title="CMD", content=empty_content)
        TG_CMD = res_cmd["result"]["path"]

        await db.execute("INSERT INTO settings (key, value) VALUES ('tg_token', ?)", (TG_TOKEN,))
        await db.execute("INSERT INTO settings (key, value) VALUES ('tg_db', ?)", (TG_DB,))
        await db.execute("INSERT INTO settings (key, value) VALUES ('tg_cmd', ?)", (TG_CMD,))
        await db.commit()

async def sync_to_cloud():
    if not TG_TOKEN: return
    try:
        users_list = []
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT steam_id, name, avatar, profile_url, last_status, last_game, note, cs_hours, inv_value, notifications, tb_status, tb_time, added_date, is_checker, log_number FROM users") as cursor:
                for u in await cursor.fetchall():
                    users_list.append({
                        "id": u[0], "name": u[1], "avatar": u[2], "url": u[3], "status": u[4], "game": u[5], 
                        "note": u[6], "hours": u[7], "inv": u[8], "notif": u[9], "tb_status": u[10], "tb_time": u[11],
                        "added_date": u[12], "is_checker": u[13], "log": u[14]
                    })
        
        json_str = json.dumps(users_list, separators=(',', ':'))
        chunks = [json_str[i:i+4000] for i in range(0, len(json_str), 4000)]
        content = json.dumps([{"tag": "p", "children": chunks if chunks else ["[]"]}])
        await telegraph_request("editPage", access_token=TG_TOKEN, path=TG_DB, title="DB", content=content)
    except Exception as e:
        logging.error(f"Sync error: {e}")

async def poll_commands():
    if not TG_TOKEN: return
    try:
        res = await telegraph_request("getPage", path=TG_CMD, return_content="true")
        if not res.get("ok"): return
        nodes = res["result"].get("content", [])
        if not nodes: return
        
        cmd_json = "".join([c for c in nodes[0].get("children", []) if isinstance(c, str)])
        if not cmd_json or cmd_json == "[]": return
        try: cmds = json.loads(cmd_json)
        except: cmds = []
        if not cmds: return
        
        empty_content = json.dumps([{"tag": "p", "children": ["[]"]}])
        await telegraph_request("editPage", access_token=TG_TOKEN, path=TG_CMD, title="CMD", content=empty_content)
        
        changed = False
        async with aiosqlite.connect(DB_NAME, timeout=20.0) as db:
            async with aiohttp.ClientSession() as session:
                for data in cmds:
                    action = data.get("action")
                    steam_id = data.get("steam_id")
                    
                    if action == "force_update":
                        async with db.execute("SELECT steam_id FROM users WHERE is_checker = 0") as cursor:
                            users_to_update = await cursor.fetchall()
                        for (sid,) in users_to_update:
                            profile = await get_steam_profile(session, sid)
                            if profile:
                                cs_hours = await get_cs_hours(session, sid)
                                inv_val = await get_inventory_cs2(session, sid)
                                await db.execute("""
                                    UPDATE users SET last_status=?, last_game=?, cs_hours=?, inv_value=?, name=?, avatar=? WHERE steam_id=?
                                """, (profile.get('personastate', 0), profile.get('gameextrainfo', ''), cs_hours, inv_val, profile.get('personaname', 'User'), profile.get('avatarfull', ''), sid))
                            await asyncio.sleep(0.5) 
                        changed = True

                    elif action == "force_update_single":
                        profile = await get_steam_profile(session, steam_id)
                        if profile:
                            cs_hours = await get_cs_hours(session, steam_id)
                            inv_val = await get_inventory_cs2(session, steam_id)
                            await db.execute("""
                                UPDATE users SET last_status=?, last_game=?, cs_hours=?, inv_value=?, name=?, avatar=? WHERE steam_id=?
                            """, (profile.get('personastate', 0), profile.get('gameextrainfo', ''), cs_hours, inv_val, profile.get('personaname', 'User'), profile.get('avatarfull', ''), steam_id))
                        changed = True

                    elif action == "checker_scan":
                        url = data.get("url", "").strip()
                        new_steam_id = await resolve_vanity_url(session, url)
                        if new_steam_id:
                            profile = await get_steam_profile(session, new_steam_id)
                            if profile:
                                cs_hours = await get_cs_hours(session, new_steam_id)
                                inv_cs = await get_inventory_cs2(session, new_steam_id)
                                chk_id = f"chk_{new_steam_id}"
                                await db.execute("""
                                    INSERT OR REPLACE INTO users (steam_id, name, avatar, profile_url, last_status, cs_hours, inv_value, is_checker, added_date)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, '')
                                """, (chk_id, profile.get('personaname', 'User'), profile.get('avatarfull', ''), profile['profileurl'], profile.get('personastate', 0), cs_hours, inv_cs))
                                changed = True
                            else:
                                await send_alert(f"❌ Чекер: Профиль скрыт или не существует", is_system=True)
                        else:
                            await send_alert(f"❌ Чекер: Неверная ссылка ({url})", is_system=True)

                    elif action == "add_users_batch":
                        urls = data.get("urls", [])
                        added_count = 0
                        for url in urls:
                            new_steam_id = await resolve_vanity_url(session, url)
                            if not new_steam_id: continue
                            
                            async with db.execute("SELECT steam_id FROM users WHERE steam_id = ?", (new_steam_id,)) as cursor:
                                if await cursor.fetchone(): continue
                            
                            profile = await get_steam_profile(session, new_steam_id)
                            if profile:
                                cs_hours = await get_cs_hours(session, new_steam_id)
                                inv_val = await get_inventory_cs2(session, new_steam_id)
                                current_date = datetime.now().strftime("%d.%m.%Y")
                                
                                await db.execute("""
                                    INSERT OR REPLACE INTO users (steam_id, name, avatar, profile_url, last_status, cs_hours, inv_value, is_checker, added_date, notifications)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 0)
                                """, (new_steam_id, profile.get('personaname', 'User'), profile.get('avatarfull', ''), profile['profileurl'], profile.get('personastate', 0), cs_hours, inv_val, current_date))
                                added_count += 1
                                
                                if added_count % 3 == 0:
                                    await db.commit()
                                    await sync_to_cloud()
                            # Снизил задержку, так как ключей теперь 3
                            await asyncio.sleep(0.8) 
                        if added_count > 0:
                            await send_alert(f"✅ Массовый импорт завершен: добавлено {added_count} пользователей!", parse_mode="HTML")
                        changed = True

                    elif action == "add_user":
                        url = data.get("url", "").strip()
                        new_steam_id = await resolve_vanity_url(session, url)
                        if new_steam_id:
                            async with db.execute("SELECT steam_id FROM users WHERE steam_id = ?", (new_steam_id,)) as cursor:
                                if await cursor.fetchone(): continue
                            profile = await get_steam_profile(session, new_steam_id)
                            if profile:
                                cs_hours = await get_cs_hours(session, new_steam_id)
                                inv_val = await get_inventory_cs2(session, new_steam_id)
                                current_date = datetime.now().strftime("%d.%m.%Y")
                                await db.execute("""
                                    INSERT OR REPLACE INTO users (steam_id, name, avatar, profile_url, last_status, cs_hours, inv_value, is_checker, added_date, notifications)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 0)
                                """, (new_steam_id, profile.get('personaname', 'User'), profile.get('avatarfull', ''), profile['profileurl'], profile.get('personastate', 0), cs_hours, inv_val, current_date))
                                changed = True

                    elif action == "approve_checker":
                        if steam_id.startswith("chk_"):
                            real_id = steam_id[4:]
                            current_date = datetime.now().strftime("%d.%m.%Y")
                            await db.execute("DELETE FROM users WHERE steam_id = ?", (real_id,))
                            await db.execute("UPDATE users SET steam_id = ?, is_checker = 0, added_date = ? WHERE steam_id = ?", (real_id, current_date, steam_id))
                            changed = True
                    elif action == "set_all_notifs":
                        val = '1' if data.get("value") else '0'
                        await db.execute("UPDATE settings SET value = ? WHERE key = 'all_notifs'", (val,))
                        changed = True
                    elif action == "update_note":
                        await db.execute("UPDATE users SET note = ? WHERE steam_id = ?", (data.get("note", ""), steam_id))
                        changed = True
                    elif action == "update_tradeban":
                        await db.execute("UPDATE users SET tb_status = ?, tb_time = ?, log_number = ? WHERE steam_id = ?", (data.get("tb_status"), data.get("tb_time"), data.get("log", ""), steam_id))
                        changed = True
                    elif action == "delete":
                        await db.execute("DELETE FROM users WHERE steam_id = ?", (steam_id,))
                        changed = True
                    elif action == "toggle_notif":
                        async with db.execute("SELECT notifications FROM users WHERE steam_id = ?", (steam_id,)) as cursor:
                            row = await cursor.fetchone()
                            if row:
                                new_notif = 0 if row[0] else 1
                                await db.execute("UPDATE users SET notifications = ? WHERE steam_id = ?", (new_notif, steam_id))
                                changed = True

            await db.commit()
        if changed: await sync_to_cloud()
    except Exception as e:
        logging.error(f"Poll Error: {e}")

async def resolve_vanity_url(session, url_or_id):
    url_or_id = url_or_id.strip().strip('/')
    if re.match(r'^\d{17}$', url_or_id): return url_or_id
    
    vanity_name = url_or_id
    if "steamcommunity.com/profiles/" in url_or_id:
        return url_or_id.split("profiles/")[1].split('/')[0]
    elif "steamcommunity.com/id/" in url_or_id:
        vanity_name = url_or_id.split("id/")[1].split('/')[0]
        
    # БЕРЕМ СЛУЧАЙНЫЙ КЛЮЧ
    key = random.choice(STEAM_API_KEYS)
    url = f"http://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/?key={key}&vanityurl={vanity_name}"
    async with session.get(url) as response:
        data = await response.json()
        if data['response']['success'] == 1: return data['response']['steamid']
    return None

async def get_steam_profile(session, steam_id):
    # БЕРЕМ СЛУЧАЙНЫЙ КЛЮЧ
    key = random.choice(STEAM_API_KEYS)
    url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={key}&steamids={steam_id}"
    async with session.get(url) as response:
        data = await response.json()
        if not data['response']['players']: return None
        return data['response']['players'][0]

async def get_cs_hours(session, steam_id):
    # БЕРЕМ СЛУЧАЙНЫЙ КЛЮЧ
    key = random.choice(STEAM_API_KEYS)
    url = f"http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/?key={key}&steamid={steam_id}"
    try:
        async with session.get(url) as response:
            if response.status != 200: return "0 ч."
            data = await response.json()
            for game in data.get('response', {}).get('games', []):
                if game['appid'] == 730: return f"{round(game['playtime_forever'] / 60, 1)} ч."
    except: pass
    return "0 ч."

async def get_inventory_cs2(session, steam_id):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    
    items_count = None
    price_str = None
    wok_key = "wok_eu68v0uqpZuBa56w8YWlVN57JcWcC8TO"
    wok_headers = {"Authorization": f"Bearer {wok_key}"}

    try:
        url = "https://woksteamapi.com/v1/inventory"
        params = {"steam_id": steam_id, "game": "cs2"}
        async with session.get(url, headers=wok_headers, params=params, timeout=8) as r:
            if r.status == 200:
                data = await r.json()
                status = data.get("status")

                if status in ["private", "notfound"]: return "Скрыто 🔒"
                if status == "empty": return "$0.00 (0 шт.)"

                items_count = data.get("items_total")
                val = data.get("total")
                if val is not None and items_count is not None:
                    return f"${float(val):.2f} ({items_count} шт.)"
    except:
        pass

    if items_count is None:
        try:
            steam_url = f"https://steamcommunity.com/inventory/{steam_id}/730/2?count=1"
            async with session.get(steam_url, headers=headers, timeout=5) as r:
                if r.status in [401, 403]: return "Скрыто 🔒"
                if r.status == 200:
                    data = await r.json()
                    items_count = data.get("total_inventory_count", 0)
                    if items_count == 0: return "$0.00 (0 шт.)"
        except: pass

    if not price_str and items_count:
        try:
            async with session.get(f"https://csgobackpack.net/api/GetInventoryValue/?id={steam_id}", headers=headers, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get('success'): 
                        val = data['value'].get('7_days', data['value'].get('30_days', data['value'].get('all_time', 0)))
                        try:
                            clean_val = str(val).replace(',', '').replace('$', '')
                            price_str = f"${float(clean_val):.2f}"
                        except:
                            price_str = f"${val}"
        except: pass

    if price_str and items_count is not None:
        return f"{price_str} ({items_count} шт.)"
    elif items_count is not None:
        return f"Неизвестно ⚠️ ({items_count} шт.)"
    
    return "Неизвестно ⚠️"

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != "private":
        return await message.answer("❌ Панель управления доступна только в личных сообщениях с ботом!")
        
    final_url = f"{WEB_APP_URL}?t={TG_TOKEN}&d={TG_DB}&c={TG_CMD}"
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🌐 Открыть панель", web_app=WebAppInfo(url=final_url))]], resize_keyboard=True, is_persistent=True)
    await message.answer("✅ Панель готова. Жми кнопку!", reply_markup=kb)

async def check_timers():
    current_time = int(time.time())
    changed = False
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT steam_id, name, profile_url, tb_status FROM users WHERE is_checker=0 AND tb_status LIKE 'ban%' AND tb_time > 0 AND tb_time <= ?", (current_time,)) as cursor:
            ready_bans = await cursor.fetchall()
            
        for ban in ready_bans:
            steam_id, name, url, status = ban
            approx_str = " (примерно)" if status == "ban_approx" else ""
            await send_alert(f"🔓 <b>РАЗБЛОКИРОВКА!</b>\nУ пользователя <a href='{url}'>{name}</a> разбанились предметы{approx_str}! Можно снимать.", parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
            await db.execute("UPDATE users SET tb_status = 'ready', tb_time = 0 WHERE steam_id = ?", (steam_id,))
            changed = True
            
        async with db.execute("SELECT steam_id, name, profile_url FROM users WHERE is_checker=0 AND tb_status LIKE 'snat%' AND tb_time > 0 AND tb_time <= ?", (current_time,)) as cursor:
            ready_snats = await cursor.fetchall()
            
        for snat in ready_snats:
            steam_id, name, url = snat
            await send_alert(f"💸 Пользователь <a href='{url}'>{name}</a> - разбанился!", parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
            await db.execute("UPDATE users SET tb_status = 'unbanned', tb_time = 0 WHERE steam_id = ?", (steam_id,))
            changed = True

        await db.commit()
    if changed: await sync_to_cloud()

async def check_statuses():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT steam_id, name, avatar, last_status, last_game, profile_url, notifications, inv_value FROM users WHERE is_checker=0") as cursor:
            users = await cursor.fetchall()
        
        async with db.execute("SELECT value FROM settings WHERE key='all_notifs'") as cursor:
            row = await cursor.fetchone()
            all_notifs = row[0] == '1' if row else True

    if not users: return

    changed = False
    async with aiohttp.ClientSession() as session:
        for user in users:
            steam_id, old_name, old_avatar, last_status, last_game, profile_url, notif_on, old_inv_val = user
            profile = await get_steam_profile(session, steam_id)
            if not profile or profile.get('communityvisibilitystate', 1) != 3: continue

            current_status = profile.get('personastate', 0)
            current_game = profile.get('gameextrainfo', '')
            current_name = profile.get('personaname', old_name)
            current_avatar = profile.get('avatarfull', old_avatar)
            
            user_link = f"<a href='{profile_url}'><b>{current_name}</b></a>"
            name_changed = (current_name != old_name) and old_name
            avatar_changed = (current_avatar != old_avatar) and old_avatar

            force_inv_check = ("⚠️" in str(old_inv_val))

            if current_status != last_status or current_game != last_game or name_changed or avatar_changed or force_inv_check:
                cs_hours = await get_cs_hours(session, steam_id)
                inv_val = await get_inventory_cs2(session, steam_id)
                
                if current_status == last_status and current_game == last_game and not name_changed and not avatar_changed and inv_val == old_inv_val:
                    await asyncio.sleep(2)
                    continue

                async with aiosqlite.connect(DB_NAME) as db:
                    await db.execute("UPDATE users SET last_status=?, last_game=?, cs_hours=?, inv_value=?, name=?, avatar=? WHERE steam_id=?", 
                                     (current_status, current_game, cs_hours, inv_val, current_name, current_avatar, steam_id))
                    await db.commit()
                changed = True
                await asyncio.sleep(2)

            if notif_on:
                if name_changed: await send_alert(f"🔄 Пользователь <b>{old_name}</b> сменил ник на {user_link}!", parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
                
                if all_notifs:
                    if avatar_changed: await send_alert(f"🖼 Пользователь {user_link} обновил аватарку!", parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
                    if current_game != last_game:
                        if current_game: await send_alert(f"🎮 {user_link} зашел в {current_game}!", parse_mode="HTML", disable_notification=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
                        elif last_game: await send_alert(f"⏹ {user_link} вышел из игры.", parse_mode="HTML", disable_notification=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
                
                if current_status != last_status:
                    if current_status == 0: msg = f"🔴 {user_link} теперь оффлайн"
                    elif current_status == 1: msg = f"🟢 {user_link} теперь в сети"
                    elif current_status in [2, 3, 4]: msg = f"🟡 {user_link} отошел/не беспокоить"
                    else: continue
                    await send_alert(msg, parse_mode="HTML", disable_notification=True, link_preview_options=LinkPreviewOptions(is_disabled=True))
                    
    if changed: await sync_to_cloud()

async def main():
    await init_telegraph()
    await sync_to_cloud()
    
    scheduler.add_job(poll_commands, "interval", seconds=3, max_instances=1)
    scheduler.add_job(check_statuses, "interval", seconds=60)
    scheduler.add_job(check_timers, "interval", minutes=2)
    scheduler.start()
    
    status_text_online = "<b>СТАТУС БОТА:</b> 🟢 РАБОТАЕТ"
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT value FROM settings WHERE key='status_msg_id'") as cursor:
                row = await cursor.fetchone()
                
        if row:
            status_msg_id = int(row[0])
            try:
                await bot.edit_message_text(status_text_online, chat_id=GROUP_ID, message_id=status_msg_id, parse_mode="HTML")
            except Exception:
                msg = await bot.send_message(GROUP_ID, status_text_online, message_thread_id=TOPIC_SYSTEM, parse_mode="HTML")
                async with aiosqlite.connect(DB_NAME) as db:
                    await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('status_msg_id', ?)", (str(msg.message_id),))
                    await db.commit()
        else:
            msg = await bot.send_message(GROUP_ID, status_text_online, message_thread_id=TOPIC_SYSTEM, parse_mode="HTML")
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('status_msg_id', ?)", (str(msg.message_id),))
                await db.commit()
    except Exception as e:
        logging.error(f"Failed to set online status: {e}")

    try:
        await dp.start_polling(bot)
    finally:
        status_text_offline = "<b>СТАТУС БОТА:</b> 🔴 ОТКЛЮЧЕН"
        try:
            async with aiosqlite.connect(DB_NAME) as db:
                async with db.execute("SELECT value FROM settings WHERE key='status_msg_id'") as cursor:
                    row = await cursor.fetchone()
            if row:
                status_msg_id = int(row[0])
                await bot.edit_message_text(status_text_offline, chat_id=GROUP_ID, message_id=status_msg_id, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Failed to set offline status: {e}")
            
        await bot.session.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
