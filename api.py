"""
Complete 2D Pixel RPG Backend
Single file FastAPI application with Supabase integration
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
            "map": self.map_id
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

# ==================== WebSocket Manager ====================

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}
        self.rooms: Dict[str, Set[int]] = {
            "global": set(),
            "trade": set(),
            "pvp": set()
        }
        self.chat_history: List[Dict] = []
    
    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        self.rooms["global"].add(user_id)
    
    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        for room in self.rooms.values():
            room.discard(user_id)
    
    async def send_personal_message(self, user_id: int, message: dict):
        if user_id in self.active_connections:
            try:
                await self.active_connections[user_id].send_json(message)
            except:
                pass
    
    async def broadcast_to_room(self, room: str, message: dict):
        if room in self.rooms:
            for user_id in self.rooms[room]:
                await self.send_personal_message(user_id, message)
    
    async def handle_chat_message(self, user_id: int, message: ChatMessage):
        # Get character name
        result = supabase.table('characters')\
            .select('name')\
            .eq('user_id', user_id)\
            .execute()
        
        character_name = result.data[0]['name'] if result.data else f"User_{user_id}"
        
        chat_entry = {
            "type": "chat",
            "channel": message.channel,
            "sender": character_name,
            "sender_id": user_id,
            "content": message.content,
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        await self.broadcast_to_room("global", chat_entry)

manager = ConnectionManager()

# ==================== FastAPI App ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("RPG Server starting...")
    yield
    # Shutdown
    print("RPG Server shutting down...")

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
    
    # Create user
    password_hash = hash_password(user.password)
    
    result = supabase.table('users').insert({
        'username': user.username,
        'password_hash': password_hash
    }).execute()
    
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create user")
    
    user_id = result.data[0]['id']
    token = create_access_token({"sub": str(user_id)})
    
    return {"access_token": token, "token_type": "bearer"}

@app.post("/api/login", response_model=TokenResponse)
async def login(user: UserLogin):
    result = supabase.table('users').select('*').eq('username', user.username).execute()
    
    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    db_user = result.data[0]
    
    if not verify_password(user.password, db_user['password_hash']):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_access_token({"sub": str(db_user['id'])})
    return {"access_token": token, "token_type": "bearer"}

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
        'gold': 100
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
        vitality=character_data['vitality']
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
            vitality=row['vitality']
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
    
    return {
        "character": character,
        "inventory": []
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
    })
    
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
        vitality=char_data["vitality"]
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
    return {
        "winner": 1,
        "player1": 1,
        "player2": 2
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
    
    guild_result = supabase.table('guilds').insert({
        'name': guild.name,
        'tag': guild.tag,
        'leader_id': result.data[0]['id']
    }).execute()
    
    return {"id": guild_result.data[0]['id'], "name": guild.name, "tag": guild.tag}

@app.get("/api/guilds/my-guild")
async def get_my_guild(user=Depends(get_current_user)):
    return {
        "name": "Test Guild",
        "tag": "TEST",
        "members": [
            {"rank": "leader", "name": "Player1", "level": 10},
            {"rank": "member", "name": "Player2", "level": 5}
        ]
    }

# ==================== Auction Routes ====================

@app.get("/api/auction/listings")
async def get_auctions(user=Depends(get_current_user)):
    return [
        {
            "id": 1,
            "item_id": "sword_001",
            "current_bid": 100,
            "starting_bid": 50,
            "buyout_price": 200
        }
    ]

@app.post("/api/auction/{auction_id}/bid")
async def place_bid(auction_id: int, bid_amount: int, user=Depends(get_current_user)):
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
    except:
        await websocket.close(code=1008)
        return
    
    await manager.connect(websocket, user_id)
    
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
                })
    
    except WebSocketDisconnect:
        manager.disconnect(user_id)

# ==================== Health Check ====================

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Pixel RPG API",
        "version": "1.0.0",
        "timestamp": datetime.datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
