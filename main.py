import os
import asyncio
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from discord import app_commands, Intents, Client, Embed, Color, Interaction
from discord.app_commands import Choice
from websockets import serve
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv
import json
import re

# Load environment variables
load_dotenv()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
DATABASE_URL = os.getenv("DATABASE_URL")

# Initialize FastAPI
app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the static files directory
app.mount("/public", StaticFiles(directory="public"), name="public")

# Database setup
engine = create_engine(DATABASE_URL)
from database import SessionLocal, Base  # Import from database.py
from models import DBCharacter  # Import from models.py

Base.metadata.create_all(bind=engine)

# Initialize Discord bot
intents = Intents.default()
intents.message_content = True
client = Client(intents=intents)
tree = app_commands.CommandTree(client)

# Global websocket connections
websocket_connections = set()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pydantic model for request validation
class Character(BaseModel):
    name: str
    faceclaim: str
    image: HttpUrl
    bio: str
    password: str

# Helper functions
def verify_character(name: str, password: str) -> bool:
    db = SessionLocal()
    try:
        character = db.query(DBCharacter).filter(DBCharacter.name == name).first()
        return character and (character.password == password or password == ADMIN_PASSWORD)
    finally:
        db.close()

def is_valid_image_url(url: str) -> bool:
    if not url:
        return False
    pattern = re.compile(r'^https://.*\.(jpg|jpeg|png)$', re.IGNORECASE)
    return bool(pattern.match(url)) and len(url) <= 2048

async def broadcast_message(message: dict):
    if not websocket_connections:
        return
    websocket_message = json.dumps(message)
    await asyncio.gather(*[ws.send(websocket_message) for ws in websocket_connections])

# Discord bot commands
@tree.command(name="create_character", description="Creates a new character profile")
async def create_character(interaction: Interaction, name: str, faceclaim: str, image: str, bio: str, password: str):
    try:
        if not is_valid_image_url(image):
            await interaction.response.send_message("❌ Invalid image URL. Please provide an HTTPS URL ending with .jpg, .jpeg, or .png.", ephemeral=True)
            return

        db = SessionLocal()
        try:
            character = DBCharacter(name=name, faceclaim=faceclaim, image=image, bio=bio, password=password)
            db.add(character)
            db.commit()
            await interaction.response.send_message(f"✓ Character '{name}' has been created successfully!")
            await broadcast_message({'action': 'create', 'name': name, 'faceclaim': faceclaim, 'image': image, 'bio': bio})
        except IntegrityError:
            await interaction.response.send_message(f"❌ A character named '{name}' already exists!", ephemeral=True)
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in create_character: {e}")

# Autocomplete function for character names
async def character_name_autocomplete(interaction: Interaction, current: str):
    db = SessionLocal()
    try:
        characters = db.query(DBCharacter).filter(DBCharacter.name.ilike(f"{current}%")).all()
        return [Choice(name=character.name, value=character.name) for character in characters[:5]]
    finally:
        db.close()

@tree.command(name="edit_character", description="Edits an existing character")
@app_commands.autocomplete(name=character_name_autocomplete)
async def edit_character(interaction: Interaction, name: str, password: str, faceclaim: Optional[str] = None, image: Optional[str] = None, bio: Optional[str] = None):
    try:
        if not verify_character(name, password):
            await interaction.response.send_message("❌ Invalid character name or password.", ephemeral=True)
            return

        if image and not is_valid_image_url(image):
            await interaction.response.send_message("❌ Invalid image URL.", ephemeral=True)
            return

        db = SessionLocal()
        try:
            character = db.query(DBCharacter).filter(DBCharacter.name == name).first()
            if not character:
                await interaction.response.send_message("❌ Character not found.", ephemeral=True)
                return
            
            if faceclaim:
                character.faceclaim = faceclaim
            if image:
                character.image = image
            if bio:
                character.bio = bio
            db.commit()
            await interaction.response.send_message(f"✓ Character '{name}' has been updated!")
            await broadcast_message({'action': 'edit', 'name': name})
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in edit_character: {e}")

@tree.command(name="delete_character", description="Deletes a character")
@app_commands.autocomplete(name=character_name_autocomplete)
async def delete_character(interaction: Interaction, name: str, password: str):
    try:
        if not verify_character(name, password):
            await interaction.response.send_message("❌ Invalid character name or password.", ephemeral=True)
            return

        db = SessionLocal()
        try:
            character = db.query(DBCharacter).filter(DBCharacter.name == name).first()
            if not character:
                await interaction.response.send_message("❌ Character not found.", ephemeral=True)
                return

            db.delete(character)
            db.commit()
            await interaction.response.send_message(f"✓ Character '{name}' has been deleted!")
            await broadcast_message({'action': 'delete', 'name': name})
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in delete_character: {e}")

@tree.command(name="show_character", description="Shows a character's profile")
@app_commands.autocomplete(name=character_name_autocomplete)
async def show_character(interaction: Interaction, name: str):
    try:
        db = SessionLocal()
        try:
            character = db.query(DBCharacter).filter(DBCharacter.name == name).first()
            if not character:
                await interaction.response.send_message("❌ Character not found.", ephemeral=True)
                return
            embed = Embed(
                title=character.name.upper(),
                description=f"[Character Sheet]({character.bio})" if character.bio.startswith('http') else "N/A",
                color=Color.from_str("#fffdd0")
            )
            embed.set_image(url=character.image)
            embed.set_footer(text=character.faceclaim)
            await interaction.response.send_message(embed=embed)
        finally:
            db.close()
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in show_character: {e}")

@tree.command(name="character_list", description="Shows the list of all characters")
async def list_all_characters(interaction: Interaction):
    try:
        website_url = "https://shield-hzo0.onrender.com"  # Updated path
        await interaction.response.send_message(f"📚 View the complete character list [here]({website_url})")
    except Exception as e:
        await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
        logging.error(f"Error in list_all_characters: {e}")

@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    try:
        logger.info("Serving index.html")
        return FileResponse("public/index.html")
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return HTMLResponse(content="Error serving index.html", status_code=500)

@app.exception_handler(Exception)
async def _handler(request: Request, exc: Exception):
    return HTMLResponse(content="404 Not Found", status_code=404)

# WebSocket handling
async def websocket_handler(websocket):
    await websocket.accept()
    websocket_connections.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # Receive messages if needed
    except Exception as e:
        logging.error(f"WebSocket connection error: {e}")
    finally:
        websocket_connections.remove(websocket)

@app.websocket("/ws")
async def websocket_endpoint(websocket):
    await websocket_handler(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
