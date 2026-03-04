import discord
from discord.ext import commands
from discord import Embed, Color, ButtonStyle, Interaction
from discord.ui import View, Button
import os
import json
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import threading
import aiohttp
from dotenv import load_dotenv
import re
from typing import Optional, List, Dict, Any, Union
import uuid
import logging
import traceback

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS', '').split(',') if os.getenv('AUTHORIZED_USERS') else []
API_KEY = os.getenv('API_KEY', 'R6X-SECURE-KEY-CHANGE-ME-NOW')
RENDER_URL = os.getenv('RENDER_URL', 'https://bot-hosting-b-ga04.onrender.com')
STEAM_API_KEY = os.getenv('STEAM_API_KEY', '')  # Optional

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("r6x-bot")

# ==================== FASTAPI APP ====================
fastapi_app = FastAPI(
    title="R6X XScan API", 
    version="1.0.0",
    description="API for R6X XScan Discord Bot"
)

# Add CORS middleware
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== PYDANTIC MODELS ====================
class ThreatModel(BaseModel):
    name: str
    severity: int
    path: str
    time: str

class FileModel(BaseModel):
    exe_files: List[str] = []
    rar_files: List[str] = []
    suspicious: List[str] = []
    exe_count: int = 0
    rar_count: int = 0
    sus_count: int = 0

class GameBansModel(BaseModel):
    rainbow_six: List[str] = []
    steam: List[str] = []

class PrefetchModel(BaseModel):
    name: str
    last_accessed: str

class LogitechScriptModel(BaseModel):
    path: str
    modified: str

class HardwareMonitorModel(BaseModel):
    name: str
    serial: str

class HardwarePCIAModel(BaseModel):
    name: str
    status: str

class HardwareModel(BaseModel):
    monitors: List[HardwareMonitorModel] = []
    pcie_devices: List[HardwarePCIAModel] = []

class SystemInfoModel(BaseModel):
    install_date: str = "Unknown"
    secure_boot: str = "Unknown"
    dma_protection: str = "Unknown"

class SecurityModel(BaseModel):
    antivirus_enabled: bool = False
    antivirus_list: List[str] = []
    defender_enabled: bool = False
    realtime: bool = False
    firewall: bool = False

class ScanData(BaseModel):
    scan_id: str
    user_id: str
    timestamp: str
    system_info: SystemInfoModel
    security: SecurityModel
    threats: List[ThreatModel] = []
    files: FileModel
    executed_programs: List[str] = []
    game_bans: GameBansModel
    prefetch: List[PrefetchModel] = []
    logitech_scripts: List[LogitechScriptModel] = []
    hardware: HardwareModel

class ScanResponse(BaseModel):
    status: str
    message: str
    scan_id: Optional[str] = None

class HealthResponse(BaseModel):
    status: str
    active_scans: int
    total_scans: int
    authorized_users: int
    uptime: str
    bot_status: str

# ==================== DISCORD BOT SETUP ====================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Remove default help command
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ==================== DATA STORAGE ====================
active_scans = {}
scan_history = {}
user_stats = {}
bot_start_time = datetime.now()

# ==================== HELPER FUNCTIONS ====================
def format_size(bytes):
    """Format file size nicely"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024.0:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.1f} TB"

def truncate_string(text, max_length=1000):
    """Truncate string to max length"""
    if len(text) > max_length:
        return text[:max_length-3] + "..."
    return text

def parse_threat_severity(severity):
    """Convert severity ID to readable format"""
    severity_map = {
        1: "Low",
        2: "Medium", 
        3: "High",
        4: "Severe",
        5: "Critical"
    }
    return severity_map.get(severity, "Unknown")

# ==================== DISCORD BOT EVENTS ====================
@bot.event
async def on_ready():
    logger.info(f'✅ R6X XScan Bot is online as {bot.user.name}')
    logger.info(f'📊 Bot ID: {bot.user.id}')
    logger.info(f'📋 Channel ID: {CHANNEL_ID}')
    
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ **R6X XScan Bot is now online and ready!**")
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="R6X Scans | !scan | !help"
        )
    )

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.NotOwner):
        await ctx.send("❌ This command is owner only.")
    else:
        await ctx.send(f"❌ An error occurred: {str(error)}")
        logger.error(f"Command error: {error}")
        logger.error(traceback.format_exc())

# ==================== DISCORD COMMANDS ====================

@bot.command(name='help')
async def help_command(ctx):
    """Show help menu"""
    embed = Embed(
        title="📚 R6X XScan Help Menu",
        description="Welcome to R6X XScan - Advanced System Security Scanner",
        color=Color.blue()
    )
    
    embed.add_field(
        name="🔍 **Scan Commands**",
        value="`!scan` - Start a new scan session\n"
              "`!status <scan_id>` - Check scan status\n"
              "`!cancel <scan_id>` - Cancel pending scan",
        inline=False
    )
    
    embed.add_field(
        name="📊 **Information Commands**",
        value="`!stats [user]` - Show scan statistics\n"
              "`!recent [limit]` - Show recent scans\n"
              "`!ping` - Check bot latency",
        inline=False
    )
    
    embed.add_field(
        name="⚙️ **Admin Commands**",
        value="`!adduser <user_id>` - Add authorized user\n"
              "`!removeuser <user_id>` - Remove authorized user\n"
              "`!listusers` - List authorized users\n"
              "`!broadcast <message>` - Broadcast to channel",
        inline=False
    )
    
    embed.add_field(
        name="📝 **How to Use**",
        value="1. Type `!scan` to get a scan ID\n"
              "2. Run the Python scanner with your Discord ID\n"
              "3. Wait for results to appear here\n"
              "4. You'll be pinged when scan completes",
        inline=False
    )
    
    embed.set_footer(text="R6X XScan v1.0 | Made for security professionals")
    
    view = View()
    view.add_item(Button(label="📖 GitHub", url="https://github.com/", style=ButtonStyle.link))
    view.add_item(Button(label="🆘 Support", url="https://discord.gg/", style=ButtonStyle.link))
    
    await ctx.send(embed=embed, view=view)

@bot.command(name='ping')
async def ping_command(ctx):
    """Check bot latency"""
    latency = round(bot.latency * 1000)
    embed = Embed(
        title="🏓 Pong!",
        description=f"Latency: **{latency}ms**",
        color=Color.green()
    )
    await ctx.send(embed=embed)

@bot.command(name='scan')
async def start_scan(ctx):
    """Start a new scan session"""
    # Check authorization
    if str(ctx.author.id) not in AUTHORIZED_USERS and not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ You are not authorized to use this command.")
        return
    
    # Generate scan ID
    scan_id = f"R6X-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{ctx.author.id}"
    
    # Store scan session
    active_scans[scan_id] = {
        'user_id': ctx.author.id,
        'user_name': str(ctx.author),
        'user_mention': ctx.author.mention,
        'start_time': datetime.now(),
        'data': None,
        'message_id': None,
        'status': 'pending',
        'steps_completed': []
    }
    
    # Update user stats
    if str(ctx.author.id) not in user_stats:
        user_stats[str(ctx.author.id)] = {
            'scans': 0,
            'last_scan': None,
            'threats_found': 0,
            'successful_scans': 0
        }
    user_stats[str(ctx.author.id)]['last_scan'] = datetime.now().isoformat()
    user_stats[str(ctx.author.id)]['scans'] += 1
    
    # Create embed with instructions
    embed = Embed(
        title="🔍 R6X XScan - New Scan Session",
        description=f"Scan initiated by {ctx.author.mention}",
        color=Color.blue(),
        timestamp=datetime.now()
    )
    
    embed.add_field(
        name="🆔 Scan ID",
        value=f"```{scan_id}```",
        inline=False
    )
    
    embed.add_field(
        name="📋 Instructions",
        value="1. **Download the Python scanner**\n"
              "2. **Install requirements:** `pip install requests`\n"
              "3. **Run this command:**",
        inline=False
    )
    
    # Python command
    py_command = f'python r6x_scanner.py -ScanID "{scan_id}" -UserID "{ctx.author.id}"'
    embed.add_field(
        name="🐍 Python Command",
        value=f"```bash\n{py_command}\n```",
        inline=False
    )
    
    # Download links
    embed.add_field(
        name="📥 Download Scanner",
        value=f"[Click here to download scanner.py]({RENDER_URL}/scanner.py)",
        inline=False
    )
    
    embed.add_field(
        name="⏱️ Timeout",
        value="This scan will expire in **10 minutes** if no data is received.",
        inline=True
    )
    
    embed.add_field(
        name="📊 Data Collected",
        value="• System Information\n"
              "• Security Status\n"
              "• File Scanner\n"
              "• Registry Analysis\n"
              "• Game Ban Checks\n"
              "• Hardware Info",
        inline=True
    )
    
    embed.set_footer(text=f"Scan ID: {scan_id} | Expires in 10 minutes")
    
    # Add buttons
    view = View(timeout=600)
    
    async def cancel_callback(interaction: Interaction):
        if scan_id in active_scans:
            del active_scans[scan_id]
            await interaction.response.edit_message(
                content="❌ Scan cancelled by user.",
                embed=None,
                view=None
            )
        else:
            await interaction.response.send_message("Scan already completed or expired.", ephemeral=True)
    
    async def refresh_callback(interaction: Interaction):
        if scan_id in active_scans:
            status = active_scans[scan_id]['status']
            steps = len(active_scans[scan_id]['steps_completed'])
            
            embed = interaction.message.embeds[0]
            # Update or add status field
            status_text = f"Status: **{status}** | Steps: {steps}/8"
            
            # Check if status field exists
            found = False
            for i, field in enumerate(embed.fields):
                if field.name == "🔄 Progress":
                    embed.set_field_at(i, name="🔄 Progress", value=status_text, inline=False)
                    found = True
                    break
            
            if not found:
                embed.add_field(name="🔄 Progress", value=status_text, inline=False)
            
            await interaction.response.edit_message(embed=embed)
        else:
            await interaction.response.send_message("Scan expired.", ephemeral=True)
    
    cancel_button = Button(label="❌ Cancel Scan", style=ButtonStyle.danger)
    cancel_button.callback = cancel_callback
    
    refresh_button = Button(label="🔄 Refresh", style=ButtonStyle.secondary)
    refresh_button.callback = refresh_callback
    
    view.add_item(cancel_button)
    view.add_item(refresh_button)
    
    msg = await ctx.send(embed=embed, view=view)
    active_scans[scan_id]['message_id'] = msg.id
    
    # Auto-expire after 10 minutes
    asyncio.create_task(expire_scan(scan_id, ctx.channel, msg))

async def expire_scan(scan_id, channel, message):
    """Expire scan after timeout"""
    await asyncio.sleep(600)  # 10 minutes
    if scan_id in active_scans and active_scans[scan_id]['data'] is None:
        embed = Embed(
            title="⏰ Scan Expired",
            description=f"Scan `{scan_id}` has expired due to inactivity.",
            color=Color.red()
        )
        await message.edit(embed=embed, view=None)
        
        # Update user stats
        user_id = active_scans[scan_id]['user_id']
        if str(user_id) in user_stats:
            user_stats[str(user_id)]['scans'] -= 1
        
        del active_scans[scan_id]

@bot.command(name='status')
async def check_status(ctx, scan_id: str = None):
    """Check status of a scan"""
    if not scan_id:
        await ctx.send("❌ Please provide a scan ID. Usage: `!status <scan_id>`")
        return
    
    if scan_id in active_scans:
        scan = active_scans[scan_id]
        embed = Embed(
            title="📊 Scan Status",
            color=Color.blue()
        )
        
        embed.add_field(name="Scan ID", value=f"`{scan_id}`", inline=False)
        embed.add_field(name="User", value=scan['user_mention'], inline=True)
        embed.add_field(name="Status", value=f"**{scan['status']}**", inline=True)
        embed.add_field(name="Started", value=f"<t:{int(scan['start_time'].timestamp())}:R>", inline=True)
        
        if scan['steps_completed']:
            steps = "\n".join([f"✅ {step}" for step in scan['steps_completed']])
            embed.add_field(name="Completed Steps", value=steps, inline=False)
        
        await ctx.send(embed=embed)
    else:
        # Check history
        found = False
        for s_id, data in list(scan_history.items())[-50:]:  # Check last 50 scans
            if s_id == scan_id:
                embed = Embed(
                    title="📊 Completed Scan",
                    color=Color.green()
                )
                embed.add_field(name="Scan ID", value=f"`{scan_id}`", inline=False)
                embed.add_field(name="User", value=f"<@{data['user_id']}>", inline=True)
                embed.add_field(name="Completed", value=f"<t:{int(data['completed_time'])}:R>", inline=True)
                embed.add_field(name="Threats Found", value=data.get('threat_count', 0), inline=True)
                await ctx.send(embed=embed)
                found = True
                break
        
        if not found:
            await ctx.send("❌ Scan ID not found.")

@bot.command(name='cancel')
async def cancel_scan(ctx, scan_id: str = None):
    """Cancel a pending scan"""
    if not scan_id:
        await ctx.send("❌ Please provide a scan ID. Usage: `!cancel <scan_id>`")
        return
    
    if scan_id in active_scans:
        if active_scans[scan_id]['user_id'] != ctx.author.id and not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ You can only cancel your own scans.")
            return
        
        # Try to update the original message
        try:
            channel = bot.get_channel(CHANNEL_ID)
            msg = await channel.fetch_message(active_scans[scan_id]['message_id'])
            embed = Embed(
                title="❌ Scan Cancelled",
                description=f"Scan `{scan_id}` was cancelled by {ctx.author.mention}",
                color=Color.red()
            )
            await msg.edit(embed=embed, view=None)
        except:
            pass
        
        del active_scans[scan_id]
        await ctx.send(f"✅ Scan `{scan_id}` cancelled successfully.")
    else:
        await ctx.send("❌ Scan ID not found or already completed.")

@bot.command(name='stats')
async def show_stats(ctx, user: discord.User = None):
    """Show scan statistics for a user"""
    if user is None:
        user = ctx.author
    
    user_id = str(user.id)
    
    if user_id in user_stats:
        stats = user_stats[user_id]
        embed = Embed(
            title=f"📊 Scan Statistics - {user.name}",
            color=Color.gold()
        )
        
        embed.add_field(name="Total Scans", value=stats.get('scans', 0), inline=True)
        embed.add_field(name="Threats Found", value=stats.get('threats_found', 0), inline=True)
        
        if stats.get('last_scan'):
            try:
                last_scan = datetime.fromisoformat(stats['last_scan'])
                embed.add_field(name="Last Scan", value=f"<t:{int(last_scan.timestamp())}:R>", inline=True)
            except:
                embed.add_field(name="Last Scan", value="Unknown", inline=True)
        
        # Calculate success rate
        successful = stats.get('successful_scans', 0)
        total = stats.get('scans', 1)
        success_rate = (successful / total * 100) if total > 0 else 0
        embed.add_field(name="Success Rate", value=f"{success_rate:.1f}%", inline=True)
        
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"📊 No scan statistics found for {user.name}.")

@bot.command(name='recent')
async def recent_scans(ctx, limit: int = 5):
    """Show recent scans"""
    if limit > 20:
        limit = 20
    
    recent = list(scan_history.items())[-limit:]
    
    if not recent:
        await ctx.send("📊 No recent scans found.")
        return
    
    embed = Embed(
        title=f"📋 Recent Scans (Last {len(recent)})",
        color=Color.blue()
    )
    
    for scan_id, data in reversed(recent):
        status = "✅ Complete" if data.get('success') else "❌ Failed"
        threats = data.get('threat_count', 0)
        threat_emoji = "⚠️" if threats > 0 else "✅"
        
        value = f"User: <@{data['user_id']}> | {status} | {threat_emoji} Threats: {threats}"
        embed.add_field(name=f"`{scan_id}`", value=value, inline=False)
    
    await ctx.send(embed=embed)

# ==================== ADMIN COMMANDS ====================

@bot.command(name='adduser')
@commands.has_permissions(administrator=True)
async def add_user(ctx, user_id: str):
    """Add authorized user"""
    global AUTHORIZED_USERS
    
    if user_id not in AUTHORIZED_USERS:
        AUTHORIZED_USERS.append(user_id)
        
        embed = Embed(
            title="✅ User Added",
            description=f"Added <@{user_id}> to authorized users.",
            color=Color.green()
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ User already authorized.")

@bot.command(name='removeuser')
@commands.has_permissions(administrator=True)
async def remove_user(ctx, user_id: str):
    """Remove authorized user"""
    global AUTHORIZED_USERS
    
    if user_id in AUTHORIZED_USERS:
        AUTHORIZED_USERS.remove(user_id)
        
        embed = Embed(
            title="✅ User Removed",
            description=f"Removed <@{user_id}> from authorized users.",
            color=Color.green()
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ User not found in authorized list.")

@bot.command(name='listusers')
@commands.has_permissions(administrator=True)
async def list_users(ctx):
    """List all authorized users"""
    if not AUTHORIZED_USERS:
        await ctx.send("📋 No authorized users.")
        return
    
    embed = Embed(
        title="📋 Authorized Users",
        color=Color.blue()
    )
    
    user_list = []
    for user_id in AUTHORIZED_USERS:
        try:
            user = await bot.fetch_user(int(user_id))
            user_list.append(f"• {user.mention} - `{user.name}`")
        except:
            user_list.append(f"• <@{user_id}> - `Unknown User`")
    
    embed.description = "\n".join(user_list)
    embed.set_footer(text=f"Total: {len(AUTHORIZED_USERS)} users")
    
    await ctx.send(embed=embed)

@bot.command(name='broadcast')
@commands.has_permissions(administrator=True)
async def broadcast(ctx, *, message: str):
    """Broadcast message to the channel"""
    embed = Embed(
        title="📢 Announcement",
        description=message,
        color=Color.purple(),
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"From: {ctx.author.name}")
    
    channel = bot.get_channel(CHANNEL_ID)
    await channel.send(embed=embed)
    await ctx.send("✅ Broadcast sent!")

# ==================== FASTAPI ROUTES ====================

@fastapi_app.get("/", response_class=PlainTextResponse)
async def root():
    """Root endpoint"""
    return "R6X XScan API is running. Visit /docs for documentation."

@fastapi_app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint"""
    return HealthResponse(
        status='healthy',
        active_scans=len(active_scans),
        total_scans=len(scan_history),
        authorized_users=len(AUTHORIZED_USERS),
        uptime=str(datetime.now() - bot_start_time).split('.')[0],
        bot_status='online'
    )

@fastapi_app.get("/stats")
async def get_stats(x_api_key: Optional[str] = Header(None)):
    """Get bot statistics (protected)"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return {
        'active_scans': len(active_scans),
        'total_scans': len(scan_history),
        'authorized_users': len(AUTHORIZED_USERS),
        'user_stats': user_stats,
        'bot_uptime': str(datetime.now() - bot_start_time).split('.')[0]
    }

@fastapi_app.post("/api/scan", response_model=ScanResponse)
async def receive_scan(
    scan_data: ScanData, 
    x_api_key: Optional[str] = Header(None),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """Receive scan data from Python scanner"""
    # Verify API key
    if x_api_key != API_KEY:
        logger.warning(f"Invalid API key attempt: {x_api_key}")
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    scan_id = scan_data.scan_id
    user_id = scan_data.user_id
    
    logger.info(f"Received scan data for scan_id: {scan_id}, user_id: {user_id}")
    
    # Verify user is authorized
    if str(user_id) not in AUTHORIZED_USERS:
        logger.warning(f"Unauthorized user attempt: {user_id}")
        raise HTTPException(status_code=403, detail="User not authorized")
    
    # Check if scan exists
    if scan_id not in active_scans:
        logger.warning(f"Invalid scan ID: {scan_id}")
        raise HTTPException(status_code=404, detail="Invalid or expired scan ID")
    
    # Verify user matches
    if active_scans[scan_id]['user_id'] != int(user_id):
        logger.warning(f"User mismatch: scan user {active_scans[scan_id]['user_id']} != {user_id}")
        raise HTTPException(status_code=403, detail="User mismatch - This scan ID belongs to another user")
    
    # Store data and update status
    active_scans[scan_id]['data'] = scan_data.dict()
    active_scans[scan_id]['status'] = 'completed'
    active_scans[scan_id]['completed_time'] = datetime.now()
    
    # Send to Discord in background
    background_tasks.add_task(send_scan_results_discord, scan_id, scan_data.dict())
    
    logger.info(f"Scan {scan_id} processed successfully")
    
    return ScanResponse(
        status='success',
        message='Scan data received successfully. Check Discord for results.',
        scan_id=scan_id
    )

@fastapi_app.get("/scanner.py", response_class=PlainTextResponse)
async def serve_scanner():
    """Serve the Python scanner script"""
    try:
        scanner_path = os.path.join(os.path.dirname(__file__), 'r6x_scanner.py')
        
        # Check if script exists
        if not os.path.exists(scanner_path):
            # Return embedded scanner code if file doesn't exist
            return get_embedded_scanner()
        
        with open(scanner_path, 'r', encoding='utf-8') as f:
            scanner_content = f.read()
        
        # Replace API URL and key
        scanner_content = scanner_content.replace(
            'API_URL = "https://bot-hosting-b-ga04.onrender.com/api/scan"',
            f'API_URL = "{RENDER_URL}/api/scan"'
        )
        scanner_content = scanner_content.replace(
            'API_KEY = "rnd_o2SUQpg4Ln3EsJSJsOYOeCHnLnId"',
            f'API_KEY = "{API_KEY}"'
        )
        
        return scanner_content
    except Exception as e:
        logger.error(f"Error serving scanner: {e}")
        return PlainTextResponse(get_embedded_scanner(), status_code=200)

def get_embedded_scanner():
    """Return embedded scanner code"""
    return '''#!/usr/bin/env python3
"""
R6X XScan - Advanced System Security Scanner
Download the full version from the Discord bot!
"""

import argparse
import requests
import sys
import json
import time
import os
from datetime import datetime

def main():
    print("="*50)
    print("R6X XScan - Please download the full scanner from Discord")
    print("="*50)
    print("\\nRun this command to download the full scanner:")
    print(f"  curl -O {RENDER_URL}/scanner.py")
    print("\\nThen run:")
    print("  python scanner.py -ScanID <SCAN_ID> -UserID <USER_ID>")

if __name__ == "__main__":
    main()
'''

@fastapi_app.get("/api/active_scans")
async def get_active_scans(x_api_key: Optional[str] = Header(None)):
    """Get list of active scans (protected)"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    scans = []
    for scan_id, scan in active_scans.items():
        scans.append({
            'scan_id': scan_id,
            'user_id': scan['user_id'],
            'user_name': scan['user_name'],
            'start_time': scan['start_time'].isoformat(),
            'status': scan['status'],
            'has_data': scan['data'] is not None
        })
    
    return {'active_scans': scans}

# ==================== DISCORD RESULT HANDLING ====================

async def send_scan_results_discord(scan_id: str, data: Dict):
    """Send formatted scan results to Discord"""
    try:
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            logger.error(f"Channel {CHANNEL_ID} not found")
            return
        
        scan_info = active_scans.get(scan_id, {})
        user_id = scan_info.get('user_id')
        user_mention = f"<@{user_id}>" if user_id else "Unknown"
        
        # Update user stats with threat count
        threat_count = len(data.get('threats', []))
        if str(user_id) in user_stats:
            user_stats[str(user_id)]['threats_found'] = user_stats[str(user_id)].get('threats_found', 0) + threat_count
            user_stats[str(user_id)]['successful_scans'] = user_stats[str(user_id)].get('successful_scans', 0) + 1
        
        # Store in history
        scan_history[scan_id] = {
            'user_id': user_id,
            'completed_time': datetime.now().timestamp(),
            'threat_count': threat_count,
            'success': True
        }
        
        # Limit history size
        if len(scan_history) > 1000:
            # Remove oldest 200
            oldest = sorted(scan_history.keys())[:200]
            for key in oldest:
                del scan_history[key]
        
        # Update the original message if it exists
        if scan_info.get('message_id'):
            try:
                msg = await channel.fetch_message(scan_info['message_id'])
                
                # Create processing embed
                processing_embed = Embed(
                    title="✅ Scan Complete - Processing Results",
                    description=f"Scan completed for {user_mention}",
                    color=Color.green(),
                    timestamp=datetime.now()
                )
                
                processing_embed.add_field(
                    name="📊 Processing Results",
                    value="Please wait while I format the results...",
                    inline=False
                )
                
                await msg.edit(embed=processing_embed, view=None)
                
            except Exception as e:
                logger.error(f"Error updating message: {e}")
        
        # Send all results
        await send_main_results(channel, data, user_mention, scan_id)
        await send_detailed_results(channel, data, user_mention, scan_id)
        await send_summary(channel, data, user_mention, scan_id)
        
        # Clean up scan
        if scan_id in active_scans:
            del active_scans[scan_id]
        
        # Send DM to user
        try:
            user = await bot.fetch_user(int(user_id))
            dm_embed = Embed(
                title="✅ Your R6X XScan is Complete",
                description=f"Scan ID: `{scan_id}`",
                color=Color.green()
            )
            dm_embed.add_field(name="Threats Found", value=threat_count, inline=True)
            dm_embed.add_field(name="Check Channel", value=f"<#{CHANNEL_ID}>", inline=True)
            await user.send(embed=dm_embed)
        except:
            pass  # User might have DMs disabled
            
    except Exception as e:
        logger.error(f"Error sending results: {e}")
        logger.error(traceback.format_exc())
        # Try to send error message
        try:
            channel = bot.get_channel(CHANNEL_ID)
            await channel.send(f"❌ Error processing scan {scan_id}: {str(e)}")
        except:
            pass

async def send_main_results(channel, data, user_mention, scan_id):
    """Send main results embed"""
    
    # Extract data
    system_info = data.get('system_info', {})
    security = data.get('security', {})
    files = data.get('files', {})
    threats = data.get('threats', [])
    
    # Create main embed
    embed = Embed(
        title="📊 R6X XScan Results",
        description=f"Scan completed for {user_mention}",
        color=Color.gold(),
        timestamp=datetime.now()
    )
    
    # System Info
    install_date = system_info.get('install_date', 'Unknown')
    if install_date != 'Unknown' and len(install_date) > 10:
        try:
            install_date = install_date[:10]  # Just the date part
        except:
            pass
    
    embed.add_field(
        name="💻 System Information",
        value=f"```\n"
              f"Windows Install: {install_date}\n"
              f"Secure Boot: {system_info.get('secure_boot', 'Unknown')}\n"
              f"DMA Protection: {system_info.get('dma_protection', 'Unknown')}\n"
              f"```",
        inline=False
    )
    
    # Security Status
    av_status = "⚠️ Third-Party AV" if security.get('antivirus_enabled') else "✅ Windows Defender Only"
    defender_status = "✅ Enabled" if security.get('defender_enabled') else "❌ Disabled"
    realtime_status = "✅ Active" if security.get('realtime') else "❌ Inactive"
    
    embed.add_field(
        name="🛡️ Security Status",
        value=f"```\n"
              f"AV: {av_status}\n"
              f"Defender: {defender_status}\n"
              f"Real-Time: {realtime_status}\n"
              f"```",
        inline=True
    )
    
    # Threats
    threat_count = len(threats)
    if threat_count > 0:
        threat_text = f"⚠️ **{threat_count} threats detected**"
        threat_color = "🔴"
    else:
        threat_text = "✅ No threats detected"
        threat_color = "🟢"
    
    embed.add_field(
        name="🦠 Threat Detection",
        value=f"```\n{threat_color} {threat_text}\n```",
        inline=True
    )
    
    # File Stats
    embed.add_field(
        name="📁 File Scan",
        value=f"```\n"
              f"EXE Files: {files.get('exe_count', 0)}\n"
              f"RAR Files: {files.get('rar_count', 0)}\n"
              f"Suspicious: {files.get('sus_count', 0)}\n"
              f"```",
        inline=True
    )
    
    # Game Bans Summary
    game_bans = data.get('game_bans', {})
    r6_accounts = game_bans.get('rainbow_six', [])
    steam_accounts = game_bans.get('steam', [])
    
    banned_r6 = 0
    for account in r6_accounts:
        if 'BANNED' in account:
            banned_r6 += 1
    
    banned_steam = 0
    for account in steam_accounts:
        if 'BANNED' in account:
            banned_steam += 1
    
    embed.add_field(
        name="🎮 Game Accounts",
        value=f"```\n"
              f"R6 Accounts: {len(r6_accounts)} (🚫 {banned_r6} banned)\n"
              f"Steam Accounts: {len(steam_accounts)} (🚫 {banned_steam} banned)\n"
              f"```",
        inline=False
    )
    
    # Hardware
    hardware = data.get('hardware', {})
    monitors = len(hardware.get('monitors', []))
    pcie = len(hardware.get('pcie_devices', []))
    
    embed.add_field(
        name="💾 Hardware",
        value=f"```\n"
              f"Monitors: {monitors}\n"
              f"PCIe Devices: {pcie}\n"
              f"```",
        inline=True
    )
    
    # Executed Programs
    exec_count = len(data.get('executed_programs', []))
    embed.add_field(
        name="📋 Executed Programs",
        value=f"```\nRecent Programs: {exec_count}\n```",
        inline=True
    )
    
    embed.set_footer(text=f"Scan ID: {scan_id}")
    
    await channel.send(content=f"{user_mention} - Your scan results are ready!", embed=embed)

async def send_detailed_results(channel, data, user_mention, scan_id):
    """Send detailed results in multiple embeds"""
    
    # Threats detailed
    threats = data.get('threats', [])
    if threats:
        chunks = [threats[i:i+3] for i in range(0, len(threats), 3)]
        for i, chunk in enumerate(chunks):
            embed = Embed(
                title=f"🦠 Threats Detected (Part {i+1}/{len(chunks)})",
                color=Color.red()
            )
            
            for threat in chunk:
                severity = parse_threat_severity(threat.get('severity', 0))
                name = truncate_string(threat.get('name', 'Unknown'), 100)
                path = truncate_string(threat.get('path', 'Unknown'), 200)
                
                value = f"**Severity:** {severity}\n**Path:** `{path}`"
                embed.add_field(name=f"⚠️ {name}", value=value, inline=False)
            
            await channel.send(embed=embed)
    
    # Suspicious Files
    sus_files = data.get('files', {}).get('suspicious', [])
    if sus_files:
        chunks = [sus_files[i:i+5] for i in range(0, len(sus_files), 5)]
        for i, chunk in enumerate(chunks):
            embed = Embed(
                title=f"⚠️ Suspicious Files Found (Part {i+1}/{len(chunks)})",
                description=f"Files that match suspicious patterns",
                color=Color.orange()
            )
            
            files_list = "\n".join([f"• `{truncate_string(f, 80)}`" for f in chunk])
            embed.add_field(name="Files", value=files_list[:1024], inline=False)
            embed.set_footer(text=f"Scan ID: {scan_id}")
            
            await channel.send(embed=embed)
    
    # Game Bans Detailed
    game_bans = data.get('game_bans', {})
    
    # R6 Bans
    r6_accounts = game_bans.get('rainbow_six', [])
    if r6_accounts:
        embed = Embed(
            title="🎮 Rainbow Six Siege Account Status",
            description=f"Checking {len(r6_accounts)} accounts",
            color=Color.purple()
        )
        
        banned_list = []
        clean_list = []
        
        for account in r6_accounts:
            if 'BANNED' in account:
                banned_list.append(f"🚫 {account}")
            else:
                clean_list.append(f"✅ {account}")
        
        if banned_list:
            embed.add_field(
                name=f"🔴 Banned Accounts ({len(banned_list)})",
                value="\n".join(banned_list[:5]) + ("\n..." if len(banned_list) > 5 else ""),
                inline=False
            )
        
        if clean_list:
            embed.add_field(
                name=f"🟢 Clean Accounts ({len(clean_list)})",
                value="\n".join(clean_list[:5]) + ("\n..." if len(clean_list) > 5 else ""),
                inline=False
            )
        
        await channel.send(embed=embed)
    
    # Steam Bans
    steam_accounts = game_bans.get('steam', [])
    if steam_accounts:
        embed = Embed(
            title="🎮 Steam Account Status",
            description=f"Checking {len(steam_accounts)} accounts",
            color=Color.blue()
        )
        
        banned_list = []
        clean_list = []
        
        for account in steam_accounts:
            if 'BANNED' in account:
                banned_list.append(f"🚫 {account}")
            else:
                clean_list.append(f"✅ {account}")
        
        if banned_list:
            embed.add_field(
                name=f"🔴 Banned Accounts ({len(banned_list)})",
                value="\n".join(banned_list[:5]) + ("\n..." if len(banned_list) > 5 else ""),
                inline=False
            )
        
        if clean_list:
            embed.add_field(
                name=f"🟢 Clean Accounts ({len(clean_list)})",
                value="\n".join(clean_list[:5]) + ("\n..." if len(clean_list) > 5 else ""),
                inline=False
            )
        
        await channel.send(embed=embed)
    
    # Top Executed Programs
    programs = data.get('executed_programs', [])
    if programs:
        embed = Embed(
            title="📋 Recently Executed Programs (Top 20)",
            description="Programs found in registry execution logs",
            color=Color.blue()
        )
        
        # Clean up paths and get unique
        clean_programs = []
        seen = set()
        for p in programs[:50]:
            # Extract just filename if it's a path
            if '\\' in p:
                p = p.split('\\')[-1]
            if p not in seen and len(p) < 100:
                clean_programs.append(p)
                seen.add(p)
        
        prog_list = "\n".join([f"• `{p}`" for p in clean_programs[:20]])
        embed.add_field(name="Programs", value=prog_list or "None found", inline=False)
        
        await channel.send(embed=embed)

async def send_summary(channel, data, user_mention, scan_id):
    """Send final summary with recommendations"""
    
    threats = data.get('threats', [])
    sus_files = data.get('files', {}).get('suspicious', [])
    game_bans = data.get('game_bans', {})
    
    # Count banned accounts
    banned_r6 = 0
    for account in game_bans.get('rainbow_six', []):
        if 'BANNED' in account:
            banned_r6 += 1
    
    banned_steam = 0
    for account in game_bans.get('steam', []):
        if 'BANNED' in account:
            banned_steam += 1
    
    # Generate recommendations
    recommendations = []
    
    if threats:
        recommendations.append("🔴 **Run a full antivirus scan immediately**")
    
    if sus_files:
        recommendations.append("⚠️ **Review suspicious files and delete if not recognized**")
    
    if banned_r6 > 0 or banned_steam > 0:
        recommendations.append("🎮 **Banned game accounts detected - Review account status**")
    
    if not data.get('security', {}).get('realtime'):
        recommendations.append("🛡️ **Enable Windows Defender real-time protection**")
    
    if not recommendations:
        recommendations.append("✅ **System appears clean - No immediate action needed**")
    
    embed = Embed(
        title="📋 Scan Summary & Recommendations",
        description=f"Based on the scan results for {user_mention}",
        color=Color.green()
    )
    
    embed.add_field(
        name="📊 Quick Stats",
        value=f"```\n"
              f"Threats: {len(threats)}\n"
              f"Suspicious Files: {len(sus_files)}\n"
              f"Banned Accounts: {banned_r6 + banned_steam}\n"
              f"```",
        inline=False
    )
    
    embed.add_field(
        name="💡 Recommendations",
        value="\n".join(recommendations),
        inline=False
    )
    
    embed.set_footer(text=f"Scan ID: {scan_id} | Complete")
    
    await channel.send(embed=embed)

# ==================== RUN BOTH SERVERS ====================

def run_fastapi():
    """Run FastAPI server"""
    port = int(os.getenv('PORT', 5000))
    uvicorn.run(fastapi_app, host="0.0.0.0", port=port)

async def run_bot():
    """Run Discord bot"""
    await bot.start(TOKEN)

def run_bot_sync():
    """Run bot in sync context"""
    asyncio.run(run_bot())

if __name__ == '__main__':
    # Start FastAPI in a thread
    fastapi_thread = threading.Thread(target=run_fastapi, daemon=True)
    fastapi_thread.start()
    
    # Run Discord bot
    run_bot_sync()
