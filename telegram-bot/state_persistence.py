"""
MongoDB-based state persistence for the bot.
"""

import os
import logging
from typing import Optional
from datetime import datetime
from pymongo import MongoClient

# Configure logging
logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongodb:27017/")
DB_NAME = "telegram_bot"
COLLECTION_NAME = "bot_state"


class StatePersistence:
    """Handle MongoDB state persistence."""

    def __init__(self):
        """Initialize MongoDB connection."""
        try:
            self.client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            self.db = self.client[DB_NAME]
            self.collection = self.db[COLLECTION_NAME]
            # Check connection
            self.client.server_info()
            logger.info(f"Connected to MongoDB at {MONGO_URI}")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            self.client = None

    async def load_state(self, user_id: str) -> Optional[dict]:
        """Load user state from MongoDB."""
        if self.client is None:
            return None
            
        try:
            doc = self.collection.find_one({"user_id": user_id})
            if doc:
                # Remove MongoDB _id field
                doc.pop("_id", None)
                return doc
        except Exception as e:
            logger.error(f"Error loading state for user {user_id}: {e}")
            
        return None

    async def save_state(self, user_id: str, state: dict):
        """Save user state to MongoDB."""
        if self.client is None:
            return
            
        try:
            # Prepare state for saving
            save_data = state.copy()
            save_data["user_id"] = user_id
            save_data["last_updated"] = datetime.now()

            # Upsert: update if exists, insert if not
            self.collection.update_one(
                {"user_id": user_id},
                {"$set": save_data},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error saving state for user {user_id}: {e}")

    async def get_persona_history(self, user_id: str, persona: str) -> list:
        """Get conversation history for a specific persona."""
        if self.client is None:
            return []
            
        try:
            doc = self.collection.find_one({"user_id": user_id})
            if doc and "persona_histories" in doc:
                return doc["persona_histories"].get(persona, [])
        except Exception as e:
            logger.error(f"Error getting persona history for user {user_id}: {e}")
            
        return []

    async def save_persona_history(self, user_id: str, persona: str, messages: list):
        """Save conversation history for a specific persona."""
        if self.client is None:
            return
            
        try:
            # Update specific persona history in the document
            self.collection.update_one(
                {"user_id": user_id},
                {"$set": {f"persona_histories.{persona}": messages}},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error saving persona history for user {user_id}: {e}")


# Global instance
state_persistence = StatePersistence()
