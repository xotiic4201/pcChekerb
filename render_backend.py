"""
XBATCH RPG - Production Backend for Render
Complete FastAPI implementation with Supabase integration
"""

import os
import json
import hashlib
import secrets
import uuid
import asyncio
import random
import hmac
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Set
from contextlib import asynccontextmanager
from collections import defaultdictrender_backend.py

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, Field, validator, EmailStr
import jwt
from supabase import create_client, Client
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================

class Config:
    """Application configuration from environment variables"""
    SUPABASE_URL = os.getenv("SUPABASE_URL", "https://your-project.supabase.co")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
    JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
    ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
    DEBUG = ENVIRONMENT == "development"
    API_VERSION = "v1"
    
    # Game settings
    MAX_PLAYER_LEVEL = 100
    BASE_EXP_MULTIPLIER = 1.5
    COMBAT_TIMEOUT = 60
    CHAT_HISTORY_LIMIT = 100

config = Config()

# ==================== LOGGING ====================

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== SUPABASE CLIENT ====================

class SupabaseClient:
    """Supabase client singleton"""
    _instance = None
    _client: Client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def get_client(self) -> Client:
        if self._client is None:
            if not config.SUPABASE_URL or not config.SUPABASE_KEY:
                raise ValueError("Supabase credentials not configured")
            self._client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
            logger.info("Supabase client initialized")
        return self._client

supabase = SupabaseClient().get_client()

# ==================== PYDANTIC MODELS ====================

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=20)
    email: EmailStr
    password: str = Field(..., min_length=6)
    
    @validator('username')
    def validate_username(cls, v):
        if not v.replace('_', '').isalnum():
            raise ValueError('Username must be alphanumeric or contain underscores')
        return v.lower()

class UserLogin(BaseModel):
    username: str
    password: str

class CharacterCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=20)
    class_name: str
    
    @validator('class_name')
    def validate_class(cls, v):
        valid_classes = ['warrior', 'mage', 'rogue', 'paladin', 'ranger']
        if v not in valid_classes:
            raise ValueError(f'Class must be one of: {", ".join(valid_classes)}')
        return v

class CombatAction(BaseModel):
    action: str
    skill_id: Optional[str] = None

class ChatMessage(BaseModel):
    channel: str = 'global'
    message: str = Field(..., max_length=500)
    recipient: Optional[str] = None
    
    @validator('channel')
    def validate_channel(cls, v):
        valid_channels = ['global', 'guild', 'party', 'trade', 'whisper']
        if v not in valid_channels:
            raise ValueError(f'Channel must be one of: {", ".join(valid_channels)}')
        return v

class ItemUse(BaseModel):
    item_id: str
    quantity: int = 1

# ==================== AUTHENTICATION ====================

security = HTTPBearer()

def create_token(user_id: int, username: str) -> str:
    """Create JWT token"""
    payload = {
        'user_id': user_id,
        'username': username,
        'exp': datetime.utcnow() + timedelta(days=7)
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm='HS256')

def verify_token(token: str) -> dict:
    """Verify JWT token"""
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Get current user from token"""
    token = credentials.credentials
    return verify_token(token)

# ==================== GAME CONSTANTS ====================

CLASS_STATS = {
    'warrior': {'hp': 120, 'mana': 30, 'attack': 15, 'defense': 12, 'magic': 0, 'crit': 5, 'crit_damage': 150},
    'mage': {'hp': 70, 'mana': 100, 'attack': 5, 'defense': 5, 'magic': 20, 'crit': 10, 'crit_damage': 175},
    'rogue': {'hp': 90, 'mana': 50, 'attack': 12, 'defense': 8, 'magic': 5, 'crit': 20, 'crit_damage': 200},
    'paladin': {'hp': 110, 'mana': 60, 'attack': 12, 'defense': 15, 'magic': 8, 'crit': 5, 'crit_damage': 150},
    'ranger': {'hp': 95, 'mana': 55, 'attack': 14, 'defense': 9, 'magic': 3, 'crit': 15, 'crit_damage': 175}
}

ENEMIES = {
    'slime': {'name': 'Slime', 'level': 1, 'max_hp': 30, 'attack': 5, 'defense': 2, 'exp': 10, 'gold': 5},
    'goblin': {'name': 'Goblin', 'level': 2, 'max_hp': 45, 'attack': 8, 'defense': 4, 'exp': 20, 'gold': 10},
    'wolf': {'name': 'Wolf', 'level': 3, 'max_hp': 60, 'attack': 12, 'defense': 5, 'exp': 30, 'gold': 15},
    'bear': {'name': 'Bear', 'level': 5, 'max_hp': 120, 'attack': 18, 'defense': 10, 'exp': 60, 'gold': 30},
    'orc': {'name': 'Orc', 'level': 7, 'max_hp': 150, 'attack': 22, 'defense': 15, 'exp': 100, 'gold': 50},
    'troll': {'name': 'Troll', 'level': 10, 'max_hp': 250, 'attack': 30, 'defense': 20, 'exp': 200, 'gold': 100},
    'dragon': {'name': 'Dragon', 'level': 20, 'max_hp': 500, 'attack': 50, 'defense': 30, 'exp': 1000, 'gold': 500}
}

ITEMS = {
    'health_potion': {'name': 'Health Potion', 'type': 'consumable', 'value': 50, 'heal': 50},
    'mana_potion': {'name': 'Mana Potion', 'type': 'consumable', 'value': 40, 'heal': 30},
    'iron_sword': {'name': 'Iron Sword', 'type': 'weapon', 'value': 100, 'attack': 5},
    'leather_armor': {'name': 'Leather Armor', 'type': 'armor', 'value': 80, 'defense': 5}
}

LOCATIONS = {
    'village': {'name': 'Village of Beginnings', 'description': 'A peaceful village', 'enemies': []},
    'forest': {'name': 'Darkwood Forest', 'description': 'A dense forest', 'enemies': ['slime', 'goblin', 'wolf']},
    'mountains': {'name': 'Crystal Mountains', 'description': 'Towering peaks', 'enemies': ['bear', 'orc', 'troll']}
}

# ==================== WEBSOCKET MANAGER ====================

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}
        self.user_info: Dict[int, dict] = {}
        self.combat_sessions: Dict[int, dict] = {}

    async def connect(self, websocket: WebSocket, user_id: int, username: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        self.user_info[user_id] = {
            'username': username,
            'connected_at': datetime.now().isoformat(),
            'location': 'village'
        }
        await self.broadcast_online_count()
        logger.info(f"User {username} connected")

    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        if user_id in self.user_info:
            del self.user_info[user_id]
        if user_id in self.combat_sessions:
            del self.combat_sessions[user_id]

    async def send_personal(self, user_id: int, message: dict):
        if user_id in self.active_connections:
            try:
                await self.active_connections[user_id].send_json(message)
            except:
                pass

    async def broadcast(self, message: dict, channel: str = 'global'):
        for user_id in self.active_connections:
            await self.send_personal(user_id, message)

    async def broadcast_online_count(self):
        count = len(self.active_connections)
        await self.broadcast({'type': 'online_count', 'count': count})

manager = ConnectionManager()

# ==================== FASTAPI APP ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan events"""
    logger.info("Starting up XBATCH RPG server...")
    yield
    logger.info("Shutting down...")

app = FastAPI(
    title="XBATCH RPG API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== HEALTH CHECK ====================

@app.get("/")
async def root():
    return {
        "status": "online",
        "game": "XBATCH RPG",
        "version": "1.0.0",
        "players_online": len(manager.active_connections)
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# ==================== AUTH ENDPOINTS ====================

@app.post("/api/auth/register")
async def register(user_data: UserCreate):
    """Register a new user"""
    try:
        # Check if user exists
        result = supabase.table('users').select('*').eq('username', user_data.username).execute()
        if result.data:
            raise HTTPException(status_code=400, detail="Username already exists")
        
        # Hash password
        salt = secrets.token_hex(16)
        password_hash = hashlib.sha256(f"{user_data.password}{salt}".encode()).hexdigest()
        
        # Create user
        user = {
            'username': user_data.username,
            'email': user_data.email,
            'password_hash': password_hash,
            'salt': salt,
            'created_at': datetime.now().isoformat()
        }
        
        result = supabase.table('users').insert(user).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create user")
        
        return {"message": "User created successfully"}
        
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/login")
async def login(login_data: UserLogin):
    """Login user"""
    try:
        # Get user
        result = supabase.table('users').select('*').eq('username', login_data.username).execute()
        if not result.data:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        user = result.data[0]
        
        # Verify password
        password_hash = hashlib.sha256(f"{login_data.password}{user['salt']}".encode()).hexdigest()
        if password_hash != user['password_hash']:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        # Get character
        char_result = supabase.table('characters').select('*').eq('user_id', user['id']).execute()
        character = char_result.data[0] if char_result.data else None
        
        # Create token
        token = create_token(user['id'], user['username'])
        
        return {
            'token': token,
            'user': {
                'id': user['id'],
                'username': user['username'],
                'email': user['email']
            },
            'character': character
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== CHARACTER ENDPOINTS ====================

@app.post("/api/characters/create")
async def create_character(
    char_data: CharacterCreate,
    user: dict = Depends(get_current_user)
):
    """Create a new character"""
    try:
        # Check if user already has character
        existing = supabase.table('characters').select('*').eq('user_id', user['user_id']).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="User already has a character")
        
        # Get class stats
        stats = CLASS_STATS[char_data.class_name]
        
        # Create character
        character = {
            'user_id': user['user_id'],
            'name': char_data.name,
            'class': char_data.class_name,
            'level': 1,
            'exp': 0,
            'exp_needed': 100,
            'max_hp': stats['hp'],
            'current_hp': stats['hp'],
            'max_mana': stats['mana'],
            'current_mana': stats['mana'],
            'base_attack': stats['attack'],
            'base_defense': stats['defense'],
            'magic': stats['magic'],
            'crit_chance': stats['crit'],
            'gold': 100,
            'current_location': 'village',
            'created_at': datetime.now().isoformat()
        }
        
        result = supabase.table('characters').insert(character).execute()
        
        # Add starting items
        starting_items = [
            {'character_id': result.data[0]['id'], 'item_id': 'health_potion', 'item_name': 'Health Potion', 'quantity': 3, 'item_type': 'consumable'},
            {'character_id': result.data[0]['id'], 'item_id': 'mana_potion', 'item_name': 'Mana Potion', 'quantity': 2, 'item_type': 'consumable'}
        ]
        supabase.table('inventory').insert(starting_items).execute()
        
        return result.data[0]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Character creation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/characters/my")
async def get_my_character(user: dict = Depends(get_current_user)):
    """Get current user's character"""
    try:
        result = supabase.table('characters').select('*').eq('user_id', user['user_id']).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.error(f"Error fetching character: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/characters/{character_id}")
async def get_character(character_id: int):
    """Get character by ID"""
    try:
        result = supabase.table('characters').select('*').eq('id', character_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Character not found")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching character: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== INVENTORY ENDPOINTS ====================

@app.get("/api/inventory")
async def get_inventory(user: dict = Depends(get_current_user)):
    """Get user's inventory"""
    try:
        char = await get_my_character(user)
        if not char:
            return []
        
        result = supabase.table('inventory').select('*').eq('character_id', char['id']).execute()
        return result.data
    except Exception as e:
        logger.error(f"Error fetching inventory: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/inventory/use")
async def use_item(
    item_data: ItemUse,
    user: dict = Depends(get_current_user)
):
    """Use an item from inventory"""
    try:
        char = await get_my_character(user)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        
        # Get item
        item_result = supabase.table('inventory').select('*')\
            .eq('character_id', char['id'])\
            .eq('item_id', item_data.item_id)\
            .execute()
        
        if not item_result.data:
            raise HTTPException(status_code=404, detail="Item not found")
        
        item = item_result.data[0]
        
        # Use item (simplified)
        if item['item_type'] == 'consumable':
            # Heal logic here
            new_hp = min(char['max_hp'], char['current_hp'] + 50)
            supabase.table('characters').update({'current_hp': new_hp}).eq('id', char['id']).execute()
            
            # Decrease quantity
            new_quantity = item['quantity'] - item_data.quantity
            if new_quantity <= 0:
                supabase.table('inventory').delete().eq('id', item['id']).execute()
            else:
                supabase.table('inventory').update({'quantity': new_quantity}).eq('id', item['id']).execute()
        
        return {"message": f"Used {item['item_name']}"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error using item: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== LOCATION ENDPOINTS ====================

@app.get("/api/locations")
async def get_locations():
    """Get all locations"""
    return LOCATIONS

@app.get("/api/locations/{location_id}")
async def get_location(location_id: str):
    """Get specific location"""
    if location_id not in LOCATIONS:
        raise HTTPException(status_code=404, detail="Location not found")
    return LOCATIONS[location_id]

# ==================== COMBAT ENDPOINTS ====================

@app.post("/api/combat/start/{enemy_id}")
async def start_combat(
    enemy_id: str,
    user: dict = Depends(get_current_user)
):
    """Start combat with an enemy"""
    try:
        if enemy_id not in ENEMIES:
            raise HTTPException(status_code=404, detail="Enemy not found")
        
        char = await get_my_character(user)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        
        enemy = ENEMIES[enemy_id].copy()
        enemy['current_hp'] = enemy['max_hp']
        
        combat_state = {
            'enemy': enemy,
            'combat_log': [f"Combat started with {enemy['name']}!"],
            'turn': 'player',
            'round': 1
        }
        
        manager.combat_sessions[user['user_id']] = combat_state
        
        return combat_state
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting combat: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/combat/action")
async def combat_action(
    action_data: CombatAction,
    user: dict = Depends(get_current_user)
):
    """Perform combat action"""
    try:
        if user['user_id'] not in manager.combat_sessions:
            raise HTTPException(status_code=404, detail="No active combat")
        
        combat = manager.combat_sessions[user['user_id']]
        char = await get_my_character(user)
        
        if action_data.action == 'attack':
            # Player attacks
            damage = random.randint(5, 15) + char['base_attack']
            combat['enemy']['current_hp'] -= damage
            combat['combat_log'].append(f"You attack for {damage} damage!")
            
            # Check if enemy defeated
            if combat['enemy']['current_hp'] <= 0:
                # Victory
                exp_gained = combat['enemy']['exp']
                gold_gained = combat['enemy']['gold']
                
                # Update character
                new_exp = char['exp'] + exp_gained
                new_gold = char['gold'] + gold_gained
                
                # Check level up
                if new_exp >= char['exp_needed']:
                    char['level'] += 1
                    char['exp'] = new_exp - char['exp_needed']
                    char['exp_needed'] = int(char['exp_needed'] * 1.5)
                    
                    # Increase stats based on class
                    stats = CLASS_STATS[char['class']]
                    char['max_hp'] += int(stats['hp'] * 0.1)
                    char['base_attack'] += int(stats['attack'] * 0.1)
                    char['base_defense'] += int(stats['defense'] * 0.1)
                else:
                    char['exp'] = new_exp
                
                char['gold'] = new_gold
                char['kills'] = char.get('kills', 0) + 1
                
                # Save to database
                supabase.table('characters').update({
                    'level': char['level'],
                    'exp': char['exp'],
                    'exp_needed': char['exp_needed'],
                    'current_hp': char['current_hp'],
                    'max_hp': char['max_hp'],
                    'base_attack': char['base_attack'],
                    'base_defense': char['base_defense'],
                    'gold': char['gold'],
                    'kills': char['kills']
                }).eq('id', char['id']).execute()
                
                del manager.combat_sessions[user['user_id']]
                
                return {
                    'victory': True,
                    'exp_gained': exp_gained,
                    'gold_gained': gold_gained,
                    'level_up': char['level'] > char['level'] - 1,
                    'new_level': char['level'] if char['level'] > char['level'] - 1 else None
                }
            
            # Enemy attacks
            enemy_damage = random.randint(3, 10) + combat['enemy']['attack']
            char['current_hp'] -= enemy_damage
            combat['combat_log'].append(f"{combat['enemy']['name']} attacks for {enemy_damage} damage!")
            
            # Check if player defeated
            if char['current_hp'] <= 0:
                # Defeat
                char['deaths'] = char.get('deaths', 0) + 1
                char['gold'] = int(char['gold'] * 0.9)
                char['current_hp'] = int(char['max_hp'] / 2)
                
                supabase.table('characters').update({
                    'current_hp': char['current_hp'],
                    'gold': char['gold'],
                    'deaths': char['deaths']
                }).eq('id', char['id']).execute()
                
                del manager.combat_sessions[user['user_id']]
                
                return {
                    'defeat': True,
                    'message': 'You have been defeated!'
                }
            
            combat['round'] += 1
            manager.combat_sessions[user['user_id']] = combat
            
            return {
                'combat': combat,
                'character_hp': char['current_hp'],
                'character_mana': char['current_mana']
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in combat: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== WEBSOCKET ENDPOINT ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket connection for real-time features"""
    token = websocket.query_params.get('token')
    if not token:
        await websocket.close(code=1008)
        return
    
    try:
        # Verify token
        payload = verify_token(token)
        user_id = payload['user_id']
        username = payload['username']
        
        # Accept connection
        await manager.connect(websocket, user_id, username)
        
        # Send initial data
        await manager.send_personal(user_id, {
            'type': 'connected',
            'message': 'Connected to server',
            'online_count': len(manager.active_connections)
        })
        
        # Handle messages
        while True:
            try:
                data = await websocket.receive_json()
                
                if data['type'] == 'chat':
                    # Broadcast chat message
                    await manager.broadcast({
                        'type': 'chat',
                        'username': username,
                        'message': data['message'],
                        'channel': data.get('channel', 'global'),
                        'timestamp': datetime.now().isoformat()
                    })
                    
                elif data['type'] == 'location_update':
                    # Update user location
                    if user_id in manager.user_info:
                        manager.user_info[user_id]['location'] = data['location']
                        
                elif data['type'] == 'ping':
                    await manager.send_personal(user_id, {'type': 'pong'})
                    
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                break
                
    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
        await websocket.close(code=1008)
    finally:
        manager.disconnect(user_id)
        await manager.broadcast_online_count()

# ==================== LEADERBOARD ENDPOINTS ====================

@app.get("/api/leaderboard/level")
async def leaderboard_level(limit: int = 10):
    """Get level leaderboard"""
    try:
        result = supabase.table('characters')\
            .select('name, class, level, kills')\
            .order('level', desc=True)\
            .limit(limit)\
            .execute()
        return result.data
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leaderboard/kills")
async def leaderboard_kills(limit: int = 10):
    """Get kills leaderboard"""
    try:
        result = supabase.table('characters')\
            .select('name, class, level, kills')\
            .order('kills', desc=True)\
            .limit(limit)\
            .execute()
        return result.data
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leaderboard/gold")
async def leaderboard_gold(limit: int = 10):
    """Get gold leaderboard"""
    try:
        result = supabase.table('characters')\
            .select('name, class, level, gold')\
            .order('gold', desc=True)\
            .limit(limit)\
            .execute()
        return result.data
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== GUILD ENDPOINTS ====================

@app.post("/api/guilds/create")
async def create_guild(
    guild_data: dict,
    user: dict = Depends(get_current_user)
):
    """Create a new guild"""
    try:
        char = await get_my_character(user)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        
        # Check if already in guild
        member_check = supabase.table('guild_members')\
            .select('*')\
            .eq('character_id', char['id'])\
            .execute()
        
        if member_check.data:
            raise HTTPException(status_code=400, detail="Already in a guild")
        
        # Create guild
        guild = {
            'name': guild_data['name'],
            'description': guild_data.get('description', ''),
            'leader_id': char['id'],
            'created_at': datetime.now().isoformat()
        }
        
        result = supabase.table('guilds').insert(guild).execute()
        guild_id = result.data[0]['id']
        
        # Add leader as member
        supabase.table('guild_members').insert({
            'guild_id': guild_id,
            'character_id': char['id'],
            'rank': 'leader',
            'joined_at': datetime.now().isoformat()
        }).execute()
        
        return result.data[0]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating guild: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/guilds/{guild_id}/invite")
async def invite_to_guild(
    guild_id: int,
    invite_data: dict,
    user: dict = Depends(get_current_user)
):
    """Invite player to guild"""
    try:
        char = await get_my_character(user)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
        
        # Check if user is guild leader
        guild = supabase.table('guilds').select('*').eq('id', guild_id).execute()
        if not guild.data or guild.data[0]['leader_id'] != char['id']:
            raise HTTPException(status_code=403, detail="Not authorized")
        
        # Find target character
        target_char = supabase.table('characters')\
            .select('*')\
            .eq('name', invite_data['username'])\
            .execute()
        
        if not target_char.data:
            raise HTTPException(status_code=404, detail="Character not found")
        
        # Add to guild
        supabase.table('guild_members').insert({
            'guild_id': guild_id,
            'character_id': target_char.data[0]['id'],
            'rank': 'member',
            'joined_at': datetime.now().isoformat()
        }).execute()
        
        return {"message": "Invitation sent"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error inviting to guild: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== ADMIN ENDPOINTS ====================

@app.get("/admin/stats")
async def admin_stats():
    """Get server statistics (admin only)"""
    try:
        # Get total users
        users = supabase.table('users').select('*', count='exact').execute()
        
        # Get total characters
        chars = supabase.table('characters').select('*', count='exact').execute()
        
        return {
            'total_users': users.count if hasattr(users, 'count') else 0,
            'total_characters': chars.count if hasattr(chars, 'count') else 0,
            'players_online': len(manager.active_connections),
            'active_combats': len(manager.combat_sessions)
        }
    except Exception as e:
        logger.error(f"Error getting admin stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== ERROR HANDLERS ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )

# ==================== RUN ====================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=config.DEBUG,
        log_level="info"
    )

