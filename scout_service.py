"""
Scout Service: Discovers and evaluates micro-task opportunities across platforms.
Implements adaptive exploration with memory-based optimization.
"""

import asyncio
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
import logging
import random
from urllib.parse import urlparse
import time

import pandas as pd
import numpy as np
from playwright.async_api import async_playwright, Page, Browser
from sklearn.ensemble import RandomForestRegressor  # For opportunity scoring

from firebase_config import (
    get_firestore_client, TaskStatus, TaskDocument, AgentDocument, 
    AgentType, AgentStatus, firebase_manager
)

logger = logging.getLogger(__name__)

class OpportunityScorer:
    """Machine learning-based task scoring with adaptive learning"""
    
    def __init__(self):
        self.model = RandomForestRegressor(n_estimators=10, random_state=42)
        self.feature_names = [
            'reward_per_minute',
            'platform_trust_score',
            'task_complexity',
            'completion_time_variance',
            'historical_success_rate'
        ]
        self.is_trained = False
        self.training_data = []
        
    def extract_features(self, task_data: Dict[str, Any]) -> np.ndarray:
        """Extract numerical features from task data"""
        features = np.zeros(len(self.feature_names))
        
        # Reward per minute (normalized)
        reward = task_data.get('reward_usd', 0)
        time_minutes = max(task_data.get('estimated_time', 60) / 60, 1)
        features[0] = min(reward / time_minutes, 1.0)  # Cap at $1/min
        
        # Platform trust score (from memory)
        platform = task_data.get('platform', 'unknown')
        features[1] = task_data.get('platform_score', 0.5)
        
        # Task complexity (0-1)
        features[2] = task_data.get('complexity_score', 0.5)
        
        # Completion time variance (estimate)
        features[3] = 0.3  # Default moderate variance
        
        # Historical success rate
        features[4] = task_data.get('success_rate', 0.7)
        
        return features.reshape(1, -1)
    
    def score_opportunity(self, task_data: Dict[str, Any]) -> float:
        """Score opportunity from 0-100"""
        if not self.is_trained:
            # Use simple heuristic before training
            reward = task_data.get('reward_usd', 0)
            time_min = max(task_data.get('estimated_time', 60) / 60, 1)
            platform_score = task_data.get('platform_score', 0.5)
            
            base_score = (reward / time_min) * 100  # Dollars per minute * 100
            adjusted_score = base_score * platform_score
            
            return min(adjusted_score, 100.0)
        else:
            features = self.extract_features(task_data)
            score = self.model.predict(features)[0]
            return float(np.clip(score * 100, 0, 100))
    
    def update_model(self, task_id: str, actual_outcome: Dict[str, Any]):
        """Update model with actual task performance"""
        self.training_data.append({
            'task_id': task_id,
            'outcome': actual_outcome
        })
        
        if len(self.training_data) >= 10:
            self._retrain_model()

class PlatformDetector:
    """Dynamically identify and classify task platforms"""
    
    PLATFORM_PATTERNS = {
        'appen': [r'appen\.com', r'connect\.appen\.com'],
        'mturk': [r'mturk\.com', r'worker\.mturk\.com'],
        'clickworker': [r'clickworker\.com'],
        'microworkers': [r'microworkers\.com'],
        'testable': [r'testable\.org'],
        'prolific': [r'prolific\.co'],
        'usertesting': [r'usertesting\.com']
    }
    
    @staticmethod
    def detect_platform(url: str) -> Tuple[str, float]:
        """Detect platform with confidence score"""
        domain = urlparse(url).netloc.lower()
        
        for platform, patterns in PlatformDetector.PLATFORM_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, domain):
                    return platform, 0.95  # High confidence
        
        # Check for common micro-task indicators
        if any(word in domain for word in ['task', 'survey', 'test', 'work', 'job']):
            return 'generic', 0.5
        
        return 'unknown', 0.3

class ScoutService:
    """Main Scout service implementing exploration and discovery"""
    
    def __init__(self, scout_id: str = None):
        self.scout_id = scout_id or f"scout_{int(time.time())}"
        self.db = get_firestore_client()
        self.scorer = OpportunityScorer()
        self.platform_detector = PlatformDetector()
        self.is_running = False
        
        # Platform discovery configuration
        self.platform_urls = [
            "https://www.appen.com/",
            "https://www.mturk.com/",
            "https://www.clickworker.com/",
            "https://www.microworkers.com/"
        ]
        
        # Initialize agent in Firestore
        self._register_agent()
    
    def _register_agent(self):
        """Register this scout agent in Firestore"""
        agent_ref = self.db.collection("agents").document(self.scout_id)
        
        agent_data = AgentDocument(
            agent_id=self.scout_id,
            agent_type=AgentType.SCOUT,
            status=AgentStatus.INITIALIZING,
            configuration={
                "platform_urls": self.platform_urls,
                "discovery_interval": 300,  # 5 minutes
                "max_tasks_per_cycle": 10
            }
        )
        
        agent_ref.set(agent_data.to_dict())
        logger.info(f"Scout agent {self.scout_id} registered")
    
    def _update_heartbeat(self):
        """Update agent heartbeat in Firestore"""
        try:
            self.db.collection("agents").document(self.scout_id).update({
                "last_heartbeat": datetime.now(timezone.utc),
                "status": AgentStatus.ACTIVE.value if self.is_running else AgentStatus.PAUSED.value
            })
        except Exception as e:
            logger.error(f"Heartbeat update failed: {e}")
    
    async def _detect_captcha_challenges(self, page: Page) -> bool:
        """Detect CAPTCHA presence with image analysis"""
        try:
            # Look for common CAPTCHA elements
            captcha_selectors = [
                'iframe[src*="recaptcha"]',
                'div.g-recaptcha',
                'img[alt*="CAPTCHA"]',
                'input[name*="captcha"]',
                '[aria-label*="captcha"]'
            ]
            
            for selector in captcha_selectors:
                elements = await page.query_selector_all(selector)
                if elements and len(elements) > 0:
                    logger.warning(f"CAPTCHA detected with selector: {selector}")
                    return True
            
            # Check for CAPTCHA-like images (basic detection)
            images = await page.query_selector_all('img')
            for img in images[:10]:  # Limit to first 10 images
                src = await img.get_attribute('src')
                if src and ('captcha' in src.lower() or 'security' in src.lower()):
                    return True
            
            return False
            
        except Exception as e:
            logger.warning(f"CAPTCHA detection error: {e}")
            return False
    
    async def _scan_platform(self, page: Page, platform_url: str) -> List[Dict]:
        """Scan a specific platform for micro-tasks"""
        tasks_found = []
        
        try:
            logger.info(f"Scanning platform: {platform_url}")
            await page.goto(platform_url, timeout=30000)
            
            # Wait for page to load
            await page.wait_for_load_state('networkidle')
            
            # Check for CAPTCHA
            has_captcha = await self._detect_captcha_challenges(page)
            if has_captcha:
                logger.warning(f"CAPTCHA detected