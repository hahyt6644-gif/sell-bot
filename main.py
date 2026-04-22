import os
import asyncio
import aiohttp
from aiohttp import web
import logging
from datetime import datetime, timedelta
import random
from telethon import TelegramClient, functions
from telethon.tl.types import ChatAdminRights
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.sessions import StringSession
import motor.motor_asyncio
import dns.resolver
import traceback


dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4']

# Setup Logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# --- CONFIGURATION ---
# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 3567))
API_HASH = os.environ.get("API_HASH", "a8ab964cb6c88")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7908931052:AAFdpDkr")
OXAPAY_KEY = os.environ.get("OXAPAY_KEY", "MEXVK4")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SESSIONS_DIR = "sessions"


WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://sell-bot-vcxn.onrender.com")
PORT = int(os.environ.get("PORT", 8080))

# --- DB SETUP ---
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://amitprojects545_db_user:XtPmY6eQTFcpHcaz@cluster0.0k08xds.mongodb.net/?appName=Cluster0")
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = mongo_client["vip_bot_db"]

products_col = db["products"]
subs_col = db["subscriptions"]
payments_col = db["payments"]
users_col = db["users"]
content_col = db["content"]
bulk_sessions_col = db["bulk_sessions"]
settings_col = db["settings"]
admin_states = db["admin_states"]
sessions_col = db["string_sessions"]

if not os.path.exists(SESSIONS_DIR): os.makedirs(SESSIONS_DIR)

# ==========================================
# CORE BOT HELPERS & INIT
# ==========================================
async def init_db():
    try:
        count = await products_col.count_documents({})
        if count == 0:
            default_categories = [{"cat_key": "python", "name": "🐍 Python Pro", "description": "Learn python with VIP access.", "image": "", "plans": {"7": {"star_price": 1, "crypto_price": 0.1, "label": "7 Days"}}}]
            await products_col.insert_many(default_categories)
    except Exception as e:
        logger.error(f"Error initializing DB: {e}\n{traceback.format_exc()}")

async def get_categories():
    try:
        products = await products_col.find().to_list(length=100)
        cat_dict = {}
        for p in products:
            cat_dict[p['cat_key']] = {"name": p.get('name', ''), "description": p.get('description', ''), "image": p.get('image', ''), "plans": p.get('plans', {})}
        return cat_dict
    except Exception as e:
        logger.error(f"Error getting categories: {e}\n{traceback.format_exc()}")
        return {}

async def save_user(user_id, username, first_name):
    try:
        await users_col.update_one({"user_id": user_id}, {"$set": {"username": username, "first_name": first_name, "last_active": datetime.now().isoformat()}}, upsert=True)
    except Exception as e:
        logger.error(f"Error saving user: {e}")

async def get_admin_ids():
    settings = await settings_col.find_one({"_id": "global_settings"})
    if settings and settings.get("admin_ids"):
        return [int(x.strip()) for x in settings["admin_ids"].split(",") if x.strip().isdigit()]
    return [6931296977]

async def api_call(method, payload=None, token=None, retries=3):
    if token is None: token = BOT_TOKEN
    url = f"https://api.telegram.org/bot{token}/{method}"
    
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload or {}, timeout=45) as resp:
                    res = await resp.json()
                    if resp.status == 429:
                        retry_after = res.get("parameters", {}).get("retry_after", 5)
                        logger.warning(f"Flood wait! Sleeping for {retry_after}s before retrying...")
                        await asyncio.sleep(retry_after + 1)
                        continue 
                        
                    if resp.status != 200:
                        if method == 'editMessageText' and "there is no text in the message to edit" in res.get('description', ''):
                            pass 
                        else:
                            logger.warning(f"TG API '{method}' blew up. Status: {resp.status} - {await resp.text()}")
                    return res
        except Exception as e:
            logger.error(f"Network error on '{method}': {e}")
            await asyncio.sleep(2)
    return {}

async def download_tg_file(token, file_id):
    info = await api_call("getFile", {"file_id": file_id}, token=token)
    if not info.get("ok"):
        logger.error(f"getFile failed. File may be >20MB: {info}")
        return None
        
    fpath = info.get("result", {}).get("file_path")
    if not fpath: return None
    url = f"https://api.telegram.org/file/bot{token}/{fpath}"
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url) as resp:
                if resp.status == 200: return await resp.read()
    except Exception as e: logger.error(f"Download failed: {e}")
    return None

# ==========================================
# FAST MIRRORING LOGIC
# ==========================================
async def _upload_to_backup_bot(b_token, admin_uid, cat, ctype, file_bytes):
    data = aiohttp.FormData()
    data.add_field("chat_id", str(admin_uid))
    data.add_field("caption", f"🔄 Storage")
    data.add_field("disable_notification", "true")
    data.add_field(ctype, file_bytes, filename=f"mirror.{'mp4' if ctype == 'video' else 'jpg'}")
    
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(f"https://api.telegram.org/bot{b_token}/send{ctype.capitalize()}", data=data) as resp:
                res = await resp.json()
                if res.get("ok"):
                    msg_id = res["result"]["message_id"]
                    fid = res["result"][ctype][-1]["file_id"] if isinstance(res["result"][ctype], list) else res["result"][ctype]["file_id"]
                    async with sess.post(f"https://api.telegram.org/bot{b_token}/deleteMessage", json={"chat_id": admin_uid, "message_id": msg_id}):
                        pass
                    return {"token": b_token, "file_id": fid}
    except Exception as e: logger.error(f"Mirror to {b_token[-6:]} failed: {e}")
    return None

async def mirror_content_background(admin_uid, ctype, primary_file_id, primary_token, text_content, cat, wait_mid):
    settings = await settings_col.find_one({"_id": "global_settings"}) or {}
    backup_bots = settings.get("backup_bots", [])
    
    content_doc = {
        "category": cat, "type": ctype, "text": text_content, 
        "primary_token": primary_token, "file_id": primary_file_id,
        "mirrors": [{"token": primary_token, "file_id": primary_file_id}],
        "added_at": datetime.now().isoformat()
    }
    
    if not backup_bots or ctype == "text":
        await content_col.insert_one(content_doc)
        if wait_mid:
            await api_call("editMessageText", {"chat_id": admin_uid, "message_id": wait_mid, "text": f"✅ **Saved!** (No backup bots configured).", "parse_mode": "Markdown"}, token=primary_token)
        return

    file_bytes = await download_tg_file(primary_token, primary_file_id)
    if file_bytes:
        tasks = [_upload_to_backup_bot(bot["token"], admin_uid, cat, ctype, file_bytes) for bot in backup_bots if bot.get("token")]
        results = await asyncio.gather(*tasks)
        for r in results:
            if r: content_doc["mirrors"].append(r)

    await content_col.insert_one(content_doc)
    if wait_mid:
        await api_call("editMessageText", {"chat_id": admin_uid, "message_id": wait_mid, "text": f"✅ **Saved!** {ctype.capitalize()} safely mirrored to {len(content_doc['mirrors']) - 1} backup bots.", "parse_mode": "Markdown"}, token=primary_token)

async def send_content_to_group(group_id, content_doc):
    settings = await settings_col.find_one({"_id": "global_settings"}) or {}
    ctoken = settings.get("content_bot_token", "").strip()
    
    if not ctoken:
        logger.error("No ACTIVE Uploader Bot configured in Admin Panel! Skipping content.")
        return

    ctype = content_doc.get("type", "text")
    text = content_doc.get("text", "")
    
    if ctype == "text":
        await api_call("sendMessage", {"chat_id": group_id, "text": text}, token=ctoken)
        await asyncio.sleep(1.5)
        return

    target_fid = None
    
    for m in content_doc.get("mirrors", []):
        if m.get("token") == ctoken:
            target_fid = m.get("file_id")
            break
    
    if target_fid:
        payload = {"chat_id": group_id, "caption": text}
        if ctype == "photo": payload["photo"] = target_fid
        elif ctype == "video": payload["video"] = target_fid
        elif ctype == "document": payload["document"] = target_fid
        res = await api_call(f"send{ctype.capitalize()}", payload, token=ctoken)
    else:
        logger.info(f"Auto-healing missing mirror for content {content_doc['_id']}...")
        old_token = content_doc.get("primary_token") or content_doc.get("bot_token") or BOT_TOKEN
        old_fid = content_doc.get("file_id")
        
        file_bytes = await download_tg_file(old_token, old_fid)
        if file_bytes:
            data = aiohttp.FormData()
            data.add_field("chat_id", str(group_id))
            data.add_field("caption", text)
            data.add_field(ctype, file_bytes, filename=f"healed.{'mp4' if ctype == 'video' else 'jpg'}")
            
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.post(f"https://api.telegram.org/bot{ctoken}/send{ctype.capitalize()}", data=data) as resp:
                        res = await resp.json()
                        if res.get("ok"):
                            new_fid = res["result"][ctype][-1]["file_id"] if isinstance(res["result"][ctype], list) else res["result"][ctype]["file_id"]
                            await content_col.update_one({"_id": content_doc["_id"]}, {"$push": {"mirrors": {"token": ctoken, "file_id": new_fid}}})
            except Exception as e: logger.error(f"Auto-heal request error: {e}")
            
    await asyncio.sleep(1.5)

# ==========================================
# STRING SESSION GROUP CREATION
# ==========================================
async def generate_vip_group(user_id, cat_key, days):
    logger.info(f"---- STARTING VIP GROUP GENERATION FOR USER {user_id} ----")
    try:
        await api_call("sendMessage", {"chat_id": user_id, "text": "✅ **Payment Confirmed!**\n\n⚙️ _Generating your private VIP group..._", "parse_mode": "Markdown"}, token=BOT_TOKEN)
        
        products = await products_col.find().to_list(100)
        cat_name = next((p['name'] for p in products if p['cat_key'] == cat_key), cat_key)
        
        logger.info("Fetching active string sessions from MongoDB...")
        active_sessions = await sessions_col.find({"active": True}).to_list(None)
        if not active_sessions: 
            logger.error("CRITICAL: No active string sessions found in MongoDB!")
            await api_call("sendMessage", {"chat_id": user_id, "text": "❌ System error: No group creators available. Admin needs to upload sessions."}, token=BOT_TOKEN)
            return

        random.shuffle(active_sessions)
        settings = await settings_col.find_one({"_id": "global_settings"}) or {}
        uploader_username = settings.get("content_bot_username", "").strip()
        main_bot_info = await api_call("getMe", token=BOT_TOKEN)
        main_bot_username = main_bot_info.get("result", {}).get("username")

        for sess_doc in active_sessions:
            sess_string = sess_doc["session_string"]
            sess_id = str(sess_doc["_id"])
            logger.info(f"Attempting to generate group using Session ID: {sess_id[-6:]}")
            
            client = TelegramClient(StringSession(sess_string), API_ID, API_HASH)
            client.flood_sleep_threshold = 0 
            
            try:
                logger.info("Connecting Telethon client...")
                await client.connect()
                
                if not await client.is_user_authorized():
                    logger.warning(f"Session {sess_id[-6:]} is unauthorized. Marking inactive.")
                    await sessions_col.update_one({"_id": sess_doc["_id"]}, {"$set": {"active": False, "error": "unauthorized"}})
                    continue

                logger.info("Creating Megagroup...")
                result = await client(functions.channels.CreateChannelRequest(title=f"VIP: {cat_name} - {user_id}", about=f"Exclusive access.", megagroup=True))
                created_chat = result.chats[0]
                bot_api_group_id = int(f"-100{created_chat.id}")

                logger.info(f"Group created! ID: {bot_api_group_id}. Setting permissions...")
                await client(functions.messages.ToggleNoForwardsRequest(peer=created_chat, enabled=True))
                await client(functions.messages.SetHistoryTTLRequest(peer=created_chat, period=int(days) * 86400))
                await client(functions.channels.EditAdminRequest(channel=created_chat, user_id='me', admin_rights=ChatAdminRights(anonymous=True, change_info=True, post_messages=True, edit_messages=True, delete_messages=True, ban_users=True, invite_users=True, pin_messages=True, add_admins=True, manage_call=True), rank="Owner"))

                if main_bot_username:
                    logger.info("Inviting Main Bot...")
                    try:
                        main_entity = await client.get_entity(f"@{main_bot_username}")
                        await client(InviteToChannelRequest(channel=created_chat, users=[main_entity]))
                        await client(functions.channels.EditAdminRequest(
                            channel=created_chat, user_id=main_entity, 
                            admin_rights=ChatAdminRights(change_info=True, post_messages=False, edit_messages=False, delete_messages=True, ban_users=True, invite_users=True, pin_messages=True, add_admins=False, manage_call=True), 
                            rank="Main Bot"
                        ))
                    except Exception as e: logger.error(f"Failed to invite main bot: {e}")

                if uploader_username:
                    logger.info("Inviting Active Uploader Bot...")
                    try:
                        bot_entity = await client.get_entity(f"@{uploader_username}")
                        await client(InviteToChannelRequest(channel=created_chat, users=[bot_entity]))
                        await client(functions.channels.EditAdminRequest(
                            channel=created_chat, user_id=bot_entity, 
                            admin_rights=ChatAdminRights(anonymous=True, change_info=True, post_messages=True, edit_messages=True, delete_messages=True, ban_users=True, invite_users=True, pin_messages=True, add_admins=False, manage_call=True), 
                            rank="Uploader"
                        ))
                    except Exception as e: logger.error(f"Failed to invite uploader: {e}")

                logger.info("Exporting Invite Link...")
                invite = await client(functions.messages.ExportChatInviteRequest(peer=created_chat, usage_limit=1))
                now_str = datetime.now().isoformat()
                expiry = (datetime.now() + timedelta(days=int(days))).isoformat()

                await subs_col.insert_one({"user_id": user_id, "group_id": bot_api_group_id, "category": cat_key, "expiry_at": expiry, "session_used": sess_id[-6:], "last_synced_at": now_str})
                await api_call("sendMessage", {"chat_id": user_id, "text": f"🎉 **Your VIP Group is Ready!**\n\n🛡️ *Content protection enabled.*\n🔗 Link: {invite.link}\n⏳ Expires: {days} days.\n\n📥 _Historical content is uploading..._", "parse_mode": "Markdown", "reply_markup": {"inline_keyboard": [[{"text": "Join VIP Group", "url": invite.link}]]}}, token=BOT_TOKEN)

                logger.info("Pushing historical content to group...")
                contents = await content_col.find({"category": cat_key}).sort("added_at", 1).to_list(None)
                for c in contents: await send_content_to_group(bot_api_group_id, c)

                logger.info("✅ SUCCESS: Group fully generated and populated!")
                break # Success! End the loop.
                
            except Exception as e:
                err_msg = str(e).lower()
                logger.error(f"Failed using session {sess_id[-6:]}: {err_msg}")
                
                # DB AUTO-CLEANING FOR DEAD SESSIONS
                if "flood" in err_msg or "too many requests" in err_msg:
                    logger.warning(f"Session {sess_id[-6:]} rate-limited. Skipping to next session.")
                elif "frozen" in err_msg or "banned" in err_msg or "deactivated" in err_msg or "expired" in err_msg or "invalid" in err_msg or "unregistered" in err_msg:
                    logger.warning(f"String Session {sess_id[-6:]} is dead. Marking inactive in MongoDB.")
                    await sessions_col.update_one({"_id": sess_doc["_id"]}, {"$set": {"active": False, "error": err_msg}})
                continue
            finally:
                try: await client.disconnect()
                except Exception: pass
                    
    except Exception as e: 
        logger.error(f"Group generation FATAL error: {e}\n{traceback.format_exc()}")

# ==========================================
# INVOICE GENERATORS
# ==========================================
async def create_crypto_invoice(user_id, cat_key, days, network):
    products = await products_col.find().to_list(100)
    cat_data = next((p for p in products if p['cat_key'] == cat_key), None)
    price = cat_data['plans'][str(days)]['crypto_price']
    order_id = f"CRYP_{user_id}_{int(datetime.now().timestamp())}"
    payload = {"amount": price, "pay_currency": "USDT", "network": network, "order_id": order_id, "callback_url": f"{WEBHOOK_URL}/oxapay_callback"}
    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.oxapay.com/v1/payment/white-label", json=payload, headers={"merchant_api_key": OXAPAY_KEY}) as resp:
            if resp.status == 200:
                data = (await resp.json()).get("data")
                await payments_col.insert_one({"user_id": user_id, "order_id": order_id, "category": cat_key, "days": int(days), "status": "pending"})
                data['merchant_order_id'] = order_id
                return data
    return None

async def create_star_invoice_link(uid, cat, days, ptoken):
    products = await products_col.find().to_list(100)
    cat_data = next((p for p in products if p['cat_key'] == cat), None)
    price = cat_data['plans'][str(days)]['star_price']
    payload = {"title": f"VIP Access - {cat_data['name']}", "description": f"{days} Days Access", "payload": f"sub_{cat}_{days}_{uid}", "currency": "XTR", "prices": [{"label": "VIP Access", "amount": price}]}
    res = await api_call("createInvoiceLink", payload, token=ptoken)
    return res.get("result")

# ==========================================
# BOT WEBHOOK HANDLERS
# ==========================================
async def send_duration_menu(uid, cat_key, categories, mid=None):
    try:
        cat_data = categories.get(cat_key)
        if not cat_data: return
        kb = {"inline_keyboard": [[{"text": f"{v['label']} - ${v.get('crypto_price',0)}", "callback_data": f"days_{cat_key}_{k}"}] for k, v in cat_data['plans'].items()]}
        kb["inline_keyboard"].append([{"text": "🔙 Back to Categories", "callback_data": "back_main"}])
        caption = f"**{cat_data['name']}**\n\n_{cat_data['description']}_\n\n⏳ **Select Duration:**"

        if cat_data.get("image") and cat_data["image"].strip() != "":
            if mid: await api_call("deleteMessage", {"chat_id": uid, "message_id": mid}, token=BOT_TOKEN)
            await api_call("sendPhoto", {"chat_id": uid, "photo": cat_data["image"].strip(), "caption": caption, "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)
        else:
            if mid: 
                res = await api_call("editMessageText", {"chat_id": uid, "message_id": mid, "text": caption, "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)
                if not res or not res.get("ok"):
                    await api_call("deleteMessage", {"chat_id": uid, "message_id": mid}, token=BOT_TOKEN)
                    await api_call("sendMessage", {"chat_id": uid, "text": caption, "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)
            else: 
                await api_call("sendMessage", {"chat_id": uid, "text": caption, "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)
    except Exception as e: pass

async def process_update(update):
    try:
        categories = await get_categories()
        
        if "message" in update:
            msg = update["message"]
            uid = msg["chat"]["id"]
            text = msg.get("text", "")
            
            if msg["chat"].get("type") == "private":
                asyncio.create_task(save_user(uid, msg["from"].get("username", ""), msg["from"].get("first_name", "")))
            
            admin_ids = await get_admin_ids()

            # --- SESSION CONVERTER ---
            if uid in admin_ids:
                state_doc = await admin_states.find_one({"uid": uid})
                
                if text == "/sessions":
                    await admin_states.update_one({"uid": uid}, {"$set": {"state": "uploading_sessions"}}, upsert=True)
                    await api_call("sendMessage", {"chat_id": uid, "text": "📂 **Session Converter Mode:**\n\nSend `.session` files here. I will extract the String Session, save it to the DB, and delete the file. When finished, send `/sesdn`."}, token=BOT_TOKEN)
                    return
                    
                if text == "/sesdn":
                    await admin_states.update_one({"uid": uid}, {"$set": {"state": "idle"}})
                    count = await sessions_col.count_documents({"active": True})
                    await api_call("sendMessage", {"chat_id": uid, "text": f"✅ Session mode closed. {count} active string sessions available in DB."}, token=BOT_TOKEN)
                    return

                if state_doc and state_doc.get("state") == "uploading_sessions":
                    if "document" in msg and msg["document"]["file_name"].endswith(".session"):
                        file_id = msg["document"]["file_id"]
                        fname = msg["document"]["file_name"]
                        session_name = fname.replace(".session", "")
                        filepath = os.path.join(SESSIONS_DIR, session_name)
                        
                        await api_call("sendMessage", {"chat_id": uid, "text": f"⏳ Downloading and converting `{fname}`..."}, token=BOT_TOKEN)
                        file_bytes = await download_tg_file(BOT_TOKEN, file_id)
                        
                        if file_bytes:
                            with open(f"{filepath}.session", 'wb') as f: 
                                f.write(file_bytes)
                                
                            client = TelegramClient(f"{filepath}.session", API_ID, API_HASH)
                            try:
                                await client.connect()
                                if await client.is_user_authorized():
                                    # THE FIX: Safely convert SQLite to StringSession format
                                    string_session = StringSession.save(client.session)
                                    
                                    if string_session:
                                        await sessions_col.insert_one({
                                            "session_string": string_session,
                                            "active": True,
                                            "added_at": datetime.now().isoformat()
                                        })
                                        await api_call("sendMessage", {"chat_id": uid, "text": f"✅ Successfully converted `{fname}` to String Session and saved to DB!"}, token=BOT_TOKEN)
                                    else:
                                        await api_call("sendMessage", {"chat_id": uid, "text": f"❌ Error: Extracted string was empty. Corrupted session file?"}, token=BOT_TOKEN)
                                else:
                                    await api_call("sendMessage", {"chat_id": uid, "text": f"❌ Session `{fname}` is not authorized/logged out."}, token=BOT_TOKEN)
                            except Exception as e:
                                logger.error(f"Session conversion error: {e}")
                                await api_call("sendMessage", {"chat_id": uid, "text": f"❌ Error converting `{fname}`: {str(e)}"}, token=BOT_TOKEN)
                            finally:
                                try: await client.disconnect()
                                except: pass
                                try: os.remove(f"{filepath}.session")
                                except: pass
                                try: os.remove(f"{filepath}.session-journal")
                                except: pass
                    return

            if text.startswith("/start"):
                parts = text.split(" ")
                if len(parts) > 1 and parts[1] in categories:
                    await send_duration_menu(uid, parts[1], categories)
                else:
                    kb = {"inline_keyboard": [[{"text": v['name'], "callback_data": f"cat_{k}"}] for k, v in categories.items()]}
                    await api_call("sendMessage", {"chat_id": uid, "text": "📚 **Select Category:**", "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)

        elif "callback_query" in update:
            uid = update["callback_query"]["message"]["chat"]["id"]
            mid = update["callback_query"]["message"]["message_id"]
            data = update["callback_query"]["data"]

            if data == "back_main":
                kb = {"inline_keyboard": [[{"text": v['name'], "callback_data": f"cat_{k}"}] for k, v in categories.items()]}
                res = await api_call("editMessageText", {"chat_id": uid, "message_id": mid, "text": "📚 **Select Category:**", "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)
                if not res or not res.get("ok"):
                    await api_call("deleteMessage", {"chat_id": uid, "message_id": mid}, token=BOT_TOKEN)
                    await api_call("sendMessage", {"chat_id": uid, "text": "📚 **Select Category:**", "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)
                
            elif data.startswith("cat_"):
                await send_duration_menu(uid, data.split("_")[1], categories, mid)

            elif data.startswith("days_"):
                _, cat, d = data.split("_")
                plan = categories[cat]['plans'][d]
                kb_buttons = []
                if plan.get('star_price', 0) > 0: kb_buttons.append([{"text": f"⭐️ Telegram Stars", "callback_data": f"pay_{cat}_{d}_stars"}])
                if plan.get('crypto_price', 0) > 0: kb_buttons.append([{"text": f"💎 USDT Crypto", "callback_data": f"pay_{cat}_{d}_crypto"}])
                kb_buttons.append([{"text": "🔙 Back", "callback_data": f"cat_{cat}"}])
                
                res = await api_call("editMessageText", {"chat_id": uid, "message_id": mid, "text": "💳 **Select Payment Method:**", "parse_mode": "Markdown", "reply_markup": {"inline_keyboard": kb_buttons}}, token=BOT_TOKEN)
                if not res or not res.get("ok"):
                    await api_call("deleteMessage", {"chat_id": uid, "message_id": mid}, token=BOT_TOKEN)
                    await api_call("sendMessage", {"chat_id": uid, "text": "💳 **Select Payment Method:**", "parse_mode": "Markdown", "reply_markup": {"inline_keyboard": kb_buttons}}, token=BOT_TOKEN)
                
            elif data.startswith("pay_"):
                _, cat, d, method = data.split("_")
                if method == "stars":
                    settings = await settings_col.find_one({"_id": "global_settings"}) or {}
                    ptoken = settings.get("payment_bot_token", "").strip()
                    if not ptoken:
                        await api_call("sendMessage", {"chat_id": uid, "text": "❌ Admin has not configured the payment bot."}, token=BOT_TOKEN)
                        return
                    
                    res = await api_call("editMessageText", {"chat_id": uid, "message_id": mid, "text": "⏳ Generating Secure Invoice..."}, token=BOT_TOKEN)
                    if not res or not res.get("ok"):
                        await api_call("deleteMessage", {"chat_id": uid, "message_id": mid}, token=BOT_TOKEN)
                        sent_msg = await api_call("sendMessage", {"chat_id": uid, "text": "⏳ Generating Secure Invoice..."}, token=BOT_TOKEN)
                        mid = sent_msg.get("result", {}).get("message_id", mid)

                    invoice_url = await create_star_invoice_link(uid, cat, d, ptoken)
                    if invoice_url:
                        kb = {"inline_keyboard": [[{"text": "⭐️ Pay Now", "url": invoice_url}], [{"text": "🔙 Back", "callback_data": f"days_{cat}_{d}"}]]}
                        res = await api_call("editMessageText", {"chat_id": uid, "message_id": mid, "text": f"⭐️ **Secure Checkout**\n\nClick below to securely purchase your access.", "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)
                        if not res or not res.get("ok"):
                            await api_call("deleteMessage", {"chat_id": uid, "message_id": mid}, token=BOT_TOKEN)
                            await api_call("sendMessage", {"chat_id": uid, "text": f"⭐️ **Secure Checkout**", "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)
                        await payments_col.update_one({"user_id": uid, "category": cat, "status": "pending"}, {"$set": {"message_id": mid, "days": int(d)}}, upsert=True)
                else:
                    kb = {"inline_keyboard": [[{"text": "USDT (BSC)", "callback_data": f"net_{cat}_{d}_BSC"}], [{"text": "USDT (TRX)", "callback_data": f"net_{cat}_{d}_Tron"}], [{"text": "🔙 Back", "callback_data": f"days_{cat}_{d}"}]]}
                    res = await api_call("editMessageText", {"chat_id": uid, "message_id": mid, "text": "🔗 **Select Network:**", "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)
                    if not res or not res.get("ok"):
                        await api_call("deleteMessage", {"chat_id": uid, "message_id": mid}, token=BOT_TOKEN)
                        await api_call("sendMessage", {"chat_id": uid, "text": "🔗 **Select Network:**", "parse_mode": "Markdown", "reply_markup": kb}, token=BOT_TOKEN)
                    
            elif data.startswith("net_"):
                _, cat, d, net = data.split("_")
                await api_call("deleteMessage", {"chat_id": uid, "message_id": mid}, token=BOT_TOKEN)
                wait_msg = await api_call("sendMessage", {"chat_id": uid, "text": "⏳ Generating Invoice..."}, token=BOT_TOKEN)
                
                invoice = await create_crypto_invoice(uid, cat, d, net)
                if invoice:
                    res = await api_call("sendPhoto", {"chat_id": uid, "photo": invoice['qr_code'], "caption": f"<b>Deposit {invoice['pay_amount']} USDT</b>\nNetwork: {net}\nAddress: <code>{invoice['address']}</code>", "parse_mode": "html"}, token=BOT_TOKEN)
                    if res and res.get("ok"):
                        await payments_col.update_one({"order_id": invoice['merchant_order_id']}, {"$set": {"message_id": res["result"]["message_id"]}})
                
                if wait_msg.get("result"): await api_call("deleteMessage", {"chat_id": uid, "message_id": wait_msg["result"]["message_id"]}, token=BOT_TOKEN)
            
            await api_call("answerCallbackQuery", {"callback_query_id": update["callback_query"]["id"]}, token=BOT_TOKEN)
    except Exception as e:
        logger.error(f"Error in process_update: {e}\n{traceback.format_exc()}")

async def process_payment_update(update):
    try:
        settings = await settings_col.find_one({"_id": "global_settings"}) or {}
        ptoken = settings.get("payment_bot_token", "").strip()
        if not ptoken: return

        if "pre_checkout_query" in update:
            await api_call("answerPreCheckoutQuery", {"pre_checkout_query_id": update["pre_checkout_query"]["id"], "ok": True}, token=ptoken)
            return

        if "message" in update and "successful_payment" in update["message"]:
            uid = update["message"]["from"]["id"]
            payload = update["message"]["successful_payment"]["invoice_payload"]
            parts = payload.split("_")
            cat = parts[1]
            d = parts[2]
            target_uid = int(parts[3]) if len(parts) > 3 else uid
            
            order_id = f"STAR_{target_uid}_{int(datetime.now().timestamp())}"
            pending = await payments_col.find_one_and_update({"user_id": target_uid, "category": cat, "status": "pending"}, {"$set": {"status": "completed", "order_id": order_id}})
            
            if pending and "message_id" in pending:
                await api_call("deleteMessage", {"chat_id": target_uid, "message_id": pending["message_id"]}, token=BOT_TOKEN)
            await api_call("deleteMessage", {"chat_id": uid, "message_id": update["message"]["message_id"]}, token=ptoken)
            
            asyncio.create_task(generate_vip_group(target_uid, cat, d))
    except Exception: pass

async def process_content_update(update, ctoken):
    try:
        if "message" in update:
            msg = update["message"]
            uid = msg["chat"]["id"]
            text = msg.get("text", "")
            
            admin_ids = await get_admin_ids()

            if uid in admin_ids:
                active_session = await bulk_sessions_col.find_one({"admin_id": uid, "active": True})

                if text.startswith("/bulkmodestart "):
                    cat = text.split(" ")[1]
                    await bulk_sessions_col.update_one({"admin_id": uid}, {"$set": {"category": cat, "active": True}}, upsert=True)
                    await api_call("sendMessage", {"chat_id": uid, "text": f"🟢 **Bulk Mode Started: `{cat}`**\n\nForward media to this bot. It will be saved and mirrored."}, token=ctoken)
                    return
                elif text == "/bulkmodeend":
                    await bulk_sessions_col.update_one({"admin_id": uid}, {"$set": {"active": False}})
                    await api_call("sendMessage", {"chat_id": uid, "text": "🔴 Bulk Mode Ended."}, token=ctoken)
                    return
                    
                elif active_session and not text.startswith("/"):
                    ctype = "text"
                    fid = None
                    text_content = msg.get("text") or msg.get("caption", "")

                    if "photo" in msg: ctype = "photo"; fid = msg["photo"][-1]["file_id"]
                    elif "video" in msg: ctype = "video"; fid = msg["video"]["file_id"]
                    elif "document" in msg: ctype = "document"; fid = msg["document"]["file_id"]

                    wait_msg = await api_call("sendMessage", {"chat_id": uid, "text": f"⚡ Fast Saving {ctype}..."}, token=ctoken)
                    wait_mid = wait_msg.get("result", {}).get("message_id")

                    asyncio.create_task(mirror_content_background(
                        admin_uid=uid, ctype=ctype, primary_file_id=fid, 
                        primary_token=ctoken, text_content=text_content, cat=active_session["category"],
                        wait_mid=wait_mid
                    ))
    except Exception as e: logger.error(f"Content process error: {e}")

# ==========================================
# AIOHTTP SERVER ROUTES
# ==========================================
async def handle_telegram_webhook(request):
    asyncio.create_task(process_update(await request.json()))
    return web.Response(text="OK")

async def handle_payment_webhook(request):
    asyncio.create_task(process_payment_update(await request.json()))
    return web.Response(text="OK")

async def handle_content_webhook(request):
    token = request.match_info.get('token')
    asyncio.create_task(process_content_update(await request.json(), token))
    return web.Response(text="OK")

async def handle_oxapay_webhook(request):
    try:
        data = await request.post() or await request.json()
        track_id = data.get("track_id") or data.get("trackId")
        order_id = data.get("order_id") or data.get("orderId")
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.oxapay.com/merchants/inquiry", json={"merchant": OXAPAY_KEY, "trackId": str(track_id)}) as resp:
                if (await resp.json()).get("status", "").lower() in ["paid", "success", "200"]:
                    payment = await payments_col.find_one({"order_id": order_id, "status": "pending"})
                    if payment:
                        await payments_col.update_one({"_id": payment["_id"]}, {"$set": {"status": "completed"}})
                        if "message_id" in payment:
                            await api_call("deleteMessage", {"chat_id": payment["user_id"], "message_id": payment["message_id"]}, token=BOT_TOKEN)
                        asyncio.create_task(generate_vip_group(payment["user_id"], payment["category"], payment["days"]))
        return web.Response(text="OK")
    except Exception: return web.Response(status=500)

async def start_background_tasks(app):
    await init_db()
    
    settings = await settings_col.find_one({"_id": "global_settings"}) or {}
    m_token = settings.get("bot_token", BOT_TOKEN).strip()
    p_token = settings.get("payment_bot_token", "").strip()
    backup_bots = settings.get("backup_bots", [])
    
    if m_token and ":" in m_token:
        await api_call("setWebhook", {"url": f"{WEBHOOK_URL}/webhook"}, token=m_token)
    if p_token and ":" in p_token: 
        await api_call("setWebhook", {"url": f"{WEBHOOK_URL}/payment_webhook"}, token=p_token)
        
    for bot in backup_bots:
        b_tok = bot.get("token", "").strip()
        if b_tok and ":" in b_tok:
            await api_call("setWebhook", {"url": f"{WEBHOOK_URL}/content_webhook/{b_tok}"}, token=b_tok)

if __name__ == '__main__':
    app = web.Application()
    
    app.router.add_post('/webhook', handle_telegram_webhook)
    app.router.add_post('/payment_webhook', handle_payment_webhook)
    app.router.add_post('/content_webhook/{token}', handle_content_webhook)
    app.router.add_post('/oxapay_callback', handle_oxapay_webhook)

    app.on_startup.append(start_background_tasks)
    logger.info(f"🚀 Main Bot logic online. Listening for webhooks on {PORT}...")
    web.run_app(app, host='0.0.0.0', port=PORT)

