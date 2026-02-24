"""
Complete 2D Pixel RPG Backend
Single file FastAPI application with Supabase integration
Includes Owner Auto-Registration, Role System, Cheat Menu, and Mod Menu
"""

import os
import json
import uuid
import hashlib
import secrets
import asyncio
import datetime
import math
import random
from typing import Dict, List, Optional, Any, Set
from enum import Enum
from dataclasses import dataclass, field
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
import jwt
from pydantic import BaseModel, Field, validator
import bcrypt
from supabase import create_client, Client

# ==================== Configuration ====================

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://your-project.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week

# Owner Configuration - Auto-register this user as owner
OWNER_USERNAME = os.getenv("OWNER_USERNAME")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")
OWNER_ID = "xotiic_40671"  # Unique owner identifier

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== Enums ====================

class GameClass(str, Enum):
    WARRIOR = "warrior"
    MAGE = "mage"
    ROGUE = "rogue"
    ARCHER = "archer"
    PALADIN = "paladin"
    NECROMANCER = "necromancer"

class ItemRarity(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"

class UserRole(str, Enum):
    PLAYER = "player"
    MODERATOR = "moderator"
    ADMIN = "admin"
    OWNER = "owner"

class QuestStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class ChatChannel(str, Enum):
    GLOBAL = "global"
    GUILD = "guild"
    PARTY = "party"
    TRADE = "trade"
    SYSTEM = "system"
    WHISPER = "whisper"
    MOD = "mod"      # Moderator channel
    ADMIN = "admin"  # Admin channel

class TradeStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

# ==================== Pydantic Models ====================

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=6)

class UserLogin(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str
    is_owner: bool

class CharacterCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=20)
    class_name: GameClass

class CharacterResponse(BaseModel):
    id: int
    name: str
    class_name: GameClass
    level: int
    exp: int
    gold: int
    hp: int
    max_hp: int
    mana: int
    max_mana: int
    strength: int
    agility: int
    intelligence: int
    vitality: int
    role: str

class ChatMessage(BaseModel):
    channel: ChatChannel
    content: str
    target: Optional[str] = None

class TradeOffer(BaseModel):
    item_id: int
    quantity: int
    gold: int

class AuctionListing(BaseModel):
    item_id: int
    quantity: int
    starting_bid: int
    buyout_price: Optional[int]
    duration_hours: int = 24

class GuildCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=30)
    tag: str = Field(..., min_length=2, max_length=5)

# ==================== Cheat/Mod Models ====================

class CheatCommand(BaseModel):
    command: str  # give_gold, set_level, spawn_item, god_mode, kill, heal
    target: Optional[str] = None
    amount: Optional[int] = None
    item_id: Optional[str] = None

class ModAction(BaseModel):
    action: str  # kick, ban, mute, warn, unmute, unban
    target_user_id: int
    target_character_id: Optional[int] = None
    reason: str
    duration: Optional[int] = None  # in minutes

class GameEvent(BaseModel):
    event_type: str  # spawn_boss, start_event, give_rewards, announce
    data: Dict[str, Any]

# ==================== Game Classes ====================

@dataclass
class Player:
    id: int
    user_id: int
    name: str
    class_name: GameClass
    level: int = 1
    exp: int = 0
    gold: int = 100
    hp: int = 100
    max_hp: int = 100
    mana: int = 50
    max_mana: int = 50
    strength: int = 10
    agility: int = 10
    intelligence: int = 10
    vitality: int = 10
    x: int = 0
    y: int = 0
    map_id: str = "start_village"
    guild_id: Optional[int] = None
    party_id: Optional[int] = None
    role: str = "player"
    status_effects: List[Dict] = field(default_factory=list)
    cooldowns: Dict[str, float] = field(default_factory=dict)
    skills: List[str] = field(default_factory=list)
    inventory: List[Dict] = field(default_factory=list)
    
    @property
    def attack_power(self):
        if self.class_name in [GameClass.WARRIOR, GameClass.PALADIN]:
            return self.strength * 2
        elif self.class_name == GameClass.ARCHER:
            return self.agility * 2
        elif self.class_name in [GameClass.MAGE, GameClass.NECROMANCER]:
            return self.intelligence * 2
        else:
            return self.strength + self.agility
    
    @property
    def defense(self):
        return self.vitality * 1.5
    
    def take_damage(self, damage: int) -> int:
        reduced_damage = max(1, damage - int(self.defense / 2))
        self.hp = max(0, self.hp - reduced_damage)
        return reduced_damage
    
    def heal(self, amount: int):
        self.hp = min(self.max_hp, self.hp + amount)
    
    def restore_mana(self, amount: int):
        self.mana = min(self.max_mana, self.mana + amount)
    
    def gain_exp(self, amount: int) -> bool:
        self.exp += amount
        exp_needed = self.level * 100
        if self.exp >= exp_needed:
            self.level_up()
            return True
        return False
    
    def level_up(self):
        self.level += 1
        self.exp = 0
        self.max_hp += 20
        self.max_mana += 10
        self.hp = self.max_hp
        self.mana = self.max_mana
        self.strength += 2
        self.agility += 2
        self.intelligence += 2
        self.vitality += 2
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "class": self.class_name.value,
            "level": self.level,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "mana": self.mana,
            "max_mana": self.max_mana,
            "gold": self.gold,
            "x": self.x,
            "y": self.y,
            "map": self.map_id,
            "role": self.role
        }

@dataclass
class Enemy:
    id: str
    name: str
    level: int
    hp: int
    max_hp: int
    attack: int
    defense: int
    exp_reward: int
    gold_reward: int
    loot_table: List[Dict]
    skills: List[str] = field(default_factory=list)
    
    def take_damage(self, damage: int) -> int:
        reduced_damage = max(1, damage - self.defense)
        self.hp = max(0, self.hp - reduced_damage)
        return reduced_damage

# ==================== Combat Engine ====================

class CombatEngine:
    def __init__(self):
        self.active_combats: Dict[str, Dict] = {}
    
    async def calculate_damage(self, attacker: Player, defender, skill_id: Optional[str] = None) -> Dict:
        base_damage = attacker.attack_power
        
        # Critical hit
        crit_chance = attacker.agility / 100
        is_critical = random.random() < crit_chance
        if is_critical:
            base_damage = int(base_damage * 1.5)
        
        # Apply damage
        actual_damage = defender.take_damage(base_damage)
        
        return {
            "damage": actual_damage,
            "critical": is_critical,
            "target_hp": defender.hp,
            "target_max_hp": defender.max_hp,
            "message": f"Dealt {actual_damage} damage{' (CRITICAL!)' if is_critical else ''}"
        }

combat_engine = CombatEngine()

# ==================== Quest Engine ====================

class QuestEngine:
    def __init__(self):
        self.quests = self.load_quests()
    
    def load_quests(self) -> Dict:
        """Load all quests for the 5-act storyline"""
        return {
            # Act 1: Village Invasion
            "q1_1": {
                "id": "q1_1",
                "title": "The Dark Omen",
                "act": 1,
                "chapter": 1,
                "description": "Strange creatures have been seen near the village. Investigate the forest.",
                "requirements": {
                    "level": 1,
                    "kill": {"goblin": 5}
                },
                "rewards": {
                    "exp": 100,
                    "gold": 50
                },
                "next_quest": "q1_2"
            },
            "q1_2": {
                "id": "q1_2",
                "title": "The Goblin Camp",
                "act": 1,
                "chapter": 2,
                "description": "Find the goblin camp and discover who's leading them.",
                "requirements": {
                    "explore": "goblin_camp"
                },
                "rewards": {
                    "exp": 150,
                    "gold": 75
                },
                "next_quest": "q1_3"
            },
            "q1_3": {
                "id": "q1_3",
                "title": "Goblin Chief",
                "act": 1,
                "chapter": 3,
                "description": "Defeat the Goblin Chief leading the invasion.",
                "requirements": {
                    "kill": {"goblin_chief": 1}
                },
                "rewards": {
                    "exp": 300,
                    "gold": 150
                },
                "is_boss": True,
                "next_quest": "q2_1"
            },
            
            # Act 2: Kingdom War
            "q2_1": {
                "id": "q2_1",
                "title": "The King's Summons",
                "act": 2,
                "chapter": 1,
                "description": "The king has summoned all able warriors. Report to the capital.",
                "requirements": {
                    "level": 5,
                    "talk": "king"
                },
                "rewards": {
                    "exp": 250,
                    "gold": 200
                },
                "next_quest": "q2_2"
            },
            "q2_2": {
                "id": "q2_2",
                "title": "Border Skirmish",
                "act": 2,
                "chapter": 2,
                "description": "The northern border is under attack. Defend it.",
                "requirements": {
                    "kill": {"enemy_soldier": 10}
                },
                "rewards": {
                    "exp": 350,
                    "gold": 250
                },
                "next_quest": "q2_3"
            },
            "q2_3": {
                "id": "q2_3",
                "title": "Siege of the Capital",
                "act": 2,
                "chapter": 3,
                "description": "The enemy army has reached the capital. Defend the city!",
                "requirements": {
                    "survive_waves": 5
                },
                "rewards": {
                    "exp": 600,
                    "gold": 500
                },
                "is_boss": True,
                "next_quest": "q3_1"
            },
            
            # Act 3: Corruption Spreads
            "q3_1": {
                "id": "q3_1",
                "title": "The Plague",
                "act": 3,
                "chapter": 1,
                "description": "A strange plague is spreading across the land. Find the source.",
                "requirements": {
                    "level": 10,
                    "investigate": "plague_origin"
                },
                "rewards": {
                    "exp": 500,
                    "gold": 400
                },
                "next_quest": "q3_2"
            },
            "q3_2": {
                "id": "q3_2",
                "title": "Corrupted Forest",
                "act": 3,
                "chapter": 2,
                "description": "The ancient forest is being corrupted. Purify it.",
                "requirements": {
                    "kill": {"corrupted_creature": 15}
                },
                "rewards": {
                    "exp": 550,
                    "gold": 450
                },
                "next_quest": "q3_3"
            },
            "q3_3": {
                "id": "q3_3",
                "title": "The Corruptor",
                "act": 3,
                "chapter": 3,
                "description": "Defeat the Corruptor, a powerful being spreading the corruption.",
                "requirements": {
                    "kill": {"the_corruptor": 1}
                },
                "rewards": {
                    "exp": 800,
                    "gold": 600
                },
                "is_boss": True,
                "next_quest": "q4_1"
            },
            
            # Act 4: Time Fracture
            "q4_1": {
                "id": "q4_1",
                "title": "Temporal Distortion",
                "act": 4,
                "chapter": 1,
                "description": "Time is breaking apart. Investigate the temporal anomalies.",
                "requirements": {
                    "level": 15,
                    "explore": "time_rift"
                },
                "rewards": {
                    "exp": 700,
                    "gold": 600
                },
                "next_quest": "q4_2"
            },
            "q4_2": {
                "id": "q4_2",
                "title": "Past Sins",
                "act": 4,
                "chapter": 2,
                "description": "You're thrown into the past. Witness the original corruption.",
                "requirements": {
                    "survive": 1
                },
                "rewards": {
                    "exp": 750,
                    "gold": 650
                },
                "next_quest": "q4_3"
            },
            "q4_3": {
                "id": "q4_3",
                "title": "Time Keeper",
                "act": 4,
                "chapter": 3,
                "description": "Defeat the Time Keeper to stabilize the timeline.",
                "requirements": {
                    "kill": {"time_keeper": 1}
                },
                "rewards": {
                    "exp": 1000,
                    "gold": 800
                },
                "is_boss": True,
                "next_quest": "q5_1"
            },
            
            # Act 5: Multiple Endings
            "q5_1": {
                "id": "q5_1",
                "title": "The Final Confrontation",
                "act": 5,
                "chapter": 1,
                "description": "Face the source of all evil - The Void Lord.",
                "requirements": {
                    "level": 20,
                    "kill": {"void_lord": 1}
                },
                "rewards": {
                    "exp": 2000,
                    "gold": 2000
                },
                "is_boss": True,
                "choices": [
                    {
                        "id": "ending_hero",
                        "text": "Sacrifice yourself to destroy the Void Lord",
                        "ending": "The Hero's Sacrifice - You become a legend"
                    },
                    {
                        "id": "ending_dark",
                        "text": "Embrace the void and become the new Void Lord",
                        "ending": "The Dark Ascension - You rule the void"
                    },
                    {
                        "id": "ending_balance",
                        "text": "Balance light and dark, seal the void away",
                        "ending": "The Balance - Peace is restored"
                    }
                ]
            }
        }
    
    async def check_quest_progress(self, character_id: int, quest_id: str, event_type: str, event_data: Dict) -> Dict:
        quest = self.quests.get(quest_id)
        if not quest:
            return {"success": False, "message": "Quest not found"}
        
        return {"success": True, "completed": False}
    
    async def start_quest(self, character_id: int, quest_id: str):
        pass

quest_engine = QuestEngine()

# ==================== Auto-Register Owner ====================

async def ensure_owner_exists():
    """Automatically create owner account if it doesn't exist"""
    try:
        # Check if owner exists
        result = supabase.table('users').select('*').eq('username', OWNER_USERNAME).execute()
        
        if not result.data:
            print(f"🔐 Creating owner account: {OWNER_USERNAME}")
            password_hash = hash_password(OWNER_PASSWORD)
            
            owner_result = supabase.table('users').insert({
                'username': OWNER_USERNAME,
                'password_hash': password_hash,
                'role': 'owner',
                'is_owner': True,
                'created_at': datetime.datetime.now().isoformat()
            }).execute()
            
            if owner_result.data:
                print(f"✅ Owner account created successfully")
                
                # Create a default character for owner
                owner_id = owner_result.data[0]['id']
                supabase.table('characters').insert({
                    'user_id': owner_id,
                    'name': f"{OWNER_USERNAME}_admin",
                    'class_name': 'warrior',
                    'level': 999,
                    'exp': 999999,
                    'gold': 999999,
                    'hp': 9999,
                    'max_hp': 9999,
                    'mana': 9999,
                    'max_mana': 9999,
                    'strength': 999,
                    'agility': 999,
                    'intelligence': 999,
                    'vitality': 999,
                    'role': 'owner'
                }).execute()
                print(f"✅ Owner character created")
        else:
            print(f"✅ Owner account already exists")
            
    except Exception as e:
        print(f"⚠️ Error ensuring owner exists: {e}")

# ==================== Security ====================

security = HTTPBearer()

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    result = supabase.table('users').select('*').eq('id', user_id).execute()
    
    if not result.data:
        raise HTTPException(status_code=401, detail="User not found")
    
    return result.data[0]

async def require_owner(user = Depends(get_current_user)):
    """Require owner role"""
    if not user.get('is_owner', False) and user.get('username') != OWNER_USERNAME:
        raise HTTPException(status_code=403, detail="Owner access required")
    return user

async def require_admin(user = Depends(get_current_user)):
    """Require admin or owner role"""
    role = user.get('role', 'player')
    if role not in ['admin', 'owner'] and not user.get('is_owner', False):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

async def require_mod(user = Depends(get_current_user)):
    """Require moderator, admin, or owner role"""
    role = user.get('role', 'player')
    if role not in ['moderator', 'admin', 'owner'] and not user.get('is_owner', False):
        raise HTTPException(status_code=403, detail="Moderator access required")
    return user

# ==================== WebSocket Manager ====================

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}
        self.user_roles: Dict[int, str] = {}
        self.rooms: Dict[str, Set[int]] = {
            "global": set(),
            "trade": set(),
            "pvp": set(),
            "mod": set(),    # Moderator room
            "admin": set()   # Admin room
        }
        self.banned_users: Set[int] = set()
        self.muted_users: Dict[int, datetime.datetime] = {}
        self.chat_history: List[Dict] = []
    
    async def connect(self, websocket: WebSocket, user_id: int, role: str = "player"):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        self.user_roles[user_id] = role
        self.rooms["global"].add(user_id)
        
        # Add to mod/admin rooms if applicable
        if role in ["moderator", "admin", "owner"]:
            self.rooms["mod"].add(user_id)
        if role in ["admin", "owner"]:
            self.rooms["admin"].add(user_id)
    
    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        if user_id in self.user_roles:
            del self.user_roles[user_id]
        for room in self.rooms.values():
            room.discard(user_id)
    
    async def send_personal_message(self, user_id: int, message: dict):
        if user_id in self.active_connections and user_id not in self.banned_users:
            try:
                await self.active_connections[user_id].send_json(message)
            except:
                pass
    
    async def broadcast_to_room(self, room: str, message: dict, exclude_user: int = None):
        if room in self.rooms:
            for user_id in self.rooms[room]:
                if exclude_user and user_id == exclude_user:
                    continue
                await self.send_personal_message(user_id, message)
    
    async def handle_chat_message(self, user_id: int, message: ChatMessage):
        # Check if user is muted
        if user_id in self.muted_users:
            mute_until = self.muted_users[user_id]
            if datetime.datetime.now() < mute_until:
                await self.send_personal_message(user_id, {
                    "type": "error",
                    "message": f"You are muted until {mute_until.strftime('%H:%M:%S')}"
                })
                return
            else:
                del self.muted_users[user_id]
        
        # Get character name and role
        result = supabase.table('characters')\
            .select('name, role')\
            .eq('user_id', user_id)\
            .execute()
        
        character_name = result.data[0]['name'] if result.data else f"User_{user_id}"
        role = self.user_roles.get(user_id, "player")
        
        # Add role tag to sender name
        role_tag = ""
        if role == "owner":
            role_tag = "👑 "
        elif role == "admin":
            role_tag = "⚡ "
        elif role == "moderator":
            role_tag = "🛡️ "
        
        chat_entry = {
            "type": "chat",
            "channel": message.channel,
            "sender": f"{role_tag}{character_name}",
            "sender_id": user_id,
            "role": role,
            "content": message.content,
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        # Store in history
        self.chat_history.append(chat_entry)
        if len(self.chat_history) > 100:
            self.chat_history.pop(0)
        
        # Handle special channels
        if message.channel == ChatChannel.MOD:
            await self.broadcast_to_room("mod", chat_entry)
        elif message.channel == ChatChannel.ADMIN:
            await self.broadcast_to_room("admin", chat_entry)
        else:
            await self.broadcast_to_room("global", chat_entry)

manager = ConnectionManager()

# ==================== FastAPI App ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("=" * 60)
    print("🚀 Starting Pixel RPG Server")
    print("=" * 60)
    await ensure_owner_exists()
    print("✅ Server ready")
    yield
    # Shutdown
    print("🛑 Server shutting down")

app = FastAPI(lifespan=lifespan)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== Auth Routes ====================

@app.post("/api/register", response_model=TokenResponse)
async def register(user: UserCreate):
    # Check if user exists
    result = supabase.table('users').select('*').eq('username', user.username).execute()
    if result.data:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    # Check if this is the owner
    is_owner = (user.username == OWNER_USERNAME and user.password == OWNER_PASSWORD)
    role = "owner" if is_owner else "player"
    
    # Create user
    password_hash = hash_password(user.password)
    
    result = supabase.table('users').insert({
        'username': user.username,
        'password_hash': password_hash,
        'role': role,
        'is_owner': is_owner,
        'created_at': datetime.datetime.now().isoformat()
    }).execute()
    
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create user")
    
    user_id = result.data[0]['id']
    token = create_access_token({"sub": str(user_id), "role": role, "is_owner": is_owner})
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": role,
        "is_owner": is_owner
    }

@app.post("/api/login", response_model=TokenResponse)
async def login(user: UserLogin):
    result = supabase.table('users').select('*').eq('username', user.username).execute()
    
    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    db_user = result.data[0]
    
    if not verify_password(user.password, db_user['password_hash']):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_access_token({
        "sub": str(db_user['id']),
        "role": db_user.get('role', 'player'),
        "is_owner": db_user.get('is_owner', False)
    })
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": db_user.get('role', 'player'),
        "is_owner": db_user.get('is_owner', False)
    }

# ==================== Character Routes ====================

@app.post("/api/characters", response_model=CharacterResponse)
async def create_character(character: CharacterCreate, user=Depends(get_current_user)):
    # Check if character name exists
    result = supabase.table('characters').select('*').eq('name', character.name).execute()
    if result.data:
        raise HTTPException(status_code=400, detail="Character name already exists")
    
    base_stats = {
        GameClass.WARRIOR: {"hp": 120, "mana": 40, "strength": 15, "agility": 8, "intelligence": 5, "vitality": 14},
        GameClass.MAGE: {"hp": 80, "mana": 100, "strength": 5, "agility": 7, "intelligence": 18, "vitality": 8},
        GameClass.ROGUE: {"hp": 90, "mana": 60, "strength": 10, "agility": 18, "intelligence": 7, "vitality": 10},
        GameClass.ARCHER: {"hp": 95, "mana": 55, "strength": 8, "agility": 17, "intelligence": 8, "vitality": 11},
        GameClass.PALADIN: {"hp": 110, "mana": 60, "strength": 12, "agility": 6, "intelligence": 10, "vitality": 15},
        GameClass.NECROMANCER: {"hp": 85, "mana": 110, "strength": 6, "agility": 6, "intelligence": 17, "vitality": 9}
    }
    
    stats = base_stats[character.class_name]
    
    # Owner gets boosted stats
    role = user.get('role', 'player')
    if role == "owner" or user.get('is_owner', False):
        stats = {k: v * 10 for k, v in stats.items()}
        starting_gold = 100000
        starting_level = 100
    else:
        starting_gold = 100
        starting_level = 1
    
    result = supabase.table('characters').insert({
        'user_id': user['id'],
        'name': character.name,
        'class_name': character.class_name.value,
        'hp': stats["hp"],
        'max_hp': stats["hp"],
        'mana': stats["mana"],
        'max_mana': stats["mana"],
        'strength': stats["strength"],
        'agility': stats["agility"],
        'intelligence': stats["intelligence"],
        'vitality': stats["vitality"],
        'gold': starting_gold,
        'level': starting_level,
        'role': role
    }).execute()
    
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create character")
    
    character_data = result.data[0]
    
    return CharacterResponse(
        id=character_data['id'],
        name=character_data['name'],
        class_name=character.class_name,
        level=character_data['level'],
        exp=character_data['exp'],
        gold=character_data['gold'],
        hp=character_data['hp'],
        max_hp=character_data['max_hp'],
        mana=character_data['mana'],
        max_mana=character_data['max_mana'],
        strength=character_data['strength'],
        agility=character_data['agility'],
        intelligence=character_data['intelligence'],
        vitality=character_data['vitality'],
        role=role
    )

@app.get("/api/characters", response_model=List[CharacterResponse])
async def get_characters(user=Depends(get_current_user)):
    result = supabase.table('characters')\
        .select('*')\
        .eq('user_id', user['id'])\
        .execute()
    
    characters = []
    for row in result.data:
        characters.append(CharacterResponse(
            id=row['id'],
            name=row['name'],
            class_name=GameClass(row['class_name']),
            level=row['level'],
            exp=row['exp'],
            gold=row['gold'],
            hp=row['hp'],
            max_hp=row['max_hp'],
            mana=row['mana'],
            max_mana=row['max_mana'],
            strength=row['strength'],
            agility=row['agility'],
            intelligence=row['intelligence'],
            vitality=row['vitality'],
            role=row.get('role', 'player')
        ))
    
    return characters

@app.get("/api/characters/{character_id}")
async def get_character(character_id: int, user=Depends(get_current_user)):
    char_result = supabase.table('characters')\
        .select('*')\
        .eq('id', character_id)\
        .eq('user_id', user['id'])\
        .execute()
    
    if not char_result.data:
        raise HTTPException(status_code=404, detail="Character not found")
    
    character = char_result.data[0]
    
    # Get inventory
    inv_result = supabase.table('inventory')\
        .select('*')\
        .eq('character_id', character_id)\
        .execute()
    
    return {
        "character": character,
        "inventory": inv_result.data
    }

# ==================== Game Routes ====================

@app.post("/api/characters/{character_id}/move")
async def move_character(character_id: int, x: int, y: int, user=Depends(get_current_user)):
    supabase.table('characters')\
        .update({'x_position': x, 'y_position': y})\
        .eq('id', character_id)\
        .eq('user_id', user['id'])\
        .execute()
    
    await manager.broadcast_to_room("global", {
        "type": "movement",
        "character_id": character_id,
        "x": x,
        "y": y
    }, exclude_user=user['id'])
    
    return {"success": True}

@app.post("/api/characters/{character_id}/combat/{enemy_id}")
async def start_combat(character_id: int, enemy_id: str, user=Depends(get_current_user)):
    char_result = supabase.table('characters')\
        .select('*')\
        .eq('id', character_id)\
        .eq('user_id', user['id'])\
        .execute()
    
    if not char_result.data:
        raise HTTPException(status_code=404, detail="Character not found")
    
    char_data = char_result.data[0]
    
    player = Player(
        id=char_data["id"],
        user_id=char_data["user_id"],
        name=char_data["name"],
        class_name=GameClass(char_data["class_name"]),
        level=char_data["level"],
        exp=char_data["exp"],
        gold=char_data["gold"],
        hp=char_data["hp"],
        max_hp=char_data["max_hp"],
        mana=char_data["mana"],
        max_mana=char_data["max_mana"],
        strength=char_data["strength"],
        agility=char_data["agility"],
        intelligence=char_data["intelligence"],
        vitality=char_data["vitality"],
        role=char_data.get('role', 'player')
    )
    
    enemies = {
        "goblin": Enemy(
            id="goblin",
            name="Goblin",
            level=1,
            hp=50,
            max_hp=50,
            attack=8,
            defense=2,
            exp_reward=25,
            gold_reward=10,
            loot_table=[]
        ),
        "goblin_chief": Enemy(
            id="goblin_chief",
            name="Goblin Chief",
            level=3,
            hp=150,
            max_hp=150,
            attack=15,
            defense=5,
            exp_reward=100,
            gold_reward=50,
            loot_table=[]
        ),
        "the_corruptor": Enemy(
            id="the_corruptor",
            name="The Corruptor",
            level=10,
            hp=500,
            max_hp=500,
            attack=30,
            defense=15,
            exp_reward=500,
            gold_reward=300,
            loot_table=[]
        ),
        "time_keeper": Enemy(
            id="time_keeper",
            name="Time Keeper",
            level=15,
            hp=1000,
            max_hp=1000,
            attack=45,
            defense=25,
            exp_reward=800,
            gold_reward=600,
            loot_table=[]
        ),
        "void_lord": Enemy(
            id="void_lord",
            name="Void Lord",
            level=20,
            hp=2000,
            max_hp=2000,
            attack=60,
            defense=30,
            exp_reward=1500,
            gold_reward=1000,
            loot_table=[]
        )
    }
    
    enemy = enemies.get(enemy_id)
    if not enemy:
        raise HTTPException(status_code=404, detail="Enemy not found")
    
    combat_log = []
    
    while player.hp > 0 and enemy.hp > 0:
        damage_result = await combat_engine.calculate_damage(player, enemy, None)
        combat_log.append(damage_result)
        
        if enemy.hp <= 0:
            break
        
        enemy_damage = max(1, enemy.attack - int(player.defense / 2))
        player.take_damage(enemy_damage)
        combat_log.append({
            "turn": len(combat_log) + 1,
            "attacker": "enemy",
            "damage": enemy_damage,
            "player_hp": player.hp
        })
    
    victory = player.hp > 0
    
    if victory:
        player.gain_exp(enemy.exp_reward)
        player.gold += enemy.gold_reward
        
        supabase.table('characters')\
            .update({
                'hp': player.hp,
                'mana': player.mana,
                'exp': player.exp,
                'gold': player.gold,
                'level': player.level
            })\
            .eq('id', character_id)\
            .execute()
    
    return {
        "victory": victory,
        "player_hp": player.hp,
        "enemy_hp": enemy.hp,
        "exp_gained": enemy.exp_reward if victory else 0,
        "gold_gained": enemy.gold_reward if victory else 0,
        "combat_log": combat_log
    }

# ==================== Trading Routes ====================

@app.post("/api/trade/initiate/{target_character_id}")
async def initiate_trade(target_character_id: int, user=Depends(get_current_user)):
    result = supabase.table('characters')\
        .select('id')\
        .eq('user_id', user['id'])\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Character not found")
    
    initiator_id = result.data[0]['id']
    
    trade_id = random.randint(1000, 9999)
    
    target_result = supabase.table('characters')\
        .select('user_id')\
        .eq('id', target_character_id)\
        .execute()
    
    if target_result.data:
        await manager.send_personal_message(target_result.data[0]['user_id'], {
            "type": "trade_request",
            "trade_id": trade_id,
            "from_character": initiator_id
        })
    
    return {"trade_id": trade_id}

@app.post("/api/trade/{trade_id}/accept")
async def accept_trade(trade_id: int, user=Depends(get_current_user)):
    return {"success": True}

@app.post("/api/trade/{trade_id}/complete")
async def complete_trade(trade_id: int, user=Depends(get_current_user)):
    return {"success": True}

# ==================== PvP Routes ====================

@app.post("/api/pvp/challenge/{target_character_id}")
async def challenge_pvp(target_character_id: int, user=Depends(get_current_user)):
    result = supabase.table('characters')\
        .select('id')\
        .eq('user_id', user['id'])\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Character not found")
    
    challenger_id = result.data[0]['id']
    match_id = random.randint(1000, 9999)
    
    target_result = supabase.table('characters')\
        .select('user_id')\
        .eq('id', target_character_id)\
        .execute()
    
    if target_result.data:
        await manager.send_personal_message(target_result.data[0]['user_id'], {
            "type": "pvp_challenge",
            "match_id": match_id,
            "challenger": challenger_id
        })
    
    return {"match_id": match_id}

@app.post("/api/pvp/match/{match_id}/accept")
async def accept_pvp(match_id: int, user=Depends(get_current_user)):
    """Accept and resolve a PvP match with full combat calculation"""
    
    # Get the match from database
    match_result = supabase.table('pvp_matches')\
        .select('*')\
        .eq('id', match_id)\
        .eq('status', 'pending')\
        .execute()
    
    if not match_result.data:
        raise HTTPException(status_code=404, detail="Match not found or already completed")
    
    match = match_result.data[0]
    
    # Get current user's character
    char_result = supabase.table('characters')\
        .select('*')\
        .eq('user_id', user['id'])\
        .execute()
    
    if not char_result.data:
        raise HTTPException(status_code=404, detail="Character not found")
    
    current_char = char_result.data[0]
    
    # Verify this user is part of the match
    if current_char['id'] not in [match['player1_id'], match['player2_id']]:
        raise HTTPException(status_code=403, detail="You are not part of this match")
    
    # Get both players' full character data
    player1_result = supabase.table('characters')\
        .select('*')\
        .eq('id', match['player1_id'])\
        .execute()
    
    player2_result = supabase.table('characters')\
        .select('*')\
        .eq('id', match['player2_id'])\
        .execute()
    
    if not player1_result.data or not player2_result.data:
        raise HTTPException(status_code=404, detail="Player data not found")
    
    player1_data = player1_result.data[0]
    player2_data = player2_result.data[0]
    
    # Create Player objects for combat
    player1 = Player(
        id=player1_data['id'],
        user_id=player1_data['user_id'],
        name=player1_data['name'],
        class_name=GameClass(player1_data['class_name']),
        level=player1_data['level'],
        exp=player1_data['exp'],
        gold=player1_data['gold'],
        hp=player1_data['hp'],
        max_hp=player1_data['max_hp'],
        mana=player1_data['mana'],
        max_mana=player1_data['max_mana'],
        strength=player1_data['strength'],
        agility=player1_data['agility'],
        intelligence=player1_data['intelligence'],
        vitality=player1_data['vitality'],
        role=player1_data.get('role', 'player')
    )
    
    player2 = Player(
        id=player2_data['id'],
        user_id=player2_data['user_id'],
        name=player2_data['name'],
        class_name=GameClass(player2_data['class_name']),
        level=player2_data['level'],
        exp=player2_data['exp'],
        gold=player2_data['gold'],
        hp=player2_data['hp'],
        max_hp=player2_data['max_hp'],
        mana=player2_data['mana'],
        max_mana=player2_data['max_mana'],
        strength=player2_data['strength'],
        agility=player2_data['agility'],
        intelligence=player2_data['intelligence'],
        vitality=player2_data['vitality'],
        role=player2_data.get('role', 'player')
    )
    
    # Initialize combat
    combat_log = []
    turn = 1
    max_turns = 50  # Prevent infinite loops
    
    # Determine who goes first based on agility
    first_attacker = player1 if player1.agility >= player2.agility else player2
    second_attacker = player2 if first_attacker == player1 else player1
    
    combat_log.append({
        "turn": 0,
        "message": f"Match started: {player1.name} ({player1.class_name.value}) vs {player2.name} ({player2.class_name.value})",
        "first_attacker": first_attacker.name
    })
    
    # Combat loop
    while player1.hp > 0 and player2.hp > 0 and turn <= max_turns:
        # First attacker's turn
        if player1.hp > 0 and player2.hp > 0:
            # Calculate damage with some randomness and critical hits
            base_damage = first_attacker.attack_power
            crit_chance = first_attacker.agility / 200  # 0.5% per agility point
            is_critical = random.random() < crit_chance
            
            if is_critical:
                base_damage = int(base_damage * 1.8)  # Critical hit does 80% more damage
            
            # Defense reduces damage
            defense = second_attacker.defense
            damage = max(1, base_damage - int(defense / 3))
            
            # Apply damage
            second_attacker.hp -= damage
            
            combat_log.append({
                "turn": turn,
                "attacker": first_attacker.name,
                "defender": second_attacker.name,
                "damage": damage,
                "critical": is_critical,
                "defender_hp": max(0, second_attacker.hp),
                "message": f"{first_attacker.name} hits {second_attacker.name} for {damage} damage{' (CRITICAL!)' if is_critical else ''}"
            })
        
        # Check if defender died
        if second_attacker.hp <= 0:
            break
        
        turn += 1
        
        # Second attacker's turn
        if player1.hp > 0 and player2.hp > 0:
            # Swap roles
            base_damage = second_attacker.attack_power
            crit_chance = second_attacker.agility / 200
            is_critical = random.random() < crit_chance
            
            if is_critical:
                base_damage = int(base_damage * 1.8)
            
            defense = first_attacker.defense
            damage = max(1, base_damage - int(defense / 3))
            
            first_attacker.hp -= damage
            
            combat_log.append({
                "turn": turn,
                "attacker": second_attacker.name,
                "defender": first_attacker.name,
                "damage": damage,
                "critical": is_critical,
                "defender_hp": max(0, first_attacker.hp),
                "message": f"{second_attacker.name} hits {first_attacker.name} for {damage} damage{' (CRITICAL!)' if is_critical else ''}"
            })
        
        turn += 1
    
    # Determine winner
    winner_id = None
    loser_id = None
    winner_data = None
    loser_data = None
    
    if player1.hp <= 0:
        winner_id = player2.id
        loser_id = player1.id
        winner_data = player2
        loser_data = player1
    elif player2.hp <= 0:
        winner_id = player1.id
        loser_id = player2.id
        winner_data = player1
        loser_data = player2
    else:
        # Match ended by turn limit - winner is the one with more HP
        if player1.hp > player2.hp:
            winner_id = player1.id
            loser_id = player2.id
            winner_data = player1
            loser_data = player2
        elif player2.hp > player1.hp:
            winner_id = player2.id
            loser_id = player1.id
            winner_data = player2
            loser_data = player1
        else:
            # Tie - both get partial rewards
            winner_id = None
            loser_id = None
    
    # Calculate rewards
    pvp_rewards = {}
    if winner_id:
        # Winner gets gold and XP based on loser's level
        gold_reward = 50 + (loser_data.level * 10)
        exp_reward = 100 + (loser_data.level * 20)
        
        # Update winner in database
        supabase.table('characters')\
            .update({
                'gold': winner_data.gold + gold_reward,
                'exp': winner_data.exp + exp_reward,
                'hp': winner_data.hp  # Keep remaining HP
            })\
            .eq('id', winner_id)\
            .execute()
        
        # Update loser in database (they keep their HP too)
        supabase.table('characters')\
            .update({
                'hp': loser_data.hp
            })\
            .eq('id', loser_id)\
            .execute()
        
        pvp_rewards = {
            "winner": {
                "id": winner_id,
                "name": winner_data.name,
                "gold_earned": gold_reward,
                "exp_earned": exp_reward
            },
            "loser": {
                "id": loser_id,
                "name": loser_data.name
            }
        }
        
        # Record win/loss for leaderboard
        supabase.table('pvp_stats').upsert({
            'character_id': winner_id,
            'wins': supabase.table('pvp_stats').select('wins').eq('character_id', winner_id).execute().data[0].get('wins', 0) + 1 if supabase.table('pvp_stats').select('wins').eq('character_id', winner_id).execute().data else 1
        }).execute()
        
        supabase.table('pvp_stats').upsert({
            'character_id': loser_id,
            'losses': supabase.table('pvp_stats').select('losses').eq('character_id', loser_id).execute().data[0].get('losses', 0) + 1 if supabase.table('pvp_stats').select('losses').eq('character_id', loser_id).execute().data else 1
        }).execute()
        
    else:
        # Tie game - both get reduced rewards
        tie_gold = 30
        tie_exp = 50
        
        supabase.table('characters')\
            .update({
                'gold': player1.gold + tie_gold,
                'exp': player1.exp + tie_exp,
                'hp': player1.hp
            })\
            .eq('id', player1.id)\
            .execute()
        
        supabase.table('characters')\
            .update({
                'gold': player2.gold + tie_gold,
                'exp': player2.exp + tie_exp,
                'hp': player2.hp
            })\
            .eq('id', player2.id)\
            .execute()
        
        pvp_rewards = {
            "tie": True,
            "gold_earned": tie_gold,
            "exp_earned": tie_exp
        }
    
    # Update match record
    end_time = datetime.datetime.now().isoformat()
    supabase.table('pvp_matches')\
        .update({
            'status': 'completed',
            'winner_id': winner_id,
            'ended_at': end_time,
            'match_data': {
                'combat_log': combat_log,
                'turns': turn,
                'player1_final_hp': player1.hp,
                'player2_final_hp': player2.hp
            }
        })\
        .eq('id', match_id)\
        .execute()
    
    # Notify both players via WebSocket if they're online
    await manager.send_personal_message(player1.user_id, {
        "type": "pvp_result",
        "match_id": match_id,
        "winner_id": winner_id,
        "rewards": pvp_rewards,
        "combat_log": combat_log[-10:]  # Last 10 actions
    })
    
    await manager.send_personal_message(player2.user_id, {
        "type": "pvp_result",
        "match_id": match_id,
        "winner_id": winner_id,
        "rewards": pvp_rewards,
        "combat_log": combat_log[-10:]  # Last 10 actions
    })
    
    # Calculate stats
    player1_damage_dealt = sum(log['damage'] for log in combat_log if log.get('attacker') == player1.name)
    player2_damage_dealt = sum(log['damage'] for log in combat_log if log.get('attacker') == player2.name)
    
    return {
        "success": True,
        "match_id": match_id,
        "winner_id": winner_id,
        "player1": {
            "id": player1.id,
            "name": player1.name,
            "class": player1.class_name.value,
            "level": player1.level,
            "final_hp": player1.hp,
            "max_hp": player1.max_hp,
            "damage_dealt": player1_damage_dealt
        },
        "player2": {
            "id": player2.id,
            "name": player2.name,
            "class": player2.class_name.value,
            "level": player2.level,
            "final_hp": player2.hp,
            "max_hp": player2.max_hp,
            "damage_dealt": player2_damage_dealt
        },
        "rewards": pvp_rewards,
        "combat_summary": {
            "total_turns": turn - 1,
            "decisive_victory": winner_id is not None,
            "first_attacker": first_attacker.name,
            "combat_log": combat_log  # Full log for detailed viewing
        },
        "ended_at": end_time
    }

# ==================== Guild Routes ====================

@app.post("/api/guilds")
async def create_guild(guild: GuildCreate, user=Depends(get_current_user)):
    result = supabase.table('characters')\
        .select('id')\
        .eq('user_id', user['id'])\
        .execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="Character not found")
    
    character_id = result.data[0]['id']
    
    guild_result = supabase.table('guilds').insert({
        'name': guild.name,
        'tag': guild.tag,
        'leader_id': character_id
    }).execute()
    
    # Add leader as member
    supabase.table('guild_members').insert({
        'guild_id': guild_result.data[0]['id'],
        'character_id': character_id,
        'rank': 'leader'
    }).execute()
    
    return {"id": guild_result.data[0]['id'], "name": guild.name, "tag": guild.tag}

@app.get("/api/guilds/my-guild")
async def get_my_guild(user=Depends(get_current_user)):
    # Get user's character
    char_result = supabase.table('characters')\
        .select('id')\
        .eq('user_id', user['id'])\
        .execute()
    
    if not char_result.data:
        return {"name": None, "tag": None, "members": []}
    
    character_id = char_result.data[0]['id']
    
    # Get guild membership
    member_result = supabase.table('guild_members')\
        .select('guild_id')\
        .eq('character_id', character_id)\
        .execute()
    
    if not member_result.data:
        return {"name": None, "tag": None, "members": []}
    
    guild_id = member_result.data[0]['guild_id']
    
    # Get guild info
    guild_result = supabase.table('guilds')\
        .select('*')\
        .eq('id', guild_id)\
        .execute()
    
    if not guild_result.data:
        return {"name": None, "tag": None, "members": []}
    
    guild = guild_result.data[0]
    
    # Get members
    members_result = supabase.table('guild_members')\
        .select('*, characters(name, level)')\
        .eq('guild_id', guild_id)\
        .execute()
    
    members = []
    for m in members_result.data:
        members.append({
            "rank": m['rank'],
            "name": m['characters']['name'],
            "level": m['characters']['level']
        })
    
    return {
        "name": guild['name'],
        "tag": guild['tag'],
        "members": members
    }

# ==================== Auction Routes ====================

@app.get("/api/auction/listings")
async def get_auctions(user=Depends(get_current_user)):
    result = supabase.table('auctions')\
        .select('*, inventory(item_id)')\
        .eq('status', 'active')\
        .execute()
    
    auctions = []
    for auction in result.data:
        auctions.append({
            "id": auction['id'],
            "item_id": auction['inventory']['item_id'],
            "current_bid": auction['current_bid'] or auction['starting_bid'],
            "starting_bid": auction['starting_bid'],
            "buyout_price": auction['buyout_price']
        })
    
    return auctions

@app.post("/api/auction/{auction_id}/bid")
async def place_bid(auction_id: int, bid_amount: int, user=Depends(get_current_user)):
    # Get character
    char_result = supabase.table('characters')\
        .select('id, gold')\
        .eq('user_id', user['id'])\
        .execute()
    
    if not char_result.data:
        raise HTTPException(status_code=404, detail="Character not found")
    
    character = char_result.data[0]
    
    # Check if user has enough gold
    if character['gold'] < bid_amount:
        raise HTTPException(status_code=400, detail="Not enough gold")
    
    # Update auction
    supabase.table('auctions')\
        .update({
            'current_bid': bid_amount,
            'current_bidder_id': character['id']
        })\
        .eq('id', auction_id)\
        .execute()
    
    return {"success": True}

# ==================== Leaderboard Routes ====================

@app.get("/api/leaderboard/level")
async def level_leaderboard(limit: int = 10):
    result = supabase.table('characters')\
        .select('name, class_name, level, exp')\
        .order('level', desc=True)\
        .order('exp', desc=True)\
        .limit(limit)\
        .execute()
    
    return result.data

@app.get("/api/leaderboard/gold")
async def gold_leaderboard(limit: int = 10):
    result = supabase.table('characters')\
        .select('name, class_name, gold')\
        .order('gold', desc=True)\
        .limit(limit)\
        .execute()
    
    return result.data

@app.get("/api/leaderboard/pvp")
async def pvp_leaderboard(limit: int = 10):
    # Simplified - just return top players by level
    result = supabase.table('characters')\
        .select('name, class_name, level')\
        .order('level', desc=True)\
        .limit(limit)\
        .execute()
    
    return result.data

# ==================== Quest Routes ====================

@app.get("/api/quests")
async def get_quests(act: Optional[int] = None):
    quests = quest_engine.quests
    if act:
        quests = {k: v for k, v in quests.items() if v.get("act") == act}
    return list(quests.values())

@app.post("/api/characters/{character_id}/quests/{quest_id}/start")
async def start_quest_route(character_id: int, quest_id: str, user=Depends(get_current_user)):
    await quest_engine.start_quest(character_id, quest_id)
    return {"success": True}

# ==================== Owner/Admin Routes ====================

@app.post("/api/admin/cheat")
async def cheat_command(
    command: CheatCommand,
    user = Depends(require_owner)
):
    """Owner-only cheat commands"""
    try:
        target_user = command.target
        target_char = None
        
        if target_user:
            # Find target character by name
            result = supabase.table('characters')\
                .select('*')\
                .eq('name', target_user)\
                .execute()
            if result.data:
                target_char = result.data[0]
            else:
                # Try to find by user_id
                result = supabase.table('characters')\
                    .select('*')\
                    .eq('user_id', target_user)\
                    .execute()
                if result.data:
                    target_char = result.data[0]
        
        # If no target specified, use current user's character
        if not target_char:
            result = supabase.table('characters')\
                .select('*')\
                .eq('user_id', user['id'])\
                .execute()
            if result.data:
                target_char = result.data[0]
        
        if not target_char:
            return {"success": False, "message": "Target character not found"}
        
        if command.command == "give_gold":
            amount = command.amount or 1000
            supabase.table('characters')\
                .update({'gold': target_char['gold'] + amount})\
                .eq('id', target_char['id'])\
                .execute()
            
            # Notify target if online
            await manager.send_personal_message(target_char['user_id'], {
                "type": "cheat",
                "message": f"You received {amount} gold from owner"
            })
            
            return {"success": True, "message": f"Gave {amount} gold to {target_char['name']}"}
        
        elif command.command == "set_level":
            amount = command.amount or 100
            supabase.table('characters')\
                .update({'level': amount})\
                .eq('id', target_char['id'])\
                .execute()
            
            await manager.send_personal_message(target_char['user_id'], {
                "type": "cheat",
                "message": f"Your level has been set to {amount}"
            })
            
            return {"success": True, "message": f"Set {target_char['name']} to level {amount}"}
        
        elif command.command == "spawn_item":
            if command.item_id:
                supabase.table('inventory').insert({
                    'character_id': target_char['id'],
                    'item_id': command.item_id,
                    'quantity': command.amount or 1
                }).execute()
                
                await manager.send_personal_message(target_char['user_id'], {
                    "type": "cheat",
                    "message": f"You received item: {command.item_id}"
                })
                
                return {"success": True, "message": f"Spawned {command.item_id} for {target_char['name']}"}
        
        elif command.command == "god_mode":
            # Set insane stats
            supabase.table('characters')\
                .update({
                    'hp': 99999,
                    'max_hp': 99999,
                    'mana': 99999,
                    'max_mana': 99999,
                    'strength': 9999,
                    'agility': 9999,
                    'intelligence': 9999,
                    'vitality': 9999
                })\
                .eq('id', target_char['id'])\
                .execute()
            
            await manager.send_personal_message(target_char['user_id'], {
                "type": "cheat",
                "message": "God mode activated!"
            })
            
            return {"success": True, "message": f"God mode activated for {target_char['name']}"}
        
        elif command.command == "kill":
            # Set HP to 0
            supabase.table('characters')\
                .update({'hp': 0})\
                .eq('id', target_char['id'])\
                .execute()
            
            await manager.send_personal_message(target_char['user_id'], {
                "type": "cheat",
                "message": "You have been killed by owner"
            })
            
            return {"success": True, "message": f"Killed {target_char['name']}"}
        
        elif command.command == "heal":
            # Restore to full HP
            supabase.table('characters')\
                .update({'hp': target_char['max_hp']})\
                .eq('id', target_char['id'])\
                .execute()
            
            await manager.send_personal_message(target_char['user_id'], {
                "type": "cheat",
                "message": "You have been fully healed"
            })
            
            return {"success": True, "message": f"Healed {target_char['name']}"}
        
        return {"success": False, "message": "Invalid command"}
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/mod/action")
async def mod_action(
    action: ModAction,
    user = Depends(require_mod)
):
    """Moderator actions (kick, ban, mute, warn, unmute, unban)"""
    try:
        target = action.target_user_id
        
        if action.action == "kick":
            # Disconnect user
            if target in manager.active_connections:
                await manager.send_personal_message(target, {
                    "type": "system",
                    "message": f"You have been kicked. Reason: {action.reason}"
                })
                await manager.active_connections[target].close()
                manager.disconnect(target)
            
            # Log the action
            supabase.table('mod_logs').insert({
                'moderator_id': user['id'],
                'target_id': target,
                'action': 'kick',
                'reason': action.reason,
                'timestamp': datetime.datetime.now().isoformat()
            }).execute()
            
            return {"success": True, "message": f"Kicked user {target}"}
        
        elif action.action == "ban":
            # Ban user
            manager.banned_users.add(target)
            if target in manager.active_connections:
                await manager.active_connections[target].close()
                manager.disconnect(target)
            
            # Update database
            supabase.table('users')\
                .update({'banned': True, 'ban_reason': action.reason})\
                .eq('id', target)\
                .execute()
            
            # Log the action
            supabase.table('mod_logs').insert({
                'moderator_id': user['id'],
                'target_id': target,
                'action': 'ban',
                'reason': action.reason,
                'timestamp': datetime.datetime.now().isoformat()
            }).execute()
            
            return {"success": True, "message": f"Banned user {target}"}
        
        elif action.action == "unban":
            # Unban user
            manager.banned_users.discard(target)
            
            supabase.table('users')\
                .update({'banned': False, 'ban_reason': None})\
                .eq('id', target)\
                .execute()
            
            return {"success": True, "message": f"Unbanned user {target}"}
        
        elif action.action == "mute":
            # Mute user for duration
            duration = action.duration or 60  # default 60 minutes
            mute_until = datetime.datetime.now() + datetime.timedelta(minutes=duration)
            manager.muted_users[target] = mute_until
            
            await manager.send_personal_message(target, {
                "type": "system",
                "message": f"You have been muted for {duration} minutes. Reason: {action.reason}"
            })
            
            # Log the action
            supabase.table('mod_logs').insert({
                'moderator_id': user['id'],
                'target_id': target,
                'action': 'mute',
                'duration': duration,
                'reason': action.reason,
                'timestamp': datetime.datetime.now().isoformat()
            }).execute()
            
            return {"success": True, "message": f"Muted user {target} for {duration} minutes"}
        
        elif action.action == "unmute":
            # Unmute user
            if target in manager.muted_users:
                del manager.muted_users[target]
                
                await manager.send_personal_message(target, {
                    "type": "system",
                    "message": "You have been unmuted"
                })
            
            return {"success": True, "message": f"Unmuted user {target}"}
        
        elif action.action == "warn":
            # Log warning
            warning_result = supabase.table('user_warnings').insert({
                'user_id': target,
                'moderator_id': user['id'],
                'reason': action.reason,
                'created_at': datetime.datetime.now().isoformat()
            }).execute()
            
            await manager.send_personal_message(target, {
                "type": "system",
                "message": f"You have received a warning. Reason: {action.reason}"
            })
            
            # Get warning count
            warnings = supabase.table('user_warnings')\
                .select('id', count='exact')\
                .eq('user_id', target)\
                .execute()
            
            return {
                "success": True,
                "message": f"Warned user {target}",
                "warning_count": warnings.count
            }
        
        return {"success": False, "message": "Invalid action"}
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/admin/event")
async def create_game_event(
    event: GameEvent,
    user = Depends(require_admin)
):
    """Create global game events"""
    try:
        if event.event_type == "spawn_boss":
            # Broadcast boss spawn to all players
            boss_data = event.data
            await manager.broadcast_to_room("global", {
                "type": "boss_spawn",
                "boss": boss_data.get("name", "Unknown Boss"),
                "location": boss_data.get("location", "Unknown"),
                "level": boss_data.get("level", 50),
                "hp": boss_data.get("hp", 1000),
                "rewards": boss_data.get("rewards", {})
            })
            
            return {"success": True, "message": f"Boss {boss_data.get('name')} spawned"}
        
        elif event.event_type == "start_event":
            # Start global event
            event_data = event.data
            await manager.broadcast_to_room("global", {
                "type": "global_event",
                "event": event_data.get("name"),
                "description": event_data.get("description"),
                "duration": event_data.get("duration", 60),
                "rewards": event_data.get("rewards", {})
            })
            
            return {"success": True, "message": f"Event {event_data.get('name')} started"}
        
        elif event.event_type == "give_rewards":
            # Give rewards to all online players
            reward_data = event.data
            gold_amount = reward_data.get("gold", 0)
            item_id = reward_data.get("item_id")
            
            for user_id in manager.rooms["global"]:
                # Get character for this user
                char_result = supabase.table('characters')\
                    .select('id, gold')\
                    .eq('user_id', user_id)\
                    .execute()
                
                if char_result.data:
                    char = char_result.data[0]
                    
                    # Give gold
                    if gold_amount > 0:
                        supabase.table('characters')\
                            .update({'gold': char['gold'] + gold_amount})\
                            .eq('id', char['id'])\
                            .execute()
                    
                    # Give item
                    if item_id:
                        supabase.table('inventory').insert({
                            'character_id': char['id'],
                            'item_id': item_id,
                            'quantity': 1
                        }).execute()
                    
                    await manager.send_personal_message(user_id, {
                        "type": "reward",
                        "gold": gold_amount,
                        "item": item_id,
                        "message": f"You received {gold_amount} gold" + (f" and {item_id}" if item_id else "")
                    })
            
            return {"success": True, "message": "Rewards distributed"}
        
        elif event.event_type == "announce":
            # Send global announcement
            announcement = event.data.get("message", "Announcement")
            await manager.broadcast_to_room("global", {
                "type": "announcement",
                "message": announcement,
                "from": user.get('username', 'Admin')
            })
            
            return {"success": True, "message": "Announcement sent"}
        
        return {"success": False, "message": "Invalid event type"}
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/admin/online-players")
async def get_online_players(user = Depends(require_mod)):
    """Get list of online players with their info"""
    players = []
    for user_id in manager.rooms["global"]:
        role = manager.user_roles.get(user_id, "player")
        
        # Get character info
        char_result = supabase.table('characters')\
            .select('name, level')\
            .eq('user_id', user_id)\
            .execute()
        
        char_name = char_result.data[0]['name'] if char_result.data else "Unknown"
        char_level = char_result.data[0]['level'] if char_result.data else 0
        
        players.append({
            "user_id": user_id,
            "character_name": char_name,
            "level": char_level,
            "role": role,
            "is_muted": user_id in manager.muted_users,
            "is_banned": user_id in manager.banned_users
        })
    
    return {
        "online_count": len(players),
        "players": players,
        "timestamp": datetime.datetime.now().isoformat()
    }

@app.post("/api/admin/promote")
async def promote_user(
    target_user_id: int,
    new_role: str,
    user = Depends(require_owner)
):
    """Promote user to moderator/admin (owner only)"""
    if new_role not in ["moderator", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'moderator' or 'admin'")
    
    # Update user role in database
    supabase.table('users')\
        .update({'role': new_role})\
        .eq('id', target_user_id)\
        .execute()
    
    # Update role in connection manager if online
    if target_user_id in manager.user_roles:
        manager.user_roles[target_user_id] = new_role
        if new_role in ["moderator", "admin", "owner"]:
            manager.rooms["mod"].add(target_user_id)
        if new_role in ["admin", "owner"]:
            manager.rooms["admin"].add(target_user_id)
    
    # Update character role
    supabase.table('characters')\
        .update({'role': new_role})\
        .eq('user_id', target_user_id)\
        .execute()
    
    # Notify user if online
    await manager.send_personal_message(target_user_id, {
        "type": "system",
        "message": f"You have been promoted to {new_role}!"
    })
    
    return {"success": True, "message": f"User promoted to {new_role}"}

@app.get("/api/admin/warnings/{user_id}")
async def get_user_warnings(
    user_id: int,
    user = Depends(require_mod)
):
    """Get warning history for a user"""
    result = supabase.table('user_warnings')\
        .select('*, moderator:moderator_id(username)')\
        .eq('user_id', user_id)\
        .order('created_at', desc=True)\
        .execute()
    
    return {"warnings": result.data}

# ==================== WebSocket Routes ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return
    
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
        role = payload.get("role", "player")
        is_owner = payload.get("is_owner", False)
        
        # Check if banned
        result = supabase.table('users').select('banned').eq('id', user_id).execute()
        if result.data and result.data[0].get('banned', False):
            await websocket.close(code=1008)
            return
        
        if user_id in manager.banned_users:
            await websocket.close(code=1008)
            return
        
    except Exception:
        await websocket.close(code=1008)
        return
    
    await manager.connect(websocket, user_id, role)
    
    # Send welcome message with role
    role_emoji = "👑" if is_owner else "⚡" if role == "admin" else "🛡️" if role == "moderator" else ""
    await manager.send_personal_message(user_id, {
        "type": "system",
        "message": f"Connected to server {role_emoji}".strip()
    })
    
    # Send recent chat history
    for msg in manager.chat_history[-20:]:  # Last 20 messages
        await manager.send_personal_message(user_id, msg)
    
    try:
        while True:
            data = await websocket.receive_json()
            
            msg_type = data.get("type")
            
            if msg_type == "chat":
                message = ChatMessage(
                    channel=data["channel"],
                    content=data["content"],
                    target=data.get("target")
                )
                await manager.handle_chat_message(user_id, message)
            
            elif msg_type == "move":
                character_id = data.get("character_id")
                x = data.get("x")
                y = data.get("y")
                
                supabase.table('characters')\
                    .update({'x_position': x, 'y_position': y})\
                    .eq('id', character_id)\
                    .eq('user_id', user_id)\
                    .execute()
                
                await manager.broadcast_to_room("global", {
                    "type": "movement",
                    "user_id": user_id,
                    "character_id": character_id,
                    "x": x,
                    "y": y
                }, exclude_user=user_id)
            
            elif msg_type == "emote":
                emote = data.get("emote")
                await manager.broadcast_to_room("global", {
                    "type": "emote",
                    "user_id": user_id,
                    "emote": emote
                }, exclude_user=user_id)
    
    except WebSocketDisconnect:
        manager.disconnect(user_id)

# ==================== Health Check ====================

@app.get("/", include_in_schema=True)
async def root():
    return {
        "status": "online",
        "service": "Pixel RPG API",
        "version": "2.0.0",
        "features": ["owner_auto_register", "role_system", "cheat_menu", "mod_menu"],
        "owner": OWNER_USERNAME,
        "timestamp": datetime.datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    # Check database connection
    db_status = "healthy"
    try:
        supabase.table('users').select('id').limit(1).execute()
    except:
        db_status = "unhealthy"
    
    return {
        "status": "healthy",
        "database": db_status,
        "websocket_connections": len(manager.active_connections),
        "timestamp": datetime.datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)



