# app.py - R6X CYBERSCAN Backend (Fixed)
# No email validator needed - removed EmailStr

import os
import uuid
import json
import asyncio
import threading
import time
from datetime import datetime
from typing import List, Dict, Optional, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
import logging
import discord
from discord.ext import commands

# ========== CONFIGURATION ==========
class Config:
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
    
config = Config()

# ========== LOGGING ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("r6x-backend")

# ========== SUPABASE ==========
supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
service_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)

# ========== PYDANTIC MODELS (No EmailStr) ==========
class ScanResult(BaseModel):
    username: str
    computer: str
    files_scanned: int
    threats_found: int
    suspicious_files: List[Dict[str, Any]]
    r6_accounts: List[Dict[str, Any]]
    steam_accounts: List[Dict[str, Any]]
    windows_install_date: Optional[str] = None
    antivirus_status: Optional[str] = None
    prefetch_files: Optional[List[Dict[str, Any]]] = None
    logitech_scripts: Optional[List[Dict[str, Any]]] = None
    scan_time: str

# ========== DISCORD BOT (Fixed - No rate limits) ==========
class DiscordBot:
    def __init__(self):
        self.bot = None
        self.loop = None
        self.thread = None
        self.channel = None
        self.is_ready = False
        self.retry_count = 0
        self.max_retries = 5
        
    def start(self):
        """Start the Discord bot in a separate thread with retry logic"""
        if not config.DISCORD_BOT_TOKEN or not config.DISCORD_CHANNEL_ID:
            logger.warning("Discord bot not configured - check environment variables")
            return
            
        self.thread = threading.Thread(target=self._run_bot, daemon=True)
        self.thread.start()
        
    def _run_bot(self):
        """Run the bot in its own event loop with retry logic"""
        while self.retry_count < self.max_retries:
            try:
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                self.loop.run_until_complete(self._setup_bot())
                break
            except Exception as e:
                self.retry_count += 1
                logger.error(f"Bot error (attempt {self.retry_count}/{self.max_retries}): {e}")
                time.sleep(5 * self.retry_count)  # Exponential backoff
            finally:
                if self.loop:
                    self.loop.close()
                    
    async def _setup_bot(self):
        """Setup and run the Discord bot"""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        
        self.bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
        
        @self.bot.event
        async def on_ready():
            logger.info(f"✅ Discord bot connected as {self.bot.user}")
            self.is_ready = True
            self.retry_count = 0  # Reset retry count on success
            
            # Get the channel
            try:
                self.channel = self.bot.get_channel(int(config.DISCORD_CHANNEL_ID))
                if not self.channel:
                    for guild in self.bot.guilds:
                        self.channel = guild.get_channel(int(config.DISCORD_CHANNEL_ID))
                        if self.channel:
                            break
                
                if self.channel:
                    embed = discord.Embed(
                        title="🤖 R6X Bot Online",
                        description="Security scanner bot is now active!",
                        color=0x00FF9D,
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(name="Status", value="🟢 Online", inline=True)
                    embed.add_field(name="Commands", value="!scan [username], !stats, !help", inline=True)
                    embed.set_footer(text="R6X CyberScan")
                    
                    await self.channel.send(embed=embed)
                    logger.info("✅ Welcome message sent to Discord")
            except Exception as e:
                logger.error(f"Error sending welcome message: {e}")
        
        @self.bot.event
        async def on_command_error(ctx, error):
            if isinstance(error, commands.CommandNotFound):
                return
            await ctx.send(f"❌ Error: {str(error)}")
        
        @self.bot.command(name='scan')
        async def scan_command(ctx, username: str = None):
            """Get latest scan results for a user"""
            if not username:
                await ctx.send("❌ Please specify a username: `!scan username`")
                return
            
            try:
                # Get latest scan from database
                result = supabase.table("scans")\
                    .select("*")\
                    .eq("username", username)\
                    .order("created_at", desc=True)\
                    .limit(1)\
                    .execute()
                
                if result.data and len(result.data) > 0:
                    scan = result.data[0]
                    color = 0x00FF9D if scan['threats_found'] == 0 else 0xFF003C
                    
                    embed = discord.Embed(
                        title=f"📊 Latest Scan: {username}",
                        color=color,
                        timestamp=datetime.fromisoformat(scan['created_at'].replace('Z', '+00:00'))
                    )
                    embed.add_field(name="Files Scanned", value=str(scan['files_scanned']), inline=True)
                    embed.add_field(name="Threats Found", value=str(scan['threats_found']), inline=True)
                    
                    # Parse JSON fields safely
                    try:
                        r6_accounts = json.loads(scan['r6_accounts']) if scan['r6_accounts'] else []
                        steam_accounts = json.loads(scan['steam_accounts']) if scan['steam_accounts'] else []
                    except:
                        r6_accounts = []
                        steam_accounts = []
                    
                    embed.add_field(name="R6 Accounts", value=str(len(r6_accounts)), inline=True)
                    embed.add_field(name="Steam Accounts", value=str(len(steam_accounts)), inline=True)
                    embed.add_field(name="Computer", value=scan.get('computer', 'Unknown'), inline=True)
                    
                    await ctx.send(embed=embed)
                else:
                    await ctx.send(f"📭 No scans found for user: {username}")
            except Exception as e:
                logger.error(f"Error in scan command: {e}")
                await ctx.send("❌ Error retrieving scan data")
        
        @self.bot.command(name='stats')
        async def stats_command(ctx):
            """Get bot statistics"""
            try:
                scans_result = supabase.table("scans").select("*", count="exact").execute()
                
                # Get unique users
                users_result = supabase.table("scans").select("username").execute()
                unique_users = set()
                if users_result.data:
                    for scan in users_result.data:
                        unique_users.add(scan['username'])
                
                embed = discord.Embed(
                    title="📊 Bot Statistics",
                    color=0x00FF9D,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Total Scans", value=str(scans_result.count if hasattr(scans_result, 'count') else 0), inline=True)
                embed.add_field(name="Unique Users", value=str(len(unique_users)), inline=True)
                embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
                
                await ctx.send(embed=embed)
            except Exception as e:
                logger.error(f"Error in stats command: {e}")
                await ctx.send("❌ Error retrieving stats")
        
        @self.bot.command(name='help')
        async def help_command(ctx):
            """Show available commands"""
            embed = discord.Embed(
                title="🤖 R6X Bot Commands",
                description="Available commands",
                color=0x5865F2
            )
            embed.add_field(name="!scan [username]", value="Get latest scan for a user", inline=False)
            embed.add_field(name="!stats", value="Show bot statistics", inline=False)
            embed.add_field(name="!help", value="Show this message", inline=False)
            embed.set_footer(text="R6X CyberScan v4.0")
            
            await ctx.send(embed=embed)
        
        # Start the bot
        try:
            await self.bot.start(config.DISCORD_BOT_TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                logger.error("Rate limited by Discord. Waiting before retry...")
                await asyncio.sleep(60)  # Wait 1 minute on rate limit
                raise
            else:
                raise
    
    async def send_scan_results(self, scan_data: Dict):
        """Send scan results to Discord channel"""
        if not self.is_ready or not self.channel:
            logger.warning("Discord bot not ready, cannot send results")
            return False
            
        try:
            # Create main embed
            color = 0x00FF9D if scan_data['threats_found'] == 0 else 0xFF003C
            embed = discord.Embed(
                title=f"📊 New Scan: {scan_data['username']}",
                description=f"Computer: {scan_data['computer']}",
                color=color,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(name="📁 Files Scanned", value=str(scan_data['files_scanned']), inline=True)
            embed.add_field(name="🚨 Threats Found", value=str(scan_data['threats_found']), inline=True)
            embed.add_field(name="🎮 R6 Accounts", value=str(len(scan_data['r6_accounts'])), inline=True)
            embed.add_field(name="🔄 Steam Accounts", value=str(len(scan_data['steam_accounts'])), inline=True)
            
            if scan_data.get('windows_install_date'):
                embed.add_field(name="💻 Windows Install", value=scan_data['windows_install_date'][:10], inline=True)
            
            if scan_data.get('antivirus_status'):
                embed.add_field(name="🛡️ Antivirus", value=scan_data['antivirus_status'][:50], inline=True)
            
            await self.channel.send(embed=embed)
            
            # Send suspicious files if any
            if scan_data['suspicious_files']:
                sus_embed = discord.Embed(
                    title="⚠️ Suspicious Files Found",
                    color=0xFF003C
                )
                sus_list = ""
                for f in scan_data['suspicious_files'][:10]:
                    sus_list += f"• {f.get('name', 'Unknown')} ({f.get('severity', 'MEDIUM')})\n"
                if sus_list:
                    sus_embed.description = sus_list
                    await self.channel.send(embed=sus_embed)
            
            # Send account info if any
            if scan_data['r6_accounts'] or scan_data['steam_accounts']:
                account_embed = discord.Embed(
                    title="🎮 Gaming Accounts Found",
                    color=0x5865F2
                )
                account_text = ""
                for acc in scan_data['r6_accounts'][:5]:
                    account_text += f"• R6: {acc.get('name', 'Unknown')}\n"
                for acc in scan_data['steam_accounts'][:5]:
                    account_text += f"• Steam: {acc.get('name', 'Unknown')}\n"
                if account_text:
                    account_embed.description = account_text
                    await self.channel.send(embed=account_embed)
            
            logger.info(f"✅ Scan results sent to Discord for {scan_data['username']}")
            return True
        except discord.errors.HTTPException as e:
            if e.status == 429:
                logger.error("Rate limited by Discord. Waiting before next message...")
                await asyncio.sleep(10)
            else:
                logger.error(f"Error sending to Discord: {e}")
            return False
        except Exception as e:
            logger.error(f"Error sending to Discord: {e}")
            return False

# Initialize Discord bot
discord_bot = DiscordBot()

# ========== FASTAPI APP ==========
app = FastAPI(title="R6X CYBERSCAN API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== API ROUTES ==========

@app.get("/")
async def root():
    return {
        "service": "R6X CYBERSCAN API",
        "status": "online",
        "bot_status": "connected" if discord_bot.is_ready else "connecting",
        "version": "4.0.0"
    }

@app.get("/api/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.post("/api/scan/save")
async def save_scan(scan: ScanResult, background_tasks: BackgroundTasks):
    """Save scan results and send to Discord"""
    
    # Save to database
    scan_id = str(uuid.uuid4())
    scan_doc = {
        "scan_id": scan_id,
        "username": scan.username,
        "computer": scan.computer,
        "files_scanned": scan.files_scanned,
        "threats_found": scan.threats_found,
        "r6_accounts": json.dumps(scan.r6_accounts),
        "steam_accounts": json.dumps(scan.steam_accounts),
        "suspicious_files": json.dumps(scan.suspicious_files),
        "windows_install_date": scan.windows_install_date,
        "antivirus_status": scan.antivirus_status,
        "prefetch_files": json.dumps(scan.prefetch_files) if scan.prefetch_files else None,
        "logitech_scripts": json.dumps(scan.logitech_scripts) if scan.logitech_scripts else None,
        "scan_time": scan.scan_time,
        "created_at": datetime.utcnow().isoformat()
    }
    
    service_supabase.table("scans").insert(scan_doc).execute()
    
    # Send to Discord in background
    background_tasks.add_task(discord_bot.send_scan_results, scan.dict())
    
    logger.info(f"✅ Scan saved for {scan.username}")
    
    return {"success": True, "scan_id": scan_id}

@app.get("/api/scan/history/{username}")
async def get_history(username: str, limit: int = 10):
    """Get scan history for a user"""
    
    result = supabase.table("scans").select("*")\
        .eq("username", username)\
        .order("created_at", desc=True)\
        .limit(limit)\
        .execute()
    
    scans = result.data if result.data else []
    
    # Parse JSON fields for response
    for scan in scans:
        try:
            if scan.get("r6_accounts"):
                scan["r6_accounts"] = json.loads(scan["r6_accounts"])
            if scan.get("steam_accounts"):
                scan["steam_accounts"] = json.loads(scan["steam_accounts"])
            if scan.get("suspicious_files"):
                scan["suspicious_files"] = json.loads(scan["suspicious_files"])
            if scan.get("prefetch_files"):
                scan["prefetch_files"] = json.loads(scan["prefetch_files"])
            if scan.get("logitech_scripts"):
                scan["logitech_scripts"] = json.loads(scan["logitech_scripts"])
        except:
            pass
    
    return {"scans": scans}

@app.get("/api/scan/latest/{username}")
async def get_latest_scan(username: str):
    """Get latest scan for a user"""
    
    result = supabase.table("scans").select("*")\
        .eq("username", username)\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()
    
    if not result.data or len(result.data) == 0:
        raise HTTPException(status_code=404, detail="No scans found for this user")
    
    scan = result.data[0]
    
    # Parse JSON fields
    try:
        if scan.get("r6_accounts"):
            scan["r6_accounts"] = json.loads(scan["r6_accounts"])
        if scan.get("steam_accounts"):
            scan["steam_accounts"] = json.loads(scan["steam_accounts"])
        if scan.get("suspicious_files"):
            scan["suspicious_files"] = json.loads(scan["suspicious_files"])
        if scan.get("prefetch_files"):
            scan["prefetch_files"] = json.loads(scan["prefetch_files"])
        if scan.get("logitech_scripts"):
            scan["logitech_scripts"] = json.loads(scan["logitech_scripts"])
    except:
        pass
    
    return scan

@app.get("/api/stats")
async def get_stats():
    """Get overall statistics"""
    
    scans_result = supabase.table("scans").select("*", count="exact").execute()
    
    # Get unique users
    users_result = supabase.table("scans").select("username").execute()
    unique_users = set()
    if users_result.data:
        for scan in users_result.data:
            unique_users.add(scan['username'])
    
    # Get total threats
    total_threats = 0
    if scans_result.data:
        for scan in scans_result.data:
            total_threats += scan.get('threats_found', 0)
    
    return {
        "total_scans": scans_result.count if hasattr(scans_result, 'count') else 0,
        "unique_users": len(unique_users),
        "total_threats": total_threats,
        "bot_status": "connected" if discord_bot.is_ready else "connecting"
    }

# ========== STARTUP ==========
@app.on_event("startup")
async def startup():
    logger.info("🚀 R6X CYBERSCAN Backend starting...")
    
    # Test Supabase connection
    try:
        test = supabase.table("scans").select("*").limit(1).execute()
        logger.info("✅ Supabase connected")
    except Exception as e:
        logger.error(f"❌ Supabase connection failed: {e}")
    
    # Start Discord bot
    if config.DISCORD_BOT_TOKEN and config.DISCORD_CHANNEL_ID:
        discord_bot.start()
        logger.info("✅ Discord bot starting...")
    else:
        logger.warning("⚠️ Discord bot not configured - check environment variables")

@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down...")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
