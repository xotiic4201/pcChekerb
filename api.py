"""
Complete 2D Pixel RPG Backend
Single file FastAPI application with Supabase PostgreSQL database
Includes Full Game Systems, Multiplayer, Trading, PvP, Guilds, and 5-Act Story
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
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, FileResponse
import jwt
from pydantic import BaseModel, Field, validator
import bcrypt

# Supabase imports
from supabase import create_client, Client
from postgrest import APIError

# ==================== Configuration ====================

# Supabase configuration - REPLACE WITH YOUR ACTUAL SUPABASE URL AND KEY
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://your-project-url.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "your-supabase-anon-key")
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week

# Owner credentials
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "admin")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "admin123")

# ==================== Supabase Client ====================

supabase: Optional[Client] = None

def get_supabase_client() -> Client:
    """Get or create Supabase client"""
    global supabase
    if supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise Exception("SUPABASE_URL and SUPABASE_KEY must be set")
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase

async def execute_query(query_func):
    """Execute a Supabase query with error handling"""
    try:
        return await query_func()
    except APIError as e:
        print(f"Database error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

async def init_database():
    """Initialize database connection and create tables if they don't exist"""
    try:
        client = get_supabase_client()
        
        # Test connection
        result = client.table('users').select('count', count='exact').limit(1).execute()
        print("✅ Supabase connection successful")
        
        # Create tables if they don't exist (you need to create these in Supabase SQL editor)
        # See the SQL schema at the bottom of this file
        
        # Create owner account
        await create_owner_account()
        
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        print("Please ensure your Supabase database is properly set up with the schema")

async def create_owner_account():
    """Create owner account with specified credentials"""
    client = get_supabase_client()
    
    try:
        # Check if owner already exists
        result = client.table('users').select('*').eq('username', OWNER_USERNAME).execute()
        
        if not result.data:
            # Create owner
            password_hash = bcrypt.hashpw(OWNER_PASSWORD.encode(), bcrypt.gensalt()).decode()
            
            user_data = {
                'username': OWNER_USERNAME,
                'password_hash': password_hash,
                'role': 'owner',
                'is_owner': True,
                'created_at': datetime.datetime.now().isoformat()
            }
            
            user_result = client.table('users').insert(user_data).execute()
            
            if user_result.data:
                user_id = user_result.data[0]['id']
                
                # Create owner character
                character_data = {
                    'user_id': user_id,
                    'name': 'Admin',
                    'class_name': 'warrior',
                    'level': 999,
                    'exp': 0,
                    'gold': 999999,
                    'hp': 99999,
                    'max_hp': 99999,
                    'mana': 99999,
                    'max_mana': 99999,
                    'strength': 999,
                    'agility': 999,
                    'intelligence': 999,
                    'vitality': 999,
                    'role': 'owner',
                    'skill_points': 999,
                    'x_position': 250,
                    'y_position': 250
                }
                
                client.table('characters').insert(character_data).execute()
                print(f"✅ Owner account created ({OWNER_USERNAME})")
    except Exception as e:
        print(f"⚠️ Could not create default owner: {e}")

# ==================== Enums ====================

class GameClass(str, Enum):
    WARRIOR = "warrior"
    MAGE = "mage"
    ROGUE = "rogue"
    ARCHER = "archer"
    PALADIN = "paladin"

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
    MOD = "mod"
    ADMIN = "admin"

class TradeStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

# ==================== Pydantic Models ====================

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=6)
    email: Optional[str] = None

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
    skill_points: int
    x_position: Optional[int] = 250
    y_position: Optional[int] = 250

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
    command: str
    target: Optional[str] = None
    amount: Optional[int] = None
    item_id: Optional[str] = None

class ModAction(BaseModel):
    action: str
    target_user_id: int
    target_character_id: Optional[int] = None
    reason: str
    duration: Optional[int] = None

class GameEvent(BaseModel):
    event_type: str
    data: Dict[str, Any]

# ==================== Game Data ====================

# Item Database with emojis
ITEMS = {
    # Weapons
    "iron_sword": {"name": "⚔️ Iron Sword", "icon": "🗡️", "type": "weapon", "rarity": "common", 
                   "stats": {"attack": 12}, "value": 50, "description": "A sturdy iron sword."},
    "steel_sword": {"name": "⚔️ Steel Sword", "icon": "⚔️", "type": "weapon", "rarity": "uncommon", 
                    "stats": {"attack": 22}, "value": 150, "description": "Forged from fine steel."},
    "oak_staff": {"name": "🪄 Oak Staff", "icon": "🪄", "type": "weapon", "rarity": "common", 
                  "stats": {"intelligence": 15}, "value": 45, "description": "A staff carved from ancient oak."},
    "longbow": {"name": "🏹 Longbow", "icon": "🏹", "type": "weapon", "rarity": "common", 
                "stats": {"agility": 18}, "value": 60, "description": "A reliable longbow."},
    "shadow_dagger": {"name": "🗡️ Shadow Dagger", "icon": "🗡️", "type": "weapon", "rarity": "rare", 
                      "stats": {"agility": 25, "critical": 0.1}, "value": 500, "description": "Forged in shadow."},
    "dragon_blade": {"name": "🐉 Dragon Blade", "icon": "⚔️", "type": "weapon", "rarity": "epic", 
                     "stats": {"strength": 40, "fire_damage": 15}, "value": 2000, "description": "Wreathed in flame."},
    "void_orb": {"name": "🌌 Void Orb", "icon": "🔮", "type": "weapon", "rarity": "legendary", 
                 "stats": {"intelligence": 60, "void_damage": 30}, "value": 5000, "description": "An orb of incomprehensible power."},
    
    # Armor
    "leather_armor": {"name": "🧥 Leather Armor", "icon": "🛡️", "type": "armor", "rarity": "common", 
                      "stats": {"defense": 10}, "value": 40, "description": "Light leather protection."},
    "chain_mail": {"name": "🛡️ Chain Mail", "icon": "🛡️", "type": "armor", "rarity": "uncommon", 
                   "stats": {"defense": 25}, "value": 120, "description": "Interlocked metal rings."},
    "knight_helm": {"name": "⛑️ Knight's Helm", "icon": "⛑️", "type": "armor", "rarity": "uncommon", 
                    "stats": {"defense": 15, "strength": 5}, "value": 180, "description": "The helm of a noble knight."},
    "shadow_cape": {"name": "🌑 Shadow Cape", "icon": "👕", "type": "armor", "rarity": "epic", 
                    "stats": {"agility": 20, "critical": 0.1}, "value": 800, "description": "Woven from shadows themselves."},
    "dragon_plate": {"name": "🐉 Dragon Plate", "icon": "🛡️", "type": "armor", "rarity": "legendary", 
                     "stats": {"defense": 80, "fire_resist": 50}, "value": 3000, "description": "Made from dragon scales."},
    
    # Accessories
    "iron_ring": {"name": "💍 Iron Ring", "icon": "💍", "type": "accessory", "rarity": "common", 
                  "stats": {"vitality": 5}, "value": 30, "description": "A simple iron ring."},
    "magic_ring": {"name": "✨ Magic Ring", "icon": "💍", "type": "accessory", "rarity": "rare", 
                   "stats": {"all_stats": 8}, "value": 600, "description": "Radiates ancient magic."},
    "ancient_amulet": {"name": "📿 Ancient Amulet", "icon": "📿", "type": "accessory", "rarity": "epic", 
                       "stats": {"intelligence": 30, "mana_regen": 5}, "value": 1200, "description": "Pulsing with energy."},
    
    # Consumables
    "health_potion": {"name": "❤️ Health Potion", "icon": "🧪", "type": "consumable", "rarity": "common", 
                      "effect": {"heal": 50}, "value": 25, "description": "Restores 50 HP."},
    "mana_potion": {"name": "💙 Mana Potion", "icon": "💧", "type": "consumable", "rarity": "common", 
                    "effect": {"restore_mana": 30}, "value": 25, "description": "Restores 30 MP."},
    "elixir": {"name": "✨ Elixir", "icon": "🧪", "type": "consumable", "rarity": "rare", 
               "effect": {"heal": 200, "restore_mana": 100}, "value": 200, "description": "Fully restores HP and MP."},
    
    # Crafting
    "iron_ore": {"name": "⛏️ Iron Ore", "icon": "⛏️", "type": "crafting", "rarity": "common", 
                 "value": 10, "description": "Can be smelted into iron."},
    "magic_dust": {"name": "✨ Magic Dust", "icon": "✨", "type": "crafting", "rarity": "uncommon", 
                   "value": 50, "description": "Infused with magical essence."},
    "dragon_scale": {"name": "🐉 Dragon Scale", "icon": "🐉", "type": "crafting", "rarity": "legendary", 
                     "value": 1000, "description": "A scale from a dragon."},
}

# Enemy Database with emojis
ENEMIES = {
    # Act 1 Enemies
    "goblin": {
        "name": "👺 Goblin", "emoji": "👺", "level": 1, "hp": 50, "max_hp": 50,
        "attack": 8, "defense": 2, "exp_reward": 25, "gold_reward": 10,
        "loot_table": [{"item_id": "iron_ore", "chance": 0.3}]
    },
    "goblin_warrior": {
        "name": "👹 Goblin Warrior", "emoji": "👹", "level": 2, "hp": 80, "max_hp": 80,
        "attack": 12, "defense": 5, "exp_reward": 40, "gold_reward": 20,
        "loot_table": [{"item_id": "iron_ore", "chance": 0.5}, {"item_id": "health_potion", "chance": 0.2}]
    },
    "goblin_chief": {
        "name": "👑 Goblin Chief", "emoji": "👑", "level": 3, "hp": 150, "max_hp": 150,
        "attack": 18, "defense": 8, "exp_reward": 100, "gold_reward": 50,
        "loot_table": [{"item_id": "iron_sword", "chance": 0.3}, {"item_id": "health_potion", "chance": 0.5}],
        "is_boss": True
    },
    
    # Act 2 Enemies
    "enemy_soldier": {
        "name": "⚔️ Enemy Soldier", "emoji": "⚔️", "level": 4, "hp": 120, "max_hp": 120,
        "attack": 15, "defense": 8, "exp_reward": 60, "gold_reward": 30,
        "loot_table": [{"item_id": "iron_ore", "chance": 0.4}, {"item_id": "health_potion", "chance": 0.3}]
    },
    "enemy_knight": {
        "name": "🛡️ Enemy Knight", "emoji": "🛡️", "level": 5, "hp": 180, "max_hp": 180,
        "attack": 22, "defense": 15, "exp_reward": 90, "gold_reward": 50,
        "loot_table": [{"item_id": "chain_mail", "chance": 0.2}, {"item_id": "health_potion", "chance": 0.4}]
    },
    "enemy_general": {
        "name": "🎖️ Enemy General", "emoji": "🎖️", "level": 6, "hp": 250, "max_hp": 250,
        "attack": 30, "defense": 20, "exp_reward": 150, "gold_reward": 100,
        "loot_table": [{"item_id": "steel_sword", "chance": 0.3}, {"item_id": "knight_helm", "chance": 0.3}],
        "is_boss": True
    },
    
    # Act 3 Enemies
    "corrupted_creature": {
        "name": "👾 Corrupted Creature", "emoji": "👾", "level": 7, "hp": 200, "max_hp": 200,
        "attack": 25, "defense": 10, "exp_reward": 80, "gold_reward": 40,
        "loot_table": [{"item_id": "magic_dust", "chance": 0.4}]
    },
    "corrupted_beast": {
        "name": "🐗 Corrupted Beast", "emoji": "🐗", "level": 8, "hp": 280, "max_hp": 280,
        "attack": 32, "defense": 15, "exp_reward": 110, "gold_reward": 60,
        "loot_table": [{"item_id": "magic_dust", "chance": 0.6}, {"item_id": "shadow_dagger", "chance": 0.1}]
    },
    "the_corruptor": {
        "name": "💀 The Corruptor", "emoji": "💀", "level": 10, "hp": 500, "max_hp": 500,
        "attack": 40, "defense": 20, "exp_reward": 500, "gold_reward": 300,
        "loot_table": [{"item_id": "shadow_cape", "chance": 0.5}, {"item_id": "magic_ring", "chance": 0.5}],
        "is_boss": True
    },
    
    # Act 4 Enemies
    "time_wraith": {
        "name": "👻 Time Wraith", "emoji": "👻", "level": 12, "hp": 350, "max_hp": 350,
        "attack": 35, "defense": 18, "exp_reward": 150, "gold_reward": 80,
        "loot_table": [{"item_id": "magic_dust", "chance": 0.7}]
    },
    "temporal_beast": {
        "name": "⏳ Temporal Beast", "emoji": "⏳", "level": 14, "hp": 500, "max_hp": 500,
        "attack": 45, "defense": 25, "exp_reward": 200, "gold_reward": 120,
        "loot_table": [{"item_id": "ancient_amulet", "chance": 0.2}]
    },
    "time_keeper": {
        "name": "⌛ Time Keeper", "emoji": "⌛", "level": 15, "hp": 1000, "max_hp": 1000,
        "attack": 50, "defense": 30, "exp_reward": 800, "gold_reward": 600,
        "loot_table": [{"item_id": "ancient_amulet", "chance": 1.0}, {"item_id": "elixir", "chance": 0.5}],
        "is_boss": True
    },
    
    # Act 5 Enemies
    "void_guardian": {
        "name": "🌑 Void Guardian", "emoji": "🌑", "level": 18, "hp": 800, "max_hp": 800,
        "attack": 55, "defense": 35, "exp_reward": 400, "gold_reward": 250,
        "loot_table": [{"item_id": "dragon_scale", "chance": 0.3}]
    },
    "void_lord": {
        "name": "🌌 Void Lord", "emoji": "🌌", "level": 20, "hp": 2000, "max_hp": 2000,
        "attack": 70, "defense": 40, "exp_reward": 2000, "gold_reward": 1000,
        "loot_table": [{"item_id": "void_orb", "chance": 1.0}, {"item_id": "dragon_plate", "chance": 0.5}],
        "is_boss": True,
        "is_final_boss": True
    }
}

# Quest Database - 5 Act Story with Multiple Endings
QUESTS = {
    # Act 1: Village Invasion
    "q1_1": {
        "id": "q1_1", "title": "🌿 The Dark Omen", "act": 1, "chapter": 1,
        "description": "Strange creatures have been seen near the village. Kill 5 Goblins.",
        "dialogue": "Elder: 'Strange howls have been coming from the forest. Please investigate, young hero.'",
        "objectives": [{"type": "kill", "target": "goblin", "amount": 5}],
        "rewards": {"exp": 100, "gold": 50, "items": ["health_potion"]},
        "next_quest": "q1_2",
        "choices": [
            {"id": "help", "text": "✨ I'll help the village", "reputation": {"village": 10}},
            {"id": "demand_payment", "text": "💰 I want payment first", "reputation": {"village": -5}}
        ]
    },
    "q1_2": {
        "id": "q1_2", "title": "🏕️ The Goblin Camp", "act": 1, "chapter": 2,
        "description": "Find the goblin camp and discover who's leading them.",
        "dialogue": "Elder: 'We've tracked them to a camp in the hills. Find out what they're planning.'",
        "objectives": [{"type": "explore", "target": "goblin_camp", "amount": 1}],
        "rewards": {"exp": 150, "gold": 75, "items": ["iron_ring"]},
        "next_quest": "q1_3"
    },
    "q1_3": {
        "id": "q1_3", "title": "👑 Goblin Chief", "act": 1, "chapter": 3,
        "description": "Defeat the Goblin Chief leading the invasion.",
        "dialogue": "Elder: 'The chief must be defeated if we're to save the village.'",
        "objectives": [{"type": "kill", "target": "goblin_chief", "amount": 1}],
        "rewards": {"exp": 300, "gold": 150, "items": ["iron_sword"]},
        "next_quest": "q2_1",
        "is_boss": True
    },
    
    # Act 2: Kingdom War
    "q2_1": {
        "id": "q2_1", "title": "👑 The King's Summons", "act": 2, "chapter": 1,
        "description": "The king has summoned all able warriors. Report to the capital.",
        "dialogue": "Messenger: 'By order of the King, all able-bodied warriors must report to the capital immediately!'",
        "objectives": [{"type": "talk", "target": "king", "amount": 1}],
        "rewards": {"exp": 250, "gold": 200, "items": ["health_potion"]},
        "next_quest": "q2_2"
    },
    "q2_2": {
        "id": "q2_2", "title": "⚔️ Border Skirmish", "act": 2, "chapter": 2,
        "description": "The northern border is under attack. Kill 10 Enemy Soldiers.",
        "dialogue": "Captain: 'The enemy has breached our northern defenses! We need reinforcements!'",
        "objectives": [{"type": "kill", "target": "enemy_soldier", "amount": 10}],
        "rewards": {"exp": 350, "gold": 250, "items": ["leather_armor"]},
        "next_quest": "q2_3"
    },
    "q2_3": {
        "id": "q2_3", "title": "🏰 Siege of the Capital", "act": 2, "chapter": 3,
        "description": "The enemy army has reached the capital. Defeat the Enemy General!",
        "dialogue": "King: 'The enemy is at our gates! This is our darkest hour.'",
        "objectives": [{"type": "kill", "target": "enemy_general", "amount": 1}],
        "rewards": {"exp": 600, "gold": 500, "items": ["steel_sword"]},
        "next_quest": "q3_1",
        "is_boss": True
    },
    
    # Act 3: Corruption Spreads
    "q3_1": {
        "id": "q3_1", "title": "💀 The Plague", "act": 3, "chapter": 1,
        "description": "A strange plague is spreading across the land. Investigate the plague origin.",
        "dialogue": "Healer: 'People are falling ill with a mysterious plague. It's like nothing I've seen.'",
        "objectives": [{"type": "investigate", "target": "plague_origin", "amount": 1}],
        "rewards": {"exp": 500, "gold": 400, "items": ["health_potion"]},
        "next_quest": "q3_2"
    },
    "q3_2": {
        "id": "q3_2", "title": "🌲 Corrupted Forest", "act": 3, "chapter": 2,
        "description": "The ancient forest is being corrupted. Kill 15 Corrupted Creatures.",
        "dialogue": "Druid: 'The corruption spreads through the forest. We must cleanse it.'",
        "objectives": [{"type": "kill", "target": "corrupted_creature", "amount": 15}],
        "rewards": {"exp": 550, "gold": 450, "items": ["magic_ring"]},
        "next_quest": "q3_3"
    },
    "q3_3": {
        "id": "q3_3", "title": "👾 The Corruptor", "act": 3, "chapter": 3,
        "description": "Defeat the Corruptor, a powerful being spreading the corruption.",
        "dialogue": "Druid: 'The Corruptor awaits at the heart of the forest. Be careful.'",
        "objectives": [{"type": "kill", "target": "the_corruptor", "amount": 1}],
        "rewards": {"exp": 800, "gold": 600, "items": ["shadow_cape"]},
        "next_quest": "q4_1",
        "is_boss": True
    },
    
    # Act 4: Time Fracture
    "q4_1": {
        "id": "q4_1", "title": "⏳ Temporal Distortion", "act": 4, "chapter": 1,
        "description": "Time is breaking apart. Investigate the temporal anomalies.",
        "dialogue": "Wizard: 'The fabric of time is unraveling! You must investigate the rifts.'",
        "objectives": [{"type": "explore", "target": "time_rift", "amount": 1}],
        "rewards": {"exp": 700, "gold": 600, "items": ["elixir"]},
        "next_quest": "q4_2"
    },
    "q4_2": {
        "id": "q4_2", "title": "📜 Past Sins", "act": 4, "chapter": 2,
        "description": "You're thrown into the past. Survive the encounter and witness the truth.",
        "dialogue": "Ancient Sage: 'You should not be here, traveler of time.'",
        "objectives": [{"type": "survive", "amount": 1}],
        "rewards": {"exp": 750, "gold": 650, "items": ["ancient_amulet"]},
        "next_quest": "q4_3"
    },
    "q4_3": {
        "id": "q4_3", "title": "⌛ Time Keeper", "act": 4, "chapter": 3,
        "description": "Defeat the Time Keeper to stabilize the timeline.",
        "dialogue": "Wizard: 'The Time Keeper guards the rift. Defeat it to return home.'",
        "objectives": [{"type": "kill", "target": "time_keeper", "amount": 1}],
        "rewards": {"exp": 1000, "gold": 800, "items": ["magic_ring"]},
        "next_quest": "q5_1",
        "is_boss": True
    },
    
    # Act 5: Multiple Endings
    "q5_1": {
        "id": "q5_1", "title": "🌌 The Final Confrontation", "act": 5, "chapter": 1,
        "description": "Face the source of all evil - The Void Lord.",
        "dialogue": "Wizard: 'The Void Lord awaits. This is the final battle.'",
        "objectives": [{"type": "kill", "target": "void_lord", "amount": 1}],
        "rewards": {"exp": 2000, "gold": 2000, "items": ["void_orb"]},
        "is_boss": True,
        "is_final": True,
        "choices": [
            {
                "id": "ending_hero",
                "text": "✨ Sacrifice yourself to destroy the Void Lord",
                "reputation": {"world": 100},
                "ending": "The Hero's Sacrifice - You become a legend, remembered for eternity."
            },
            {
                "id": "ending_dark",
                "text": "🌑 Embrace the void and become the new Void Lord",
                "reputation": {"world": -100},
                "ending": "The Dark Ascension - You rule the void, feared by all."
            },
            {
                "id": "ending_balance",
                "text": "⚖️ Balance light and dark, seal the void away",
                "reputation": {"world": 50},
                "ending": "The Balance - Peace is restored, but at a great cost."
            }
        ]
    }
}

# Skills Database
SKILLS = {
    "power_strike": {
        "name": "⚡ Power Strike",
        "description": "Deal 150% damage",
        "mana_cost": 10,
        "cooldown": 3,
        "damage_multiplier": 1.5,
        "unlock_level": 1
    },
    "cleave": {
        "name": "🌀 Cleave",
        "description": "Hit all enemies",
        "mana_cost": 15,
        "cooldown": 4,
        "damage_multiplier": 1.2,
        "unlock_level": 2
    },
    "battle_cry": {
        "name": "🗣️ Battle Cry",
        "description": "Increase attack for 10 seconds",
        "mana_cost": 20,
        "cooldown": 20,
        "effect": {"attack_bonus": 10},
        "unlock_level": 5
    },
    "whirlwind": {
        "name": "🌪️ Whirlwind",
        "description": "Spin attack dealing damage to all nearby enemies",
        "mana_cost": 25,
        "cooldown": 8,
        "damage_multiplier": 1.8,
        "unlock_level": 8
    },
    "fireball": {
        "name": "🔥 Fireball",
        "description": "Launch a fireball at the enemy",
        "mana_cost": 20,
        "cooldown": 5,
        "damage_multiplier": 2.0,
        "unlock_level": 3,
        "class_restriction": ["mage"]
    },
    "backstab": {
        "name": "🗡️ Backstab",
        "description": "Deal massive damage from behind",
        "mana_cost": 15,
        "cooldown": 6,
        "damage_multiplier": 2.5,
        "unlock_level": 3,
        "class_restriction": ["rogue"]
    }
}

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
    x: int = 250
    y: int = 250
    map_id: str = "start_village"
    guild_id: Optional[int] = None
    party_id: Optional[int] = None
    role: str = "player"
    skill_points: int = 0
    status_effects: List[Dict] = field(default_factory=list)
    cooldowns: Dict[str, float] = field(default_factory=dict)
    skills: List[str] = field(default_factory=list)
    inventory: List[Dict] = field(default_factory=list)
    
    @property
    def attack_power(self):
        if self.class_name in [GameClass.WARRIOR, GameClass.PALADIN]:
            return self.strength * 2 + self.level
        elif self.class_name == GameClass.ARCHER:
            return self.agility * 2 + self.level
        elif self.class_name == GameClass.MAGE:
            return self.intelligence * 2 + self.level
        else:  # ROGUE
            return self.strength + self.agility * 1.5 + self.level
    
    @property
    def defense(self):
        return self.vitality * 1.5 + self.level // 2
    
    @property
    def crit_chance(self):
        return self.agility / 200
    
    @property
    def dodge_chance(self):
        return self.agility / 300
    
    def take_damage(self, damage: int) -> int:
        # Check dodge
        if random.random() < self.dodge_chance:
            return 0
        
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
        self.skill_points += 2
        
        # Class-based stat increases
        if self.class_name == GameClass.WARRIOR:
            self.max_hp += 30
            self.strength += 3
            self.vitality += 3
            self.agility += 1
            self.intelligence += 1
        elif self.class_name == GameClass.MAGE:
            self.max_mana += 25
            self.intelligence += 4
            self.vitality += 1
            self.agility += 1
            self.strength += 1
        elif self.class_name == GameClass.ROGUE:
            self.agility += 4
            self.strength += 2
            self.vitality += 2
            self.intelligence += 1
        elif self.class_name == GameClass.ARCHER:
            self.agility += 4
            self.strength += 2
            self.vitality += 2
            self.intelligence += 1
        elif self.class_name == GameClass.PALADIN:
            self.max_hp += 25
            self.strength += 2
            self.vitality += 3
            self.intelligence += 2
        
        self.hp = self.max_hp
        self.mana = self.max_mana
    
    def use_skill(self, skill_id: str, target) -> Dict:
        # Check cooldown
        now = time.time()
        if skill_id in self.cooldowns and self.cooldowns[skill_id] > now:
            return {"success": False, "message": "Skill on cooldown"}
        
        skill = SKILLS.get(skill_id)
        if not skill:
            return {"success": False, "message": "Skill not found"}
        
        # Check class restriction
        if "class_restriction" in skill and self.class_name.value not in skill["class_restriction"]:
            return {"success": False, "message": "Your class cannot use this skill"}
        
        # Check mana
        if self.mana < skill["mana_cost"]:
            return {"success": False, "message": "Not enough mana"}
        
        self.mana -= skill["mana_cost"]
        
        # Calculate damage
        base_damage = self.attack_power
        damage_multiplier = skill.get("damage_multiplier", 1.0)
        
        # Critical hit
        is_critical = random.random() < self.crit_chance
        if is_critical:
            base_damage = int(base_damage * 1.8)
        
        damage = int(base_damage * damage_multiplier)
        
        # Set cooldown
        self.cooldowns[skill_id] = now + skill["cooldown"]
        
        return {
            "success": True,
            "damage": damage,
            "critical": is_critical,
            "mana_cost": skill["mana_cost"],
            "skill_name": skill["name"]
        }
    
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
    emoji: str
    level: int
    hp: int
    max_hp: int
    attack: int
    defense: int
    exp_reward: int
    gold_reward: int
    loot_table: List[Dict]
    skills: List[str] = field(default_factory=list)
    is_boss: bool = False
    
    def take_damage(self, damage: int) -> int:
        reduced_damage = max(1, damage - self.defense)
        self.hp = max(0, self.hp - reduced_damage)
        return reduced_damage
    
    def attack_target(self, player: Player) -> Dict:
        damage = max(1, self.attack - int(player.defense / 2))
        actual_damage = player.take_damage(damage)
        
        return {
            "damage": actual_damage,
            "message": f"{self.name} attacks for {actual_damage} damage"
        }

# ==================== Combat Engine ====================

class CombatEngine:
    def __init__(self):
        self.active_combats: Dict[str, Dict] = {}
    
    async def calculate_damage(self, attacker: Player, defender, skill_id: Optional[str] = None) -> Dict:
        if isinstance(defender, Player):
            # PvP damage calculation
            base_damage = attacker.attack_power
            defense = defender.defense
        else:
            # PvE damage calculation
            base_damage = attacker.attack_power
            defense = defender.defense
        
        # Critical hit
        is_critical = random.random() < attacker.crit_chance
        if is_critical:
            base_damage = int(base_damage * 1.5)
        
        # Apply damage
        actual_damage = max(1, base_damage - int(defense / 3))
        
        if isinstance(defender, Player):
            defender.hp = max(0, defender.hp - actual_damage)
        else:
            defender.hp = max(0, defender.hp - actual_damage)
        
        return {
            "damage": actual_damage,
            "critical": is_critical,
            "target_hp": defender.hp,
            "target_max_hp": defender.max_hp,
            "message": f"Dealt {actual_damage} damage{' (CRITICAL!)' if is_critical else ''}"
        }
    
    async def start_pvp_combat(self, player1: Player, player2: Player) -> Dict:
        """Simulate PvP combat between two players"""
        combat_log = []
        turn = 1
        max_turns = 50
        
        # Determine first attacker (higher agility)
        if player1.agility >= player2.agility:
            first, second = player1, player2
        else:
            first, second = player2, player1
        
        combat_log.append({
            "turn": 0,
            "message": f"⚔️ Match started: {player1.name} ({player1.class_name.value}) vs {player2.name} ({player2.class_name.value})",
            "first_attacker": first.name
        })
        
        # Combat loop
        while player1.hp > 0 and player2.hp > 0 and turn <= max_turns:
            # First attacker's turn
            if player1.hp > 0 and player2.hp > 0:
                damage_result = await self.calculate_damage(first, second, None)
                combat_log.append({
                    "turn": turn,
                    "attacker": first.name,
                    "damage": damage_result["damage"],
                    "critical": damage_result["critical"],
                    "target_hp": damage_result["target_hp"],
                    "message": damage_result["message"]
                })
            
            if second.hp <= 0:
                break
            
            turn += 1
            
            # Second attacker's turn
            if player1.hp > 0 and player2.hp > 0:
                damage_result = await self.calculate_damage(second, first, None)
                combat_log.append({
                    "turn": turn,
                    "attacker": second.name,
                    "damage": damage_result["damage"],
                    "critical": damage_result["critical"],
                    "target_hp": damage_result["target_hp"],
                    "message": damage_result["message"]
                })
            
            turn += 1
        
        # Determine winner
        if player1.hp <= 0:
            winner = player2
            loser = player1
        elif player2.hp <= 0:
            winner = player1
            loser = player2
        else:
            # Draw - winner by higher HP
            if player1.hp > player2.hp:
                winner = player1
                loser = player2
            else:
                winner = player2
                loser = player1
        
        return {
            "winner": winner,
            "loser": loser,
            "combat_log": combat_log,
            "player1_final_hp": player1.hp,
            "player2_final_hp": player2.hp
        }

combat_engine = CombatEngine()

# ==================== Quest Engine ====================

class QuestEngine:
    def __init__(self):
        self.quests = QUESTS
    
    async def start_quest(self, character_id: int, quest_id: str) -> Dict:
        client = get_supabase_client()
        
        # Check if already started
        result = client.table('quests').select('*').eq('character_id', character_id).eq('quest_id', quest_id).execute()
        
        if result.data:
            return {"success": False, "message": "Quest already started"}
        
        quest = self.quests.get(quest_id)
        if not quest:
            return {"success": False, "message": "Quest not found"}
        
        # Initialize objectives as JSON
        objectives = json.dumps(quest.get("objectives", []))
        
        quest_data = {
            'character_id': character_id,
            'quest_id': quest_id,
            'status': 'in_progress',
            'objectives': objectives,
            'progress': 0,
            'started_at': datetime.datetime.now().isoformat()
        }
        
        client.table('quests').insert(quest_data).execute()
        
        return {"success": True, "message": f"Quest '{quest['title']}' started"}
    
    async def update_quest_progress(self, character_id: int, event_type: str, event_data: Dict) -> Dict:
        client = get_supabase_client()
        
        # Get active quests
        result = client.table('quests').select('*').eq('character_id', character_id).eq('status', 'in_progress').execute()
        
        active_quests = result.data
        updates = []
        
        for q in active_quests:
            quest_id = q["quest_id"]
            quest = self.quests.get(quest_id)
            if not quest:
                continue
            
            objectives = json.loads(q["objectives"] or "[]")
            progress = q["progress"] or 0
            completed = True
            
            # Update progress based on event
            for obj in objectives:
                if obj["type"] == event_type and obj["target"] == event_data.get("target"):
                    progress += event_data.get("amount", 1)
                    if progress < obj["amount"]:
                        completed = False
            
            if completed:
                # Quest completed
                client.table('quests').update({
                    'status': 'completed',
                    'completed_at': datetime.datetime.now().isoformat()
                }).eq('character_id', character_id).eq('quest_id', quest_id).execute()
                
                # Grant rewards
                rewards = quest.get("rewards", {})
                if rewards:
                    # Get character
                    char_result = client.table('characters').select('*').eq('id', character_id).execute()
                    if char_result.data:
                        char = char_result.data[0]
                        
                        # Update character exp and gold
                        new_exp = char['exp'] + rewards.get("exp", 0)
                        new_gold = char['gold'] + rewards.get("gold", 0)
                        
                        client.table('characters').update({
                            'exp': new_exp,
                            'gold': new_gold
                        }).eq('id', character_id).execute()
                        
                        # Check for level up
                        exp_needed = char['level'] * 100
                        if new_exp >= exp_needed:
                            # Level up logic here
                            pass
                        
                        # Add items
                        for item in rewards.get("items", []):
                            # Check if item already exists
                            inv_result = client.table('inventory').select('*').eq('character_id', character_id).eq('item_id', item).execute()
                            if inv_result.data:
                                # Update quantity
                                client.table('inventory').update({
                                    'quantity': inv_result.data[0]['quantity'] + 1
                                }).eq('id', inv_result.data[0]['id']).execute()
                            else:
                                # Insert new
                                client.table('inventory').insert({
                                    'character_id': character_id,
                                    'item_id': item,
                                    'quantity': 1
                                }).execute()
                
                updates.append({"quest_id": quest_id, "completed": True})
            else:
                # Update progress
                client.table('quests').update({'progress': progress}).eq('character_id', character_id).eq('quest_id', quest_id).execute()
                
                updates.append({"quest_id": quest_id, "progress": progress})
        
        return {"success": True, "updates": updates}

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
    
    client = get_supabase_client()
    result = client.table('users').select('*').eq('id', user_id).execute()
    
    if not result.data:
        raise HTTPException(status_code=401, detail="User not found")
    
    user = result.data[0]
    
    if user.get("banned", False):
        raise HTTPException(status_code=403, detail="User is banned")
    
    return dict(user)

async def require_owner(user = Depends(get_current_user)):
    """Require owner role"""
    if not user.get('is_owner', False) and user.get('role') != 'owner':
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
        self.character_ids: Dict[int, int] = {}
        self.rooms: Dict[str, Set[int]] = {
            "global": set(),
            "trade": set(),
            "pvp": set(),
            "mod": set(),
            "admin": set()
        }
        self.banned_users: Set[int] = set()
        self.muted_users: Dict[int, datetime.datetime] = {}
        self.chat_history: List[Dict] = []
    
    async def connect(self, websocket: WebSocket, user_id: int, role: str = "player"):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        self.user_roles[user_id] = role
        self.rooms["global"].add(user_id)
        
        # Get character ID for this user
        client = get_supabase_client()
        result = client.table('characters').select('id, name').eq('user_id', user_id).execute()
        if result.data:
            self.character_ids[user_id] = result.data[0]["id"]
        
        # Add to mod/admin rooms if applicable
        if role in ["moderator", "admin", "owner"]:
            self.rooms["mod"].add(user_id)
        if role in ["admin", "owner"]:
            self.rooms["admin"].add(user_id)
        
        # Broadcast user joined
        await self.broadcast_to_room("global", {
            "type": "system",
            "message": f"👤 Player joined the game"
        }, exclude_user=user_id)
    
    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        if user_id in self.user_roles:
            del self.user_roles[user_id]
        if user_id in self.character_ids:
            del self.character_ids[user_id]
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
        
        # Get character name
        client = get_supabase_client()
        result = client.table('characters').select('name, class_name').eq('user_id', user_id).execute()
        char = result.data[0] if result.data else None
        
        character_name = char["name"] if char else f"User_{user_id}"
        role = self.user_roles.get(user_id, "player")
        
        # Add role tag
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
        
        # Handle whisper
        if message.channel == ChatChannel.WHISPER and message.target:
            # Find target user by character name
            result = client.table('characters').select('user_id').eq('name', message.target).execute()
            
            if result.data:
                await self.send_personal_message(result.data[0]["user_id"], chat_entry)
                await self.send_personal_message(user_id, chat_entry)
            else:
                await self.send_personal_message(user_id, {
                    "type": "error",
                    "message": f"Player '{message.target}' not found"
                })
        elif message.channel == ChatChannel.MOD:
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
    print("🚀 Starting Pixel RPG Server with Supabase")
    print("=" * 60)
    await init_database()
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
    client = get_supabase_client()
    
    # Check if user exists
    result = client.table('users').select('*').eq('username', user.username).execute()
    if result.data:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    # Check if email exists
    if user.email:
        result = client.table('users').select('*').eq('email', user.email).execute()
        if result.data:
            raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user
    password_hash = hash_password(user.password)
    
    user_data = {
        'username': user.username,
        'password_hash': password_hash,
        'email': user.email,
        'role': 'player',
        'is_owner': False,
        'created_at': datetime.datetime.now().isoformat()
    }
    
    result = client.table('users').insert(user_data).execute()
    
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create user")
    
    user_id = result.data[0]['id']
    
    token = create_access_token({"sub": str(user_id), "role": "player", "is_owner": False})
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": "player",
        "is_owner": False
    }

@app.post("/api/login", response_model=TokenResponse)
async def login(user: UserLogin):
    client = get_supabase_client()
    
    result = client.table('users').select('*').eq('username', user.username).execute()
    
    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    db_user = result.data[0]
    
    if not verify_password(user.password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if db_user.get("banned", False):
        raise HTTPException(status_code=403, detail="Account is banned")
    
    # Update last login
    client.table('users').update({'last_login': datetime.datetime.now().isoformat()}).eq('id', db_user["id"]).execute()
    
    token = create_access_token({
        "sub": str(db_user["id"]),
        "role": db_user.get('role', 'player'),
        "is_owner": bool(db_user.get('is_owner', False))
    })
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": db_user.get('role', 'player'),
        "is_owner": bool(db_user.get('is_owner', False))
    }

# ==================== Character Routes ====================

@app.post("/api/characters", response_model=CharacterResponse)
async def create_character(character: CharacterCreate, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Check if character name exists
    result = client.table('characters').select('*').eq('name', character.name).execute()
    if result.data:
        raise HTTPException(status_code=400, detail="Character name already exists")
    
    # Check if user already has max characters (3)
    result = client.table('characters').select('id', count='exact').eq('user_id', user["id"]).execute()
    count = result.count if hasattr(result, 'count') else len(result.data)
    if count >= 3:
        raise HTTPException(status_code=400, detail="Maximum characters reached (3)")
    
    # Base stats per class
    base_stats = {
        GameClass.WARRIOR: {"hp": 120, "mana": 40, "strength": 15, "agility": 8, "intelligence": 5, "vitality": 14},
        GameClass.MAGE: {"hp": 80, "mana": 100, "strength": 5, "agility": 7, "intelligence": 18, "vitality": 8},
        GameClass.ROGUE: {"hp": 90, "mana": 60, "strength": 10, "agility": 18, "intelligence": 7, "vitality": 10},
        GameClass.ARCHER: {"hp": 95, "mana": 55, "strength": 8, "agility": 17, "intelligence": 8, "vitality": 11},
        GameClass.PALADIN: {"hp": 110, "mana": 60, "strength": 12, "agility": 6, "intelligence": 10, "vitality": 15}
    }
    
    stats = base_stats[character.class_name]
    role = user.get('role', 'player')
    
    # Starting gold
    starting_gold = 100
    if role == "owner" or user.get('is_owner', False):
        starting_gold = 100000
    
    character_data = {
        'user_id': user["id"],
        'name': character.name,
        'class_name': character.class_name.value,
        'level': 1,
        'exp': 0,
        'gold': starting_gold,
        'hp': stats["hp"],
        'max_hp': stats["hp"],
        'mana': stats["mana"],
        'max_mana': stats["mana"],
        'strength': stats["strength"],
        'agility': stats["agility"],
        'intelligence': stats["intelligence"],
        'vitality': stats["vitality"],
        'role': role,
        'skill_points': 0,
        'x_position': 250,
        'y_position': 250
    }
    
    result = client.table('characters').insert(character_data).execute()
    
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create character")
    
    char_data = result.data[0]
    
    # Add starting items
    client.table('inventory').insert({
        'character_id': char_data["id"],
        'item_id': 'health_potion',
        'quantity': 3
    }).execute()
    
    # Add starting skills based on class
    starting_skills = ['power_strike']
    if character.class_name == GameClass.MAGE:
        starting_skills.append('fireball')
    elif character.class_name == GameClass.ROGUE:
        starting_skills.append('backstab')
    
    for skill_id in starting_skills:
        client.table('skills').insert({
            'character_id': char_data["id"],
            'skill_id': skill_id,
            'learned_at': datetime.datetime.now().isoformat()
        }).execute()
    
    return CharacterResponse(
        id=char_data["id"],
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
        role=char_data.get('role', 'player'),
        skill_points=char_data["skill_points"],
        x_position=char_data["x_position"],
        y_position=char_data["y_position"]
    )

@app.get("/api/characters", response_model=List[CharacterResponse])
async def get_characters(user=Depends(get_current_user)):
    client = get_supabase_client()
    
    result = client.table('characters').select('*').eq('user_id', user["id"]).execute()
    
    characters = []
    for row in result.data:
        characters.append(CharacterResponse(
            id=row["id"],
            name=row["name"],
            class_name=GameClass(row["class_name"]),
            level=row["level"],
            exp=row["exp"],
            gold=row["gold"],
            hp=row["hp"],
            max_hp=row["max_hp"],
            mana=row["mana"],
            max_mana=row["max_mana"],
            strength=row["strength"],
            agility=row["agility"],
            intelligence=row["intelligence"],
            vitality=row["vitality"],
            role=row.get('role', 'player'),
            skill_points=row["skill_points"],
            x_position=row["x_position"],
            y_position=row["y_position"]
        ))
    
    return characters

@app.get("/api/characters/{character_id}")
async def get_character(character_id: int, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Get character
    char_result = client.table('characters').select('*').eq('id', character_id).eq('user_id', user["id"]).execute()
    if not char_result.data:
        raise HTTPException(status_code=404, detail="Character not found")
    
    character = char_result.data[0]
    
    # Get inventory
    inv_result = client.table('inventory').select('*').eq('character_id', character_id).execute()
    
    # Get skills
    skills_result = client.table('skills').select('*').eq('character_id', character_id).execute()
    
    # Get quests
    quests_result = client.table('quests').select('*').eq('character_id', character_id).execute()
    
    # Get story progression
    story_result = client.table('story_progression').select('*').eq('character_id', character_id).execute()
    
    # Get codex
    codex_result = client.table('codex').select('*').eq('character_id', character_id).execute()
    
    return {
        "character": character,
        "inventory": inv_result.data,
        "skills": skills_result.data,
        "quests": quests_result.data,
        "story": story_result.data[0] if story_result.data else None,
        "codex": codex_result.data
    }

# ==================== Game Routes ====================

@app.post("/api/characters/{character_id}/move")
async def move_character(character_id: int, x: int, y: int, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Verify ownership
    char_result = client.table('characters').select('user_id, name').eq('id', character_id).execute()
    if not char_result.data or char_result.data[0]['user_id'] != user['id']:
        raise HTTPException(status_code=403, detail="Not your character")
    
    client.table('characters').update({
        'x_position': x,
        'y_position': y
    }).eq('id', character_id).execute()
    
    # Broadcast movement
    await manager.broadcast_to_room("global", {
        "type": "movement",
        "character_id": character_id,
        "name": char_result.data[0]['name'],
        "x": x,
        "y": y
    }, exclude_user=user["id"])
    
    return {"success": True}

@app.post("/api/characters/{character_id}/combat/{enemy_id}")
async def start_combat(character_id: int, enemy_id: str, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Get character
    char_result = client.table('characters').select('*').eq('id', character_id).eq('user_id', user["id"]).execute()
    
    if not char_result.data:
        raise HTTPException(status_code=404, detail="Character not found")
    
    char_data = char_result.data[0]
    
    # Create player object
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
        role=char_data.get('role', 'player'),
        skill_points=char_data["skill_points"]
    )
    
    # Get enemy
    enemy_data = ENEMIES.get(enemy_id)
    if not enemy_data:
        raise HTTPException(status_code=404, detail="Enemy not found")
    
    enemy = Enemy(
        id=enemy_id,
        name=enemy_data["name"],
        emoji=enemy_data["emoji"],
        level=enemy_data["level"],
        hp=enemy_data["hp"],
        max_hp=enemy_data["max_hp"],
        attack=enemy_data["attack"],
        defense=enemy_data["defense"],
        exp_reward=enemy_data["exp_reward"],
        gold_reward=enemy_data["gold_reward"],
        loot_table=enemy_data.get("loot_table", []),
        is_boss=enemy_data.get("is_boss", False)
    )
    
    combat_log = []
    victory = False
    
    # Combat loop
    while player.hp > 0 and enemy.hp > 0:
        # Player turn
        damage_result = await combat_engine.calculate_damage(player, enemy, None)
        combat_log.append({
            "turn": len(combat_log) + 1,
            "attacker": "player",
            **damage_result
        })
        
        if enemy.hp <= 0:
            victory = True
            break
        
        # Enemy turn
        enemy_attack = enemy.attack_target(player)
        combat_log.append({
            "turn": len(combat_log) + 1,
            "attacker": "enemy",
            "damage": enemy_attack["damage"],
            "message": enemy_attack["message"],
            "player_hp": player.hp
        })
    
    loot_items = []
    
    if victory:
        # Reward player
        player.gain_exp(enemy.exp_reward)
        player.gold += enemy.gold_reward
        
        # Check for loot
        for loot in enemy.loot_table:
            if random.random() < loot["chance"]:
                loot_items.append(loot["item_id"])
                
                # Check if item already exists in inventory
                inv_result = client.table('inventory').select('*').eq('character_id', character_id).eq('item_id', loot["item_id"]).execute()
                if inv_result.data:
                    # Update quantity
                    client.table('inventory').update({
                        'quantity': inv_result.data[0]['quantity'] + 1
                    }).eq('id', inv_result.data[0]['id']).execute()
                else:
                    # Insert new
                    client.table('inventory').insert({
                        'character_id': character_id,
                        'item_id': loot["item_id"],
                        'quantity': 1
                    }).execute()
        
        # Update character in database
        client.table('characters').update({
            'hp': player.hp,
            'mana': player.mana,
            'exp': player.exp,
            'gold': player.gold,
            'level': player.level
        }).eq('id', character_id).execute()
        
        # Update quest progress
        await quest_engine.update_quest_progress(character_id, "kill", {"target": enemy_id, "amount": 1})
        
        # Check for boss kill
        if enemy.is_boss:
            # Special boss kill handling
            await manager.broadcast_to_room("global", {
                "type": "system",
                "message": f"🏆 {player.name} has defeated {enemy.name}!"
            })
    
    return {
        "victory": victory,
        "player_hp": player.hp,
        "player_max_hp": player.max_hp,
        "enemy_hp": enemy.hp,
        "enemy_max_hp": enemy.max_hp,
        "exp_gained": enemy.exp_reward if victory else 0,
        "gold_gained": enemy.gold_reward if victory else 0,
        "loot": loot_items if victory else [],
        "combat_log": combat_log[-10:]
    }

@app.post("/api/characters/{character_id}/skill/{skill_id}")
async def use_skill(character_id: int, skill_id: str, target_type: str, target_id: str, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Get character
    char_result = client.table('characters').select('*').eq('id', character_id).eq('user_id', user["id"]).execute()
    
    if not char_result.data:
        raise HTTPException(status_code=404, detail="Character not found")
    
    char_data = char_result.data[0]
    
    # Create player object
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
        role=char_data.get('role', 'player'),
        skill_points=char_data["skill_points"]
    )
    
    # Get target
    if target_type == "enemy":
        enemy_data = ENEMIES.get(target_id)
        if not enemy_data:
            raise HTTPException(status_code=404, detail="Enemy not found")
        
        target = Enemy(
            id=target_id,
            name=enemy_data["name"],
            emoji=enemy_data["emoji"],
            level=enemy_data["level"],
            hp=enemy_data["hp"],
            max_hp=enemy_data["max_hp"],
            attack=enemy_data["attack"],
            defense=enemy_data["defense"],
            exp_reward=enemy_data["exp_reward"],
            gold_reward=enemy_data["gold_reward"],
            loot_table=enemy_data.get("loot_table", []),
            is_boss=enemy_data.get("is_boss", False)
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid target type")
    
    # Use skill
    result = player.use_skill(skill_id, target)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    # Apply damage to target
    target.take_damage(result["damage"])
    
    # Update character
    client.table('characters').update({
        'mana': player.mana
    }).eq('id', character_id).execute()
    
    return {
        "success": True,
        "damage": result["damage"],
        "critical": result["critical"],
        "skill_name": result["skill_name"],
        "target_hp": target.hp,
        "target_max_hp": target.max_hp
    }

# ==================== Inventory Routes ====================

@app.get("/api/characters/{character_id}/inventory")
async def get_inventory(character_id: int, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Verify ownership
    char_result = client.table('characters').select('user_id').eq('id', character_id).execute()
    if not char_result.data or char_result.data[0]['user_id'] != user['id']:
        raise HTTPException(status_code=403, detail="Not your character")
    
    result = client.table('inventory').select('*').eq('character_id', character_id).execute()
    
    return result.data

@app.post("/api/characters/{character_id}/inventory/use/{inventory_id}")
async def use_item(character_id: int, inventory_id: int, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Get item
    item_result = client.table('inventory').select('*, characters!inner(hp, max_hp, mana, max_mana)').eq('id', inventory_id).eq('characters.user_id', user["id"]).execute()
    
    if not item_result.data:
        raise HTTPException(status_code=404, detail="Item not found")
    
    item = item_result.data[0]
    item_data = ITEMS.get(item["item_id"])
    
    if not item_data:
        raise HTTPException(status_code=404, detail="Item data not found")
    
    effect = item_data.get("effect", {})
    
    # Apply effects
    if "heal" in effect:
        new_hp = min(item["characters"]["max_hp"], item["characters"]["hp"] + effect["heal"])
        client.table('characters').update({'hp': new_hp}).eq('id', character_id).execute()
    
    if "restore_mana" in effect:
        new_mana = min(item["characters"]["max_mana"], item["characters"]["mana"] + effect["restore_mana"])
        client.table('characters').update({'mana': new_mana}).eq('id', character_id).execute()
    
    # Remove used item
    if item["quantity"] > 1:
        client.table('inventory').update({'quantity': item["quantity"] - 1}).eq('id', inventory_id).execute()
    else:
        client.table('inventory').delete().eq('id', inventory_id).execute()
    
    return {"success": True, "effect": effect}

# ==================== Quest Routes ====================

@app.get("/api/quests")
async def get_quests(act: Optional[int] = None):
    if act:
        return [q for q in QUESTS.values() if q.get("act") == act]
    return list(QUESTS.values())

@app.post("/api/characters/{character_id}/quests/{quest_id}/start")
async def start_quest_route(character_id: int, quest_id: str, user=Depends(get_current_user)):
    # Verify ownership
    char_result = get_supabase_client().table('characters').select('user_id').eq('id', character_id).execute()
    if not char_result.data or char_result.data[0]['user_id'] != user['id']:
        raise HTTPException(status_code=403, detail="Not your character")
    
    result = await quest_engine.start_quest(character_id, quest_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result

@app.get("/api/characters/{character_id}/quests")
async def get_character_quests(character_id: int, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Verify ownership
    char_result = client.table('characters').select('user_id').eq('id', character_id).execute()
    if not char_result.data or char_result.data[0]['user_id'] != user['id']:
        raise HTTPException(status_code=403, detail="Not your character")
    
    result = client.table('quests').select('*').eq('character_id', character_id).order('started_at', desc=True).execute()
    
    return result.data

# ==================== Story Routes ====================

@app.post("/api/characters/{character_id}/story/choice")
async def make_story_choice(character_id: int, quest_id: str, choice_id: str, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Verify ownership
    char_check = client.table('characters').select('id').eq('id', character_id).eq('user_id', user["id"]).execute()
    if not char_check.data:
        raise HTTPException(status_code=403, detail="Not your character")
    
    quest = QUESTS.get(quest_id)
    if not quest:
        raise HTTPException(status_code=404, detail="Quest not found")
    
    choice = None
    for c in quest.get("choices", []):
        if c["id"] == choice_id:
            choice = c
            break
    
    if not choice:
        raise HTTPException(status_code=404, detail="Choice not found")
    
    # Get or create story progression
    story_result = client.table('story_progression').select('*').eq('character_id', character_id).execute()
    story = story_result.data[0] if story_result.data else None
    
    if story:
        choices_made = json.loads(story["choices_made"] or "[]")
        choices_made.append({
            "quest_id": quest_id,
            "choice_id": choice_id,
            "choice_text": choice["text"],
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        # Update reputation
        reputation = json.loads(story["reputation"] or "{}")
        for key, value in choice.get("reputation", {}).items():
            reputation[key] = reputation.get(key, 0) + value
        
        client.table('story_progression').update({
            'choices_made': json.dumps(choices_made),
            'reputation': json.dumps(reputation)
        }).eq('character_id', character_id).execute()
    else:
        choices_made = [{
            "quest_id": quest_id,
            "choice_id": choice_id,
            "choice_text": choice["text"],
            "timestamp": datetime.datetime.now().isoformat()
        }]
        reputation = choice.get("reputation", {})
        
        client.table('story_progression').insert({
            'character_id': character_id,
            'choices_made': json.dumps(choices_made),
            'reputation': json.dumps(reputation)
        }).execute()
    
    # If this is the final quest, handle ending
    if quest.get("is_final"):
        ending = choice.get("ending", "The adventure continues...")
        client.table('story_progression').update({
            'current_act': 6,
            'completed_acts': json.dumps(list(range(1, 6)))
        }).eq('character_id', character_id).execute()
        
        # Grant bonus rewards based on ending
        if choice_id == "ending_hero":
            client.table('characters').update({
                'gold': client.rpc('increment', {'x': 5000}),
                'exp': client.rpc('increment', {'x': 5000})
            }).eq('id', character_id).execute()
        elif choice_id == "ending_dark":
            client.table('characters').update({
                'gold': client.rpc('increment', {'x': 10000}),
                'strength': client.rpc('increment', {'x': 50})
            }).eq('id', character_id).execute()
        elif choice_id == "ending_balance":
            client.table('characters').update({
                'gold': client.rpc('increment', {'x': 3000}),
                'exp': client.rpc('increment', {'x': 3000}),
                'intelligence': client.rpc('increment', {'x': 30}),
                'vitality': client.rpc('increment', {'x': 30})
            }).eq('id', character_id).execute()
    
    return {
        "success": True,
        "choice_made": choice["text"],
        "ending": choice.get("ending") if quest.get("is_final") else None
    }

@app.get("/api/characters/{character_id}/story")
async def get_story_progress(character_id: int, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Verify ownership
    char_check = client.table('characters').select('id').eq('id', character_id).eq('user_id', user["id"]).execute()
    if not char_check.data:
        raise HTTPException(status_code=403, detail="Not your character")
    
    story_result = client.table('story_progression').select('*').eq('character_id', character_id).execute()
    
    # Get quests
    quests_result = client.table('quests').select('*').eq('character_id', character_id).execute()
    
    return {
        "story": story_result.data[0] if story_result.data else None,
        "active_quests": [q for q in quests_result.data if q["status"] == "in_progress"],
        "completed_quests": [q for q in quests_result.data if q["status"] == "completed"]
    }

# ==================== Skills Routes ====================

@app.get("/api/skills")
async def get_skills():
    return SKILLS

@app.get("/api/characters/{character_id}/skills")
async def get_character_skills(character_id: int, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Verify ownership
    char_check = client.table('characters').select('id').eq('id', character_id).eq('user_id', user["id"]).execute()
    if not char_check.data:
        raise HTTPException(status_code=403, detail="Not your character")
    
    result = client.table('skills').select('*').eq('character_id', character_id).execute()
    
    return result.data

@app.post("/api/characters/{character_id}/skills/learn/{skill_id}")
async def learn_skill(character_id: int, skill_id: str, user=Depends(get_current_user)):
    client = get_supabase_client()
    
    # Verify ownership
    char_check = client.table('characters').select('skill_points, level').eq('id', character_id).eq('user_id', user["id"]).execute()
    if not char_check.data:
        raise HTTPException(status_code=403, detail="Not your character")
    
    character = char_check.data[0]
    
    # Check if skill exists
    skill = SKILLS.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    # Check level requirement
    if character['level'] < skill['unlock_level']:
        raise HTTPException(status_code=400, detail=f"Need level {skill['unlock_level']} to learn this skill")
    
    # Check skill points
    if character['skill_points'] < 1:
        raise HTTPException(status_code=400, detail="Not enough skill points")
    
    # Check if already learned
    learned = client.table('skills').select('*').eq('character_id', character_id).eq('skill_id', skill_id).execute()
    if learned.data:
        raise HTTPException(status_code=400, detail="Skill already learned")
    
    # Learn skill
    client.table('skills').insert({
        'character_id': character_id,
        'skill_id': skill_id,
        'learned_at': datetime.datetime.now().isoformat()
    }).execute()
    
    # Deduct skill point
    client.table('characters').update({
        'skill_points': character['skill_points'] - 1
    }).eq('id', character_id).execute()
    
    return {"success": True, "skill": skill['name']}

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
        client = get_supabase_client()
        user_result = client.table('users').select('banned').eq('id', user_id).execute()
        
        if user_result.data and user_result.data[0].get("banned"):
            await websocket.close(code=1008)
            return
        
        if user_id in manager.banned_users:
            await websocket.close(code=1008)
            return
        
    except Exception:
        await websocket.close(code=1008)
        return
    
    await manager.connect(websocket, user_id, role)
    
    # Send welcome message
    role_emoji = "👑" if is_owner else "⚡" if role == "admin" else "🛡️" if role == "moderator" else ""
    await manager.send_personal_message(user_id, {
        "type": "system",
        "message": f"Connected to server {role_emoji}".strip()
    })
    
    # Send recent chat history
    for msg in manager.chat_history[-20:]:
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
                
                client = get_supabase_client()
                client.table('characters').update({
                    'x_position': x,
                    'y_position': y
                }).eq('id', character_id).eq('user_id', user_id).execute()
                
                # Get character name
                char_result = client.table('characters').select('name').eq('id', character_id).execute()
                char_name = char_result.data[0]['name'] if char_result.data else "Player"
                
                await manager.broadcast_to_room("global", {
                    "type": "movement",
                    "user_id": user_id,
                    "character_id": character_id,
                    "name": char_name,
                    "x": x,
                    "y": y
                }, exclude_user=user_id)
    
    except WebSocketDisconnect:
        manager.disconnect(user_id)

# ==================== Health Check ====================

@app.get("/", include_in_schema=True)
async def root():
    return {
        "status": "online",
        "service": "Pixel RPG API",
        "version": "3.0.0",
        "features": ["story_mode", "multiplayer", "skills", "quests", "inventory"],
        "timestamp": datetime.datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    # Check database connection
    db_status = "healthy"
    try:
        client = get_supabase_client()
        client.table('users').select('count', count='exact').limit(1).execute()
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
