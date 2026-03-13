"""
Firebase Firestore configuration and schema initialization.
Central state management for the Curiosity Colony with proper type hinting.
"""

import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import logging
from dataclasses import dataclass, asdict, field
from enum import Enum

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import Client as FirestoreClient

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TaskStatus(str, Enum):
    """Task lifecycle states with clear transitions"""
    AVAILABLE = "available"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"

class AgentType(str, Enum):
    """Agent specialization types"""
    SCOUT = "scout"
    WORKER = "worker"
    TREASURER = "treasurer"

class AgentStatus(str, Enum):
    """Agent operational states"""
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    INITIALIZING = "initializing"

@dataclass
class TaskDocument:
    """Schema for task documents with validation"""
    task_id: str
    platform: str
    url: str
    reward_usd: float
    estimated_time: int  # seconds
    complexity_score: float  # 0.0 to 1.0
    status: TaskStatus
    claimed_by: Optional[str] = None
    checkpoint: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to Firestore-compatible dictionary"""
        data = asdict(self)
        data['status'] = self.status.value
        data['created_at'] = self.created_at
        data['updated_at'] = self.updated_at
        return data

@dataclass
class AgentDocument:
    """Schema for agent state and performance tracking"""
    agent_id: str
    agent_type: AgentType
    status: AgentStatus
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    performance_metrics: Dict[str, Any] = field(default_factory=lambda: {
        "tasks_completed": 0,
        "total_earned_usd": 0.0,
        "success_rate": 0.0,
        "uptime_seconds": 0,
        "failures": 0
    })
    strategy_memory: Dict[str, Any] = field(default_factory=lambda: {
        "platform_success_rates": {},
        "avoid_patterns": [],
        "optimal_times": {}
    })
    configuration: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to Firestore-compatible dictionary"""
        data = asdict(self)
        data['agent_type'] = self.agent_type.value
        data['status'] = self.status.value
        data['last_heartbeat'] = self.last_heartbeat
        return data

class FirebaseManager:
    """Singleton manager for Firebase Firestore operations with connection pooling"""
    
    _instance = None
    _db: Optional[FirestoreClient] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FirebaseManager, cls).__new__(cls)
        return cls._instance
    
    def initialize(self, credential_path: Optional[str] = None) -> FirestoreClient:
        """Initialize Firebase Admin SDK with multiple credential strategies"""
        if self._db is not None:
            return self._db
            
        try:
            # Strategy 1: Use environment variable
            if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                cred = credentials.ApplicationDefault()
                logger.info("Using Application Default Credentials")
            
            # Strategy 2: Use provided credential file
            elif credential_path and os.path.exists(credential_path):
                cred = credentials.Certificate(credential_path)
                logger.info(f"Using credential file: {credential_path}")
            
            # Strategy 3: Try to find in default locations
            else:
                default_locations = [
                    "serviceAccountKey.json",
                    "./config/serviceAccountKey.json",
                    "../config/serviceAccountKey.json"
                ]
                
                for location in default_locations:
                    if os.path.exists(location):
                        cred = credentials.Certificate(location)
                        logger.info(f"Found credentials at: {location}")
                        break
                else:
                    raise FileNotFoundError("No Firebase credentials found. Please set GOOGLE_APPLICATION_CREDENTIALS or provide credential file.")
            
            # Initialize Firebase app if not already initialized
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            
            self._db = firestore.client()
            logger.info("Firebase Firestore initialized successfully")
            self._initialize_collections()
            return self._db
            
        except Exception as e:
            logger.error(f"Failed to initialize Firebase: {str(e)}")
            raise
    
    def _initialize_collections(self):
        """Initialize required collections with proper indexes and validation rules"""
        collections = ["tasks", "agents", "events", "config"]
        
        # Note: Firestore creates collections implicitly, but we can set up validation rules here
        # In production, you would set up Firestore security rules
        
        # Create index configuration documents if they don't exist
        config_ref = self._db.collection("config").document("indexes")
        
        if not config_ref.get().exists:
            config_ref.set({
                "created_at": datetime.now(timezone.utc),
                "collections": collections,
                "version": "1.0.0"
            })
            logger.info("Firestore collections initialized")
    
    def get_database(self) -> FirestoreClient:
        """Get database instance with lazy initialization"""
        if self._db is None:
            raise RuntimeError("Firebase not initialized. Call initialize() first.")
        return self._db
    
    def close(self):
        """Cleanup resources (Firestore client is stateless, no close needed)"""
        self._db = None
        logger.info("Firebase manager cleaned up")

# Singleton instance
firebase_manager = FirebaseManager()

def get_firestore_client() -> FirestoreClient:
    """Helper function to get Firestore client with initialization"""
    return firebase_manager.get_database()