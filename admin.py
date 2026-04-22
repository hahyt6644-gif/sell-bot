import os
import asyncio
import aiohttp
from aiohttp import web
import motor.motor_asyncio
import dns.resolver
from datetime import datetime
from bson import ObjectId

# Fix for Termux DNS issues
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4']

# --- CONFIGURATION (Environment Variables) ---
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "amit123")
PORT = int(os.environ.get("PORT", 8081))
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://amitprojects0")

# --- DB SETUP ---
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = mongo_client["vip_bot_db"]

products_col = db["products"]
subs_col = db["subscriptions"]
payments_col = db["payments"]
users_col = db["users"]
content_col = db["content"]
settings_col = db["settings"]
broadcast_col = db["broadcast_status"]
sessions_col = db["string_sessions"]  

# ==========================================
# DASHBOARD UI
# ==========================================
async def admin_dashboard(request):
    if request.query.get("pass") != ADMIN_PASSWORD:
        return web.Response(text="Unauthorized. Provide ?pass=", status=401)
    
    now_str = datetime.now().isoformat()
    total_users = await users_col.count_documents({"user_id": {"$gt": 0}})
    total_sales = await payments_col.count_documents({"status": "completed"})
    active_vips = await subs_col.count_documents({"expiry_at": {"$gt": now_str}})
    
    # --- FETCH SESSIONS ---
    sessions_list = await sessions_col.find().sort("added_at", -1).to_list(100)
    session_rows = ""
    active_sessions_count = 0
    for s in sessions_list:
        sid = str(s['_id'])
        is_active = s.get('active', False)
        status_html = "<span class='badge badge-green'>🟢 Active</span>" if is_active else "<span class='badge badge-danger'>🔴 Dead</span>"
        if is_active: active_sessions_count += 1
        err = s.get('error', 'None')
        added = s.get('added_at', '').split('T')[0] if s.get('added_at') else 'Unknown'
        session_rows += f"""<tr>
            <td><code>{sid[-6:]}</code></td>
            <td>{status_html}</td>
            <td>{added}</td>
            <td><small style='color:#64748b;'>{err}</small></td>
            <td>
                <form action='/delete_session?pass={ADMIN_PASSWORD}' method='POST' style='margin:0;' onsubmit="return confirm('Permanently delete this session?');">
                    <input type='hidden' name='session_id' value='{sid}'>
                    <button type='submit' class='btn-danger' style='padding:4px 8px;'><i class='fas fa-trash'></i></button>
                </form>
            </td>
        </tr>"""

    products = await products_col.find().to_list(100)
    prod_lookup = {}
    for p in products:
        p['content_count'] = await content_col.count_documents({"category": p['cat_key']})
        prod_lookup[p['cat_key']] = p

    settings = await settings_col.find_one({"_id": "global_settings"}) or {}
    bot_token = settings.get("bot_token", "")
    payment_bot_token = settings.get("payment_bot_token", "")
    payment_bot_username = settings.get("payment_bot_username", "")
    content_bot_token = settings.get("content_bot_token", "")
    content_bot_username = settings.get("content_bot_username", "")
    admin_ids_str = settings.get("admin_ids", "6931296977")
    
    backup_bots = settings.get("backup_bots", [])
    backup_bots_str = "\n".join([f"{b['token']}:{b['username']}" for b in backup_bots])
    
    users_list = await users_col.find({"user_id": {"$gt": 0}}).sort("last_active", -1).to_list(100)
    user_lookup = {u['user_id']: u for u in users_list}
    user_rows = ""
    for u in users_list: 
        uid = u.get('user_id', 'N/A')
        fname = u.get('first_name', 'Unknown')
        uname = f"@{u.get('username')}" if u.get('username') else "None"
        active = u.get('last_active', '').split('T')[0] if u.get('last_active') else 'Unknown'
        user_rows += f"<tr><td><div style='font-weight:600;'>{fname}</div><small style='color:#94a3b8;'><code>{uid}</code></small></td><td style='color:#0ea5e9; font-weight:500;'>{uname}</td><td style='color:#64748b;'>{active}</td></tr>"

    sales_list = await payments_col.find({"status": "completed"}).sort("_id", -1).to_list(100)
    sales_rows = ""
    for s in sales_list:
        uid = s.get('user_id', 'N/A')
        cat = s.get('category', 'N/A')
        days = str(s.get('days', 'N/A'))
        order = s.get('order_id', 'N/A')
        uname = f"@{user_lookup.get(uid, {}).get('username', 'Unknown')}"
        plan = prod_lookup.get(cat, {}).get('plans', {}).get(days, {})
        
        if "CRYP" in str(order).upper():
            price_display = f"<span style='color:#10b981; font-weight:bold;'>💎 ${plan.get('crypto_price', 0)} USDT</span>"
        else:
            price_display = f"<span style='color:#f59e0b; font-weight:bold;'>⭐️ {plan.get('star_price', 0)} Stars</span>"
        sales_rows += f"<tr><td><code>{uid}</code><br><small style='color:#0ea5e9; font-weight:500;'>{uname}</small></td><td><span class='badge badge-primary'>{cat}</span></td><td>{days} Days</td><td>{price_display}</td><td><code style='font-size:11px; color:#64748b;'>{order}</code></td></tr>"

    vip_list = await subs_col.find({"expiry_at": {"$gt": now_str}}).sort("expiry_at", 1).to_list(100)
    vip_rows = ""
    for v in vip_list:
        uid = v.get('user_id', 'N/A')
        cat = v.get('category', 'N/A')
        gid = v.get('group_id', 'N/A')
        exp = v.get('expiry_at', '').split('T')[0]
        sess = v.get('session_used', 'N/A').replace('.session', '')
        uname = f"@{user_lookup.get(uid, {}).get('username', 'Unknown')}"
        vip_rows += f"<tr><td><code>{uid}</code><br><small style='color:#0ea5e9; font-weight:500;'>{uname}</small></td><td><span class='badge badge-primary'>{cat}</span></td><td><code style='font-size:11px;'>{gid}</code></td><td><span class='badge badge-danger'>{exp}</span></td><td><span class='badge badge-secondary'><i class='fas fa-mobile-alt'></i> {sess}</span></td></tr>"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>VedaSell SaaS Admin</title>
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            :root {{ --primary: #0f172a; --sidebar: #1e293b; --secondary: #0ea5e9; --danger: #ef4444; --success: #10b981; --bg: #f8fafc; --text: #334155; }}
            *, *::before, *::after {{ box-sizing: border-box; }}
            body {{ font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); margin: 0; display: flex; height: 100vh; overflow: hidden; }}
            .sidebar {{ width: 260px; background: var(--sidebar); color: #cbd5e1; display: flex; flex-direction: column; overflow-y:auto; flex-shrink: 0; z-index: 100; }}
            .sidebar-header {{ padding: 25px 20px; font-size: 22px; font-weight: 700; color: white; text-align: center; background: var(--primary); }}
            .nav-item {{ padding: 16px 20px; margin: 8px 16px; cursor: pointer; transition: 0.2s; border-radius: 8px; font-weight: 500; display: flex; align-items: center; }}
            .nav-item:hover {{ background: rgba(255,255,255,0.05); color: white; }}
            .nav-item.active {{ background: var(--secondary); color: white; box-shadow: 0 4px 12px rgba(14, 165, 233, 0.3); }}
            .nav-item i {{ margin-right: 12px; font-size: 18px; width: 20px; text-align: center; }}
            .main-content {{ flex: 1; padding: 30px; overflow-y: auto; background-color: var(--bg); position: relative; }}
            .tab-pane {{ display: none; }}
            .tab-pane.active {{ display: block; animation: fadeIn 0.4s ease; }}
            @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
            .card {{ background: white; padding: 25px; border-radius: 16px; border: 1px solid #e2e8f0; margin-bottom: 25px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
            input, textarea, button, select {{ font-family: 'Inter', sans-serif; padding: 12px 16px; margin: 8px 0; width: 100%; border-radius: 8px; border: 1px solid #cbd5e1; font-size: 14px; transition: 0.2s; background:white; }}
            input:focus, textarea:focus, select:focus {{ outline: none; border-color: var(--secondary); box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.2); }}
            .btn-primary {{ background: var(--secondary); color: white; border: none; cursor: pointer; font-weight: 600; width: auto; display: inline-block; text-align: center; text-decoration: none; }}
            .btn-danger {{ background: white; color: var(--danger); border: 1px solid #fca5a5; cursor: pointer; font-weight: 600; width: auto; padding: 8px 16px; border-radius: 6px; }}
            .btn-green {{ background: #10b981; color: white; border: none; cursor: pointer; font-weight: 600; width: auto; padding: 8px 16px; border-radius: 6px; }}
            table {{ width: 100%; border-collapse: collapse; min-width: 600px; }}
            th {{ background: #f8fafc; padding: 14px 20px; text-align: left; font-size: 12px; text-transform: uppercase; color: #64748b; border-bottom: 1px solid #e2e8f0; }}
            td {{ padding: 16px 20px; border-bottom: 1px solid #f1f5f9; font-size: 14px; }}
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 24px; margin-bottom: 30px; }}
            .stat-card {{ background: white; padding: 24px; border-radius: 16px; border: 1px solid #e2e8f0; display: flex; align-items: center; cursor: pointer; transition: 0.2s; }}
            .stat-card:hover {{ border-color: var(--secondary); transform: translateY(-2px); }}
            .stat-icon {{ width: 56px; height: 56px; border-radius: 16px; display: flex; align-items: center; justify-content: center; font-size: 24px; margin-right: 20px; }}
            .stat-value {{ font-size: 28px; font-weight: 800; color: var(--primary); margin: 0; line-height: 1.2; }}
            .stat-label {{ color: #64748b; margin: 0; font-size: 14px; font-weight: 500; }}
            .badge {{ display: inline-block; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd; }}
            .badge-green {{ background: #dcfce3; color: #166534; border: 1px solid #bbf7d0; }}
            .badge-danger {{ background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }}
            code {{ background: #f1f5f9; padding: 3px 6px; border-radius: 4px; font-family: monospace; color: #334155; }}
            .progress-container {{ width: 100%; background-color: #f1f5f9; border-radius: 8px; margin-top: 15px; overflow: hidden; display: none; border: 1px solid #e2e8f0; }}
            .progress-bar {{ width: 0%; height: 24px; background-color: var(--success); transition: width 0.3s ease; }}
            .progress-text {{ margin-top: 8px; font-size: 14px; font-weight: 600; color: #475569; }}
            .plan-form {{ display: flex; flex-wrap: wrap; gap: 10px; background: #f8fafc; padding: 15px; border-radius: 12px; align-items: center; }}
            .plan-form input {{ flex: 1 1 100px; margin: 0; min-width: 80px; }}
            .broadcast-flex {{ display:flex; gap:10px; align-items: center; }}
            
            /* MODAL STYLES REWRITTEN FOR PINNED HEADER */
            .modal-overlay {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 2000; align-items: center; justify-content: center; }}
            .modal-content {{ background: white; border-radius: 16px; width: 90%; max-width: 800px; max-height: 85vh; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.1); }}
            .modal-header {{ padding: 20px 25px; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center; background: white; }}
            .modal-body {{ padding: 20px 25px; overflow-y: auto; flex: 1; }}

            @media (max-width: 768px) {{
                body {{ display: block; overflow-x: hidden; overflow-y: auto; padding-bottom: 80px; }}
                .sidebar {{ position: fixed; bottom: 0; left: 0; width: 100%; height: 70px; flex-direction: row; padding: 0; z-index: 1000; box-shadow: 0 -4px 15px rgba(0,0,0,0.1); background: var(--primary); justify-content: space-around; align-items: center; }}
                .sidebar-header {{ display: none; }}
                .nav-item {{ margin: 0; padding: 8px; flex: 1; border-radius: 0; flex-direction: column; justify-content: center; height: 100%; }}
                .nav-item i {{ margin: 0 0 4px 0; font-size: 20px; }}
                .nav-item span {{ font-size: 10px; text-align: center; display: block; }}
                .main-content {{ padding: 15px; overflow: visible; }}
                .plan-form {{ flex-direction: column; align-items: stretch; }}
                .plan-form input, .plan-form button {{ max-width: 100% !important; }}
                .broadcast-flex {{ flex-direction: column; }}
            }}
        </style>
    </head>
    <body>
        <div class="sidebar">
            <div class="sidebar-header">VedaSell Admin</div>
            <div id="nav-dashboard" class="nav-item active" onclick="switchTab('dashboard', this)"><i class="fas fa-chart-pie"></i> <span>Overview</span></div>
            <div id="nav-users" class="nav-item" onclick="switchTab('users', this)"><i class="fas fa-users"></i> <span>Users</span></div>
            <div id="nav-sessions" class="nav-item" onclick="switchTab('sessions', this)"><i class="fas fa-mobile-alt"></i> <span>Sessions</span></div>
            <div id="nav-sales" class="nav-item" onclick="switchTab('sales', this)"><i class="fas fa-receipt"></i> <span>Sales & VIPs</span></div>
            <div id="nav-products" class="nav-item" onclick="switchTab('products', this)"><i class="fas fa-layer-group"></i> <span>Products</span></div>
            <div id="nav-config" class="nav-item" onclick="switchTab('config', this)"><i class="fas fa-cogs"></i> <span>Config & Blast</span></div>
        </div>

        <div class="main-content">
            
            <div id="dashboard" class="tab-pane active">
                <h2>Overview Dashboard</h2>
                <div class="stats-grid">
                    <div class="stat-card" onclick="switchTab('users', document.getElementById('nav-users'))">
                        <div class="stat-icon" style="background: #e0f2fe; color: var(--secondary);"><i class="fas fa-users"></i></div>
                        <div><p class="stat-value">{total_users}</p><p class="stat-label">Total Bot Users</p></div>
                    </div>
                    <div class="stat-card" onclick="switchTab('sales', document.getElementById('nav-sales'))">
                        <div class="stat-icon" style="background: #dcfce3; color: var(--success);"><i class="fas fa-wallet"></i></div>
                        <div><p class="stat-value">{total_sales}</p><p class="stat-label">Completed Sales</p></div>
                    </div>
                    <div class="stat-card" onclick="switchTab('sessions', document.getElementById('nav-sessions'))">
                        <div class="stat-icon" style="background: #fef3c7; color: #d97706;"><i class="fas fa-mobile-alt"></i></div>
                        <div><p class="stat-value">{active_sessions_count}</p><p class="stat-label">Active Sessions</p></div>
                    </div>
                </div>
            </div>
            
            <div id="users" class="tab-pane">
                <h2>👥 User Database</h2>
                <div class="card" style="padding:0; overflow:hidden;">
                    <div class="table-wrapper" style="border:none; max-height: 600px; overflow-y:auto;">
                        <table>
                            <tr><th>User Info</th><th>Username</th><th>Last Active</th></tr>
                            {user_rows if user_rows else "<tr><td colspan='3' style='text-align:center; padding:30px;'>No users found.</td></tr>"}
                        </table>
                    </div>
                </div>
            </div>

            <div id="sessions" class="tab-pane">
                <h2>📱 String Session Management</h2>
                <p style="color:#64748b; margin-bottom:20px;">Sessions are uploaded and converted directly in the Main Bot via the <code>/sessions</code> command. Manage them below.</p>
                <div class="card" style="padding:0; overflow:hidden;">
                    <div class="table-wrapper" style="border:none; max-height: 600px; overflow-y:auto;">
                        <table>
                            <tr><th>Session ID</th><th>Status</th><th>Added On</th><th>Error Log</th><th>Action</th></tr>
                            {session_rows if session_rows else "<tr><td colspan='5' style='text-align:center; padding:30px;'>No string sessions found in database.</td></tr>"}
                        </table>
                    </div>
                </div>
            </div>

            <div id="sales" class="tab-pane">
                <h2>Completed Sales</h2>
                <div class="card" style="padding:0; overflow:hidden; margin-bottom: 30px;">
                    <div class="table-wrapper" style="border:none; max-height: 400px; overflow-y:auto;">
                        <table>
                            <tr><th>Customer</th><th>Category</th><th>Duration</th><th>Paid</th><th>Order Ref</th></tr>
                            {sales_rows if sales_rows else "<tr><td colspan='5' style='text-align:center; padding:30px;'>No sales yet.</td></tr>"}
                        </table>
                    </div>
                </div>
                <h2>Active VIP Members</h2>
                <div class="card" style="padding:0; overflow:hidden;">
                    <div class="table-wrapper" style="border:none; max-height: 400px; overflow-y:auto;">
                        <table>
                            <tr><th>Member</th><th>Tier</th><th>Group ID</th><th>Expires On</th><th>Session</th></tr>
                            {vip_rows if vip_rows else "<tr><td colspan='5' style='text-align:center; padding:30px;'>No active VIPs.</td></tr>"}
                        </table>
                    </div>
                </div>
            </div>

            <div id="products" class="tab-pane">
                <div style="display:flex; justify-content:space-between; margin-bottom:20px;">
                    <h2 style="margin:0;">Products & Content</h2>
                    <button class="btn-primary" onclick="switchTab('add_prod', null)"><i class="fas fa-plus"></i> New Category</button>
                </div>
    """
    for p in products:
        html += f"""
        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                    <h3 style="margin:0;">{p.get('name', '')} <span class="badge">{p.get('content_count', 0)} Files</span></h3>
                    <p style="margin:5px 0 15px 0; color:#64748b;">ID: <code>{p['cat_key']}</code></p>
                </div>
                <div style="display:flex; gap:10px;">
                    <button class="btn-green" onclick="openContentModal('{p['cat_key']}')"><i class="fas fa-list"></i> Content</button>
                    <form action="/delete_product?pass={ADMIN_PASSWORD}" method="POST" onsubmit="return confirm('Delete entirely?');" style="margin:0;">
                        <input type="hidden" name="cat_key" value="{p['cat_key']}">
                        <button type="submit" class="btn-danger"><i class="fas fa-trash"></i></button>
                    </form>
                </div>
            </div>
            <div style="overflow-x:auto;">
                <table>
        """
        for days, plan in p.get('plans', {}).items():
            html += f"""
                <tr>
                    <td>{plan.get('label', '')} ({days}d)</td>
                    <td>⭐️ {plan.get('star_price', 0)}</td>
                    <td style="color:#10b981; font-weight:bold;">${plan.get('crypto_price', 0)}</td>
                    <td style="text-align:right;">
                        <form action="/delete_plan?pass={ADMIN_PASSWORD}" method="POST" style="margin:0;">
                            <input type="hidden" name="cat_key" value="{p['cat_key']}">
                            <input type="hidden" name="days" value="{days}">
                            <button type="submit" class="btn-danger" style="padding:4px 8px;"><i class="fas fa-times"></i></button>
                        </form>
                    </td>
                </tr>
            """
        html += f"""
                </table>
            </div>
            <form action="/add_plan?pass={ADMIN_PASSWORD}" method="POST" class="plan-form" style="margin-top:15px;">
                <input type="hidden" name="cat_key" value="{p['cat_key']}">
                <input type="number" name="days" placeholder="Days" required>
                <input type="text" name="label" placeholder="Btn Text" required>
                <input type="number" name="star_price" placeholder="Stars" required>
                <input type="number" step="0.01" name="crypto_price" placeholder="USDT" required>
                <button type="submit" class="btn-primary"><i class="fas fa-plus"></i></button>
            </form>
        </div>
        """

    html += f"""
            </div>

            <div id="add_prod" class="tab-pane">
                <h2>Create Category</h2>
                <div class="card" style="max-width: 600px;">
                    <form action="/add_product?pass={ADMIN_PASSWORD}" method="POST">
                        <label>System ID (No spaces)</label>
                        <input type="text" name="cat_key" required>
                        <label>Display Name</label>
                        <input type="text" name="name" required>
                        <label>Description</label>
                        <textarea name="description" rows="3"></textarea>
                        <label>Image URL (Optional)</label>
                        <input type="text" name="image">
                        <button type="submit" class="btn-primary" style="margin-top: 15px;"><i class="fas fa-check"></i> Create</button>
                    </form>
                </div>
            </div>

            <div id="config" class="tab-pane">
                <h2><i class="fas fa-cogs"></i> System & Broadcast</h2>
                
                <div class="card">
                    <h3 style="margin-top:0;">Global Bot Configuration</h3>
                    <form action="/save_settings?pass={ADMIN_PASSWORD}" method="POST">
                        <label>Admin Telegram IDs (Comma separated)</label>
                        <input type="text" name="admin_ids" value="{admin_ids_str}">
                        
                        <label>Main Catalog Bot Token</label>
                        <input type="password" name="bot_token" value="{bot_token}">
                        
                        <div class="broadcast-flex" style="margin-top:10px;">
                            <div style="flex:1;">
                                <label>Stars Payment Bot Token</label>
                                <input type="password" name="payment_bot_token" value="{payment_bot_token}">
                            </div>
                            <div style="flex:1;">
                                <label>Stars Payment Bot Username (No @)</label>
                                <input type="text" name="payment_bot_username" value="{payment_bot_username}">
                            </div>
                        </div>

                        <div class="broadcast-flex" style="margin-top:10px;">
                            <div style="flex:1;">
                                <label>ACTIVE Content Uploader Bot Token</label>
                                <p style="font-size:11px; margin:0; color:var(--danger)">This bot will post into the VIP groups.</p>
                                <input type="password" name="content_bot_token" value="{content_bot_token}">
                            </div>
                            <div style="flex:1;">
                                <label>ACTIVE Content Uploader Bot Username</label>
                                <input type="text" name="content_bot_username" value="{content_bot_username}" placeholder="No @">
                            </div>
                        </div>

                        <label style="margin-top:15px; display:block;">Backup/Mirror Bots (Storage Only)</label>
                        <p style="font-size:12px; margin:0;">Format: <code>TOKEN:USERNAME</code>. Used to generate backup file_ids.</p>
                        <textarea name="backup_bots" rows="3">{backup_bots_str}</textarea>
                        
                        <button type="submit" class="btn-primary" style="margin-top:15px; width:100%;"><i class="fas fa-save"></i> Save Settings</button>
                    </form>
                </div>

                <div class="card">
                    <h3 style="margin-top:0;">📢 Marketing Broadcast</h3>
                    <form id="broadcast-form" action="/send_broadcast?pass={ADMIN_PASSWORD}" method="POST" onsubmit="startBroadcastUi();">
                        <textarea name="message" placeholder="Type your message... (Markdown)" rows="4" required></textarea>
                        <div class="broadcast-flex" style="margin-top:10px;">
                            <select name="media_type" style="width:120px; margin:0;">
                                <option value="text">No Media</option><option value="photo">Photo</option><option value="video">Video</option>
                            </select>
                            <input type="text" name="media_url" placeholder="Direct Media URL" style="flex:1; margin:0;">
                        </div>
                        <div id="btn-container" style="margin-top:10px;">
                            <div class="broadcast-flex btn-row" style="margin-bottom:10px;">
                                <input type="text" name="btn_text" placeholder="Button Text" style="flex:1; margin:0;">
                                <input type="text" name="btn_url" placeholder="Button URL" style="flex:1; margin:0;">
                                <button type="button" class="btn-danger" style="padding:12px; margin:0;" onclick="this.parentElement.remove()"><i class="fas fa-trash"></i></button>
                            </div>
                        </div>
                        <button type="button" class="btn-primary" style="padding:8px; font-size:12px;" onclick="addBtnRow()"><i class="fas fa-plus"></i> Add Button</button>
                        <button type="submit" class="btn-primary" style="margin-top:15px; width:100%; padding:12px;"><i class="fas fa-paper-plane"></i> Blast to {total_users} Users</button>
                    </form>
                    <div class="progress-container" id="progress-container">
                        <div class="progress-bar" id="progress-bar"></div>
                    </div>
                    <div class="progress-text" id="progress-text"></div>
                </div>

            </div>
        </div>

        <div id="content-modal" class="modal-overlay">
            <div class="modal-content">
                <div class="modal-header">
                    <h3 style="margin:0; color:var(--primary);"><i class="fas fa-tasks"></i> Content Manager: <span id="modal-cat-title"></span></h3>
                    <i class="fas fa-times" style="cursor: pointer; font-size: 24px; color: #64748b; transition: color 0.2s;" onmouseover="this.style.color='#ef4444'" onmouseout="this.style.color='#64748b'" onclick="closeContentModal()"></i>
                </div>
                <div class="modal-body">
                    <div class="table-wrapper">
                        <table>
                            <tr><th>Type</th><th>Preview</th><th>Mirrors</th><th>Action</th></tr>
                            <tbody id="content-table-body"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <script>
            function switchTab(tabId, element) {{
                document.querySelectorAll('.tab-pane').forEach(t => t.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                if(element) {{
                    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
                    element.classList.add('active');
                }}
            }}

            function openContentModal(catKey) {{
                document.getElementById('content-modal').style.display = 'flex';
                document.getElementById('modal-cat-title').innerText = catKey;
                fetch(`/api/content?cat_key=${{catKey}}&pass={ADMIN_PASSWORD}`)
                .then(r => r.json())
                .then(data => {{
                    let html = '';
                    if(data.length === 0) html = '<tr><td colspan="4" style="text-align:center;">Empty</td></tr>';
                    data.forEach(item => {{
                        html += `<tr>
                            <td><span class="badge">${{item.type}}</span></td>
                            <td><code>${{item.preview}}</code></td>
                            <td><span class="badge badge-green">${{item.mirrors}} Backups</span></td>
                            <td><button class="btn-danger" style="padding:4px 8px;" onclick="deleteContent('${{item.id}}', '${{catKey}}')"><i class="fas fa-trash"></i></button></td>
                        </tr>`;
                    }});
                    document.getElementById('content-table-body').innerHTML = html;
                }});
            }}

            function closeContentModal() {{ document.getElementById('content-modal').style.display = 'none'; }}
            
            function deleteContent(id, catKey) {{
                if(!confirm("Permanently delete this piece of content?")) return;
                fetch(`/api/delete_content?pass={ADMIN_PASSWORD}`, {{
                    method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{id: id}})
                }}).then(() => openContentModal(catKey)); 
            }}

            function checkBroadcastStatus() {{
                fetch('/api/broadcast_status?pass={ADMIN_PASSWORD}')
                    .then(response => response.json())
                    .then(data => {{
                        if (data.status === 'running' || data.status === 'completed') {{
                            document.getElementById('progress-container').style.display = 'block';
                            let total = data.total || 1;
                            let processed = (data.sent || 0) + (data.failed || 0);
                            let percent = Math.min(100, Math.round((processed / total) * 100));
                            document.getElementById('progress-bar').style.width = percent + '%';
                            document.getElementById('progress-text').innerHTML = `🔄 ${{percent}}% &nbsp;&bull;&nbsp; ✅ ${{data.sent}} &nbsp;&bull;&nbsp; ❌ ${{data.failed}}`;
                            if (data.status === 'completed') setTimeout(() => document.getElementById('progress-container').style.display = 'none', 5000);
                            else setTimeout(checkBroadcastStatus, 1000);
                        }}
                    }});
            }}

            function addBtnRow() {{
                const container = document.getElementById('btn-container');
                const div = document.createElement('div');
                div.className = 'broadcast-flex btn-row';
                div.style.marginBottom = '10px';
                div.innerHTML = `<input type="text" name="btn_text" placeholder="Btn Text" style="flex:1; margin:0;"><input type="text" name="btn_url" placeholder="URL" style="flex:1; margin:0;"><button type="button" class="btn-danger" style="padding:12px; margin:0;" onclick="this.parentElement.remove()"><i class="fas fa-trash"></i></button>`;
                container.appendChild(div);
            }}

            checkBroadcastStatus();
            function startBroadcastUi() {{ setTimeout(checkBroadcastStatus, 1500); return true; }}
            window.onload = function() {{
                if(window.location.hash) {{
                    let tab = window.location.hash.substring(1);
                    if(document.getElementById(tab)) switchTab(tab, document.getElementById('nav-'+(tab==='add_prod'?'products':tab)));
                }}
            }};
        </script>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

# ==========================================
# ADMIN API LOGIC
# ==========================================
async def admin_save_settings(request):
    data = await request.post()
    backup_bots_raw = data.get("backup_bots", "").strip().split('\n')
    backup_bots = []
    for line in backup_bots_raw:
        if ":" in line:
            # Splits right so that Telegram bot tokens (which contain a colon) are parsed safely
            parts = line.rsplit(":", 1)
            if len(parts) == 2:
                backup_bots.append({"token": parts[0].strip(), "username": parts[1].strip().replace("@", "")})

    await settings_col.update_one({"_id": "global_settings"}, {"$set": {
        "admin_ids": data.get("admin_ids", "6931296977").strip(),
        "bot_token": data.get("bot_token", "").strip(),
        "payment_bot_token": data.get("payment_bot_token", "").strip(),
        "payment_bot_username": data.get("payment_bot_username", "").strip().replace('@', ''),
        "content_bot_token": data.get("content_bot_token", "").strip(),
        "content_bot_username": data.get("content_bot_username", "").strip().replace('@', ''),
        "backup_bots": backup_bots
    }}, upsert=True)
    
    return web.HTTPFound(f"/?pass={ADMIN_PASSWORD}#config")

async def background_broadcast(bot_token, users, message, media_type, media_url, btn_texts, btn_urls):
    total = len(users)
    await broadcast_col.update_one({"_id": "current"}, {"$set": {"status": "running", "total": total, "sent": 0, "failed": 0}}, upsert=True)
    endpoint = "sendMessage"
    if media_type == "photo" and media_url: endpoint = "sendPhoto"
    elif media_type == "video" and media_url: endpoint = "sendVideo"
    url = f"https://api.telegram.org/bot{bot_token}/{endpoint}"
    base_payload = {"parse_mode": "Markdown"}
    
    inline_keyboard = []
    for t, u in zip(btn_texts, btn_urls):
        if t.strip() and u.strip(): inline_keyboard.append([{"text": t.strip(), "url": u.strip()}])
    if inline_keyboard: base_payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
        
    sent, failed = 0, 0
    async with aiohttp.ClientSession() as session:
        for index, u in enumerate(users):
            uid = u.get("user_id")
            if uid:
                payload = base_payload.copy()
                payload["chat_id"] = uid
                if endpoint == "sendMessage": payload["text"] = message
                else: payload[media_type] = media_url; payload["caption"] = message
                try:
                    async with session.post(url, json=payload) as resp:
                        if resp.status == 200: sent += 1
                        else: failed += 1
                except: failed += 1
            if index % 10 == 0: await broadcast_col.update_one({"_id": "current"}, {"$set": {"sent": sent, "failed": failed}})
            await asyncio.sleep(0.05) 
    await broadcast_col.update_one({"_id": "current"}, {"$set": {"status": "completed", "sent": sent, "failed": failed}})

async def admin_send_broadcast(request):
    data = await request.post()
    settings = await settings_col.find_one({"_id": "global_settings"}) or {}
    bot_token = settings.get("bot_token", "")
    if not bot_token: return web.Response(text="Error: Save Main Bot Token!")
    users = await users_col.find({"user_id": {"$gt": 0}}).to_list(None)
    asyncio.create_task(background_broadcast(bot_token, users, data.get("message", ""), data.get("media_type", "text"), data.get("media_url", "").strip(), data.getall("btn_text", []), data.getall("btn_url", [])))
    return web.HTTPFound(f"/?pass={ADMIN_PASSWORD}#config")

async def get_broadcast_status(request):
    if request.query.get("pass") != ADMIN_PASSWORD: return web.json_response({}, status=401)
    status = await broadcast_col.find_one({"_id": "current"}) or {}
    return web.json_response({"status": status.get("status", "idle"), "total": status.get("total", 0), "sent": status.get("sent", 0), "failed": status.get("failed", 0)})

async def api_get_content(request):
    if request.query.get("pass") != ADMIN_PASSWORD: return web.json_response([], status=401)
    contents = await content_col.find({"category": request.query.get("cat_key")}).sort("added_at", -1).to_list(1000)
    res = []
    for c in contents:
        preview = c.get("text", "")
        if not preview and c.get("mirrors"): preview = c["mirrors"][0].get("file_id", "")[:15] + "..."
        elif not preview: preview = c.get("file_id", "")[:15] + "..."
        res.append({"id": str(c["_id"]), "type": c.get("type", "unknown"), "preview": preview, "mirrors": len(c.get("mirrors", []))})
    return web.json_response(res)

async def api_delete_content(request):
    if request.query.get("pass") != ADMIN_PASSWORD: return web.json_response({}, status=401)
    data = await request.json()
    await content_col.delete_one({"_id": ObjectId(data.get("id"))})
    return web.json_response({"status": "ok"})

# --- SESSION API LOGIC ---
async def admin_delete_session(request):
    data = await request.post()
    session_id = data.get("session_id")
    if session_id:
        await sessions_col.delete_one({"_id": ObjectId(session_id)})
    return web.HTTPFound(f"/?pass={ADMIN_PASSWORD}#sessions")

async def admin_add_product(request):
    data = await request.post()
    await products_col.insert_one({"cat_key": data['cat_key'], "name": data['name'], "description": data['description'], "image": data['image'], "plans": {}})
    return web.HTTPFound(f"/?pass={ADMIN_PASSWORD}#products")

async def admin_delete_product(request):
    data = await request.post()
    await products_col.delete_one({"cat_key": data['cat_key']})
    return web.HTTPFound(f"/?pass={ADMIN_PASSWORD}#products")

async def admin_add_plan(request):
    data = await request.post()
    await products_col.update_one({"cat_key": data['cat_key']}, {"$set": {f"plans.{str(data['days'])}": {"label": data['label'], "star_price": int(data['star_price']), "crypto_price": float(data['crypto_price'])}}})
    return web.HTTPFound(f"/?pass={ADMIN_PASSWORD}#products")

async def admin_delete_plan(request):
    data = await request.post()
    await products_col.update_one({"cat_key": data['cat_key']}, {"$unset": {f"plans.{data['days']}": ""}})
    return web.HTTPFound(f"/?pass={ADMIN_PASSWORD}#products")

if __name__ == '__main__':
    app = web.Application()
    app.router.add_get('/', admin_dashboard)
    app.router.add_post('/add_product', admin_add_product)
    app.router.add_post('/delete_product', admin_delete_product)
    app.router.add_post('/add_plan', admin_add_plan)
    app.router.add_post('/delete_plan', admin_delete_plan)
    app.router.add_post('/save_settings', admin_save_settings)
    app.router.add_post('/send_broadcast', admin_send_broadcast)
    app.router.add_post('/delete_session', admin_delete_session) # <--- NEW ROUTE
    app.router.add_get('/api/broadcast_status', get_broadcast_status)
    app.router.add_get('/api/content', api_get_content)
    app.router.add_post('/api/delete_content', api_delete_content)
    
    print(f"🚀 Admin Panel running on port {PORT}")
    web.run_app(app, host='0.0.0.0', port=PORT)
