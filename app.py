import discord
from discord.ext import commands
from discord import Embed, Color, ButtonStyle
from discord.ui import View, Button
import os
import json
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import threading
from dotenv import load_dotenv
import logging
import traceback

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================
# These are fetched from Render environment variables
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
AUTHORIZED_USERS = os.getenv('AUTHORIZED_USERS', '').split(',') if os.getenv('AUTHORIZED_USERS') else []
API_KEY = os.getenv('API_KEY', 'R6X-SECURE-KEY-CHANGE-ME-NOW')
RENDER_URL = os.getenv('RENDER_URL', 'https://your-app-name.onrender.com')

# File paths
EXE_FILENAME = "R6XScan.exe"
EXE_PATH = os.path.join(os.path.dirname(__file__), EXE_FILENAME)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("r6x-bot")

# ==================== FASTAPI APP ====================
app = FastAPI(title="R6X XScan API", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== PYDANTIC MODELS ====================
class StartScanRequest(BaseModel):
    user_id: str

class StartScanResponse(BaseModel):
    scan_id: str
    message: str
    bot_token: str  # Send token to scanner
    channel_id: int
    api_key: str

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

# ==================== ACTIVE SCANS STORAGE ====================
active_scans = {}

# ==================== FASTAPI ROUTES ====================

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "R6X XScan API",
        "version": "1.0.0",
        "status": "online",
        "message": "Bot token is fetched from Render when scan starts"
    }

@app.post("/api/start-scan", response_model=StartScanResponse)
async def start_scan(request: StartScanRequest, x_api_key: Optional[str] = Header(None)):
    """Start a new scan and return bot credentials"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    user_id = request.user_id
    
    # Verify user is authorized
    if str(user_id) not in AUTHORIZED_USERS:
        raise HTTPException(status_code=403, detail="User not authorized")
    
    # Generate scan ID
    scan_id = f"R6X-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{user_id}"
    
    # Store scan session
    active_scans[scan_id] = {
        'user_id': int(user_id),
        'start_time': datetime.now(),
        'status': 'pending'
    }
    
    logger.info(f"Started scan {scan_id} for user {user_id}")
    
    # Return the bot token and credentials to the scanner
    return StartScanResponse(
        scan_id=scan_id,
        message="Scan started successfully. Bot will run locally.",
        bot_token=TOKEN,  # Send token from Render to scanner
        channel_id=CHANNEL_ID,
        api_key=API_KEY
    )

@app.post("/api/scan", response_model=ScanResponse)
async def receive_scan(scan_data: ScanData, x_api_key: Optional[str] = Header(None)):
    """Receive scan results (optional, can also send directly via bot)"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    logger.info(f"Received scan data for scan_id: {scan_data.scan_id}")
    
    return ScanResponse(
        status='success',
        message='Scan data received',
        scan_id=scan_data.scan_id
    )

@app.get("/download-exe")
async def download_exe():
    """Download the R6XScan.exe file"""
    if not os.path.exists(EXE_PATH):
        alt_path = os.path.join(os.getcwd(), EXE_FILENAME)
        if os.path.exists(alt_path):
            return FileResponse(
                path=alt_path,
                filename=EXE_FILENAME,
                media_type="application/octet-stream"
            )
        raise HTTPException(status_code=404, detail="EXE not found")
    
    return FileResponse(
        path=EXE_PATH,
        filename=EXE_FILENAME,
        media_type="application/octet-stream"
    )

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        'status': 'healthy',
        'active_scans': len(active_scans),
        'authorized_users': len(AUTHORIZED_USERS),
        'exe_available': os.path.exists(EXE_PATH),
        'token_loaded': bool(TOKEN)
    }

# ==================== RUN FASTAPI ====================
if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    uvicorn.run(app, host="0.0.0.0", port=port)
